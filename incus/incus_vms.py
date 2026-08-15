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
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

IMAGE = os.environ.get("INCUS_IMAGE", "images:almalinux/10/cloud")
NETWORK = os.environ.get("INCUS_NETWORK", "incusbr0")
USER = os.environ.get("INCUS_USER", config.SSH_USER)
SSH_PUB_KEY = os.environ.get(
    "INCUS_SSH_KEY", os.path.expanduser("~/.ssh/id_ed25519.pub")
)
VM_SELECT = [x.strip() for x in os.environ.get("INCUS_VMS", "").split(",") if x.strip()]
LAB_PASSWORD_HASH = os.environ.get(
    "INCUS_PASSWORD_HASH",
    "$y$j9T$TMrD0j/ZK8z8F60V05ofg/$XSzPAyAlm5HnFtT.Qu5VZNXXFes8yURGL1.RBGfsQt/",
)

VMS = config.VMS
DEFAULT_PARALLEL = 4


def run(cmd: list[str], *, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, check=check, **kwargs)


def vm_exists(name: str) -> bool:
    out = run(
        ["incus", "list", "--format=compact", name],
        check=False,
        capture_output=True,
    ).stdout
    return bool(out) and name in out


def user_data(name: str) -> str:
    hosts = "\n".join(f"      {s['ip']} {n}" for n, s in VMS.items())
    with open(SSH_PUB_KEY) as f:
        key = f.read().strip()
    hosts_file = f"      127.0.0.1   localhost\n{hosts}"
    return f"""#cloud-config
hostname: {name}
users:
  - name: {USER}
    groups: [wheel]
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: false
    passwd: {LAB_PASSWORD_HASH}
    ssh_authorized_keys:
      - {key}
ssh_pwauth: false
write_files:
  - path: /etc/hosts
    content: |
{hosts_file}
runcmd:
  - sed -i -e 's|^mirrorlist=|# mirrorlist=|' -e 's|^# baseurl=https://repo.almalinux.org/almalinux/|baseurl={config.ALMA_UPSTREAM_BASE}/|' /etc/yum.repos.d/almalinux-*.repo
  - dnf clean all
  - dnf -y upgrade
  - dnf -y install rsync openssh-server
  - systemctl enable --now sshd
  - sh -c 'grep -q "^Subsystem sftp" /etc/ssh/sshd_config || echo "Subsystem sftp /usr/libexec/openssh/sftp-server" >> /etc/ssh/sshd_config'
  - systemctl restart sshd
"""


def create_vm(name: str) -> None:
    if vm_exists(name):
        print(f"[skip] {name} already exists")
        return
    spec = VMS[name]
    commands = [
        ["incus", "init", IMAGE, name, "--vm", f"--device=root,size={spec['disk']}"],
        ["incus", "config", "set", name, f"limits.cpu={spec['vcpu']}"],
        ["incus", "config", "set", name, f"limits.memory={spec['memory']}"],
    ]
    for cmd in commands:
        run(cmd)
    run(
        ["incus", "config", "set", name, "cloud-init.user-data=-"],
        input=user_data(name),
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
            f"network={NETWORK}",
            f"ipv4.address={spec['ip']}",
        ]
    )
    run(["incus", "start", name])
    run(["incus", "wait", name, "agent"], check=False)


def destroy_vm(name: str) -> None:
    if not vm_exists(name):
        print(f"[skip] {name} does not exist")
        return
    run(["incus", "delete", "--force", name])
    print(f"[done] {name} destroyed")


def run_parallel(names: set[str], fn, parallel: int) -> None:
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        for _ in pool.map(fn, names):
            pass
    print(f"[done] processed {len(names)} VM(s)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create/destroy Incus VMs for the k8s lab."
    )
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

    provided = {*args.vms, *VM_SELECT}
    if provided:
        unknown = [n for n in provided if n not in VMS]
        if unknown:
            sys.exit(f"error: unknown VM name(s): {', '.join(sorted(unknown))}")
        selected = provided
    else:
        selected = set(VMS)
    run_parallel(selected, destroy_vm if args.destroy else create_vm, args.parallel)


main()
