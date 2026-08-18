"""Create/destroy Incus VMs for a k8s lab (plain subprocess, no pyinfra).

VM specs come from config.py (single source of truth). Creation runs in a
thread pool; select a subset via positional args or the INCUS_VMS env var.

Run:
    uv run incus/incus_vms.py                          # create all (parallel)
    uv run incus/incus_vms.py k8s-master k8s-worker-1
    INCUS_VMS=k8s-master uv run incus/incus_vms.py
    uv run incus/incus_vms.py --destroy                # destroy all
    uv run incus/incus_vms.py --destroy k8s-master
    uv run incus/incus_vms.py --parallel 8
"""

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # this dir (for _incus)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _incus import instance_exists, run

import config

VMS = config.VMS
DEFAULT_PARALLEL = 4


@dataclass(frozen=True)
class Settings:
    """Environment-tunable VM creation parameters, parsed in parse_args().

    Values default exactly like the env-overridable constants they replace, so
    behavior is unchanged; keeping them out of module scope makes the module
    importable and the settings injectable for tests.
    """

    image: str
    network: str
    user: str
    ssh_pub_key: str
    password_hash: str
    storage_pool: str


def parse_args() -> tuple[Settings, argparse.Namespace]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vms", nargs="*", help="VM names; default all from config.VMS")
    parser.add_argument(
        "--destroy",
        action="store_true",
        help="delete the selected VMs instead of creating",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=int(os.environ.get("INCUS_PARALLEL", DEFAULT_PARALLEL)),
        help=f"max worker threads (default {DEFAULT_PARALLEL})",
    )
    args = parser.parse_args()

    settings = Settings(
        image=os.environ.get("INCUS_IMAGE", "images:almalinux/10/cloud"),
        network=os.environ.get("INCUS_NETWORK", "incusbr0"),
        user=os.environ.get("INCUS_USER", config.SSH_USER),
        ssh_pub_key=os.environ.get(
            "INCUS_SSH_KEY", os.path.expanduser("~/.ssh/id_ed25519.pub")
        ),
        password_hash=os.environ.get(
            "INCUS_PASSWORD_HASH",
            "$y$j9T$TMrD0j/ZK8z8F60V05ofg/$XSzPAyAlm5HnFtT.Qu5VZNXXFes8yURGL1.RBGfsQt/",
        ),
        storage_pool=os.environ.get("INCUS_STORAGE_POOL", "default"),
    )
    return settings, args


def user_data(name: str, settings: Settings) -> str:
    hosts = "\n".join(f"      {s['ip']} {n}" for n, s in VMS.items())
    with open(settings.ssh_pub_key) as f:
        key = f.read().strip()
    hosts_file = f"      127.0.0.1   localhost\n{hosts}"
    # Repos are configured by the same rule everywhere: for the test env
    # "the intranet mirror IS the upstream", so alma repos are pointed at NJU
    # (cancel mirrorlist, use baseurl) BEFORE the first dnf so even the
    # cloud-init package install never touches the internet mirrorlist.
    # deploy/k8s_prepare.py later converges the identical result via tasks/alma_repos.py.
    alma_base = config.ALMA_UPSTREAM_BASE
    alma_sed = (
        "sed -i 's/^mirrorlist=/# mirrorlist=/; "
        f"s|^#\\? *baseurl=https://repo.almalinux.org/almalinux|baseurl={alma_base}|' "
        "/etc/yum.repos.d/almalinux*.repo"
    )
    return f"""#cloud-config
hostname: {name}
timezone: Asia/Shanghai
users:
  - name: {settings.user}
    groups: [wheel]
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: false
    passwd: {settings.password_hash}
    ssh_authorized_keys:
      - {key}
ssh_pwauth: false
write_files:
  - path: /etc/hosts
    content: |
{hosts_file}
runcmd:
  - {alma_sed}
  - dnf clean all
  - dnf -y install rsync openssh-server
  - systemctl enable --now sshd
  - sh -c 'grep -q "^Subsystem sftp" /etc/ssh/sshd_config || echo "Subsystem sftp /usr/libexec/openssh/sftp-server" >> /etc/ssh/sshd_config'
  - systemctl restart sshd
"""


def create_vm(name: str, settings: Settings) -> None:
    if instance_exists(name):
        print(f"[skip] {name} already exists")
        return
    spec = VMS[name]
    commands = [
        ["incus", "init", settings.image, name, "--vm", f"--device=root,size={spec['disk']}"],
        ["incus", "config", "set", name, f"limits.cpu={spec['vcpu']}"],
        ["incus", "config", "set", name, f"limits.memory={spec['memory']}"],
    ]
    for cmd in commands:
        run(cmd)
    run(
        ["incus", "config", "set", name, "cloud-init.user-data=-"],
        input=user_data(name, settings),
    )
    run(
        [
            "incus",
            "config",
            "device",
            "add",
            name,
            "agent",
            "disk",
            "source=agent:config",
        ]
    )
    run(
        [
            "incus",
            "config",
            "device",
            "override",
            name,
            "eth0",
            f"network={settings.network}",
            f"ipv4.address={spec['ip']}",
        ]
    )
    run(["incus", "start", name])
    run(["incus", "wait", name, "agent"], check=False)


def destroy_vm(name: str, *, settings: Settings) -> None:
    if not instance_exists(name):
        print(f"[skip] {name} does not exist")
        return
    run(["incus", "delete", "--force", name])
    print(f"[done] {name} destroyed")


def run_parallel(names: set[str], fn, parallel: int) -> None:
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        for _ in pool.map(fn, names):
            pass
    print(f"[done] processed {len(names)} VM(s)")


def main(settings: Settings, args: argparse.Namespace) -> None:
    select_env = [x.strip() for x in os.environ.get("INCUS_VMS", "").split(",") if x.strip()]
    provided = {*args.vms, *select_env}
    if provided:
        unknown = [n for n in provided if n not in VMS]
        if unknown:
            sys.exit(f"error: unknown VM name(s): {', '.join(sorted(unknown))}")
        selected = provided
    else:
        selected = set(VMS)
    run_parallel(
        selected,
        (
            (lambda n: destroy_vm(n, settings=settings))
            if args.destroy
            else (lambda n: create_vm(n, settings=settings))
        ),
        args.parallel,
    )


if __name__ == "__main__":
    settings, args = parse_args()
    main(settings, args)
