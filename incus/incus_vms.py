"""Create/destroy Incus VMs for a k8s lab (plain subprocess, no pyinfra).

VM specs come from config.py (single source of truth). Creation runs in a
thread pool; select a subset via positional args or the INCUS_VMS env var.

Run:
    uv run incus/incus_vms.py                          # create all (parallel)
    uv run incus/incus_vms.py k8s-master k8s-worker-1
    INCUS_VMS=k8s-master uv run incus/incus_vms.py
    uv run incus/incus_vms.py --destroy                # destroy all
    uv run incus/incus_vms.py --destroy k8s-master
    uv run incus/incus_vms.py --destroy k8s-repo --purge-repos-data  # also wipe mirror data
    uv run incus/incus_vms.py --parallel 8

The k8s-repo mirror VM gets a persistent incus volume (k8s-repo-repos) mounted
at /var/www/repos; --destroy keeps it so rebuilding the VM does not re-download
the mirror. Use --purge-repos-data to delete it explicitly.
"""

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # this dir (for _incus)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))

import _alma_repos
from _incus import instance_exists, run

import config

VMS = config.VMS
DEFAULT_PARALLEL = 4

# The mirror VM's data lives on a persistent incus custom volume mounted at
# /var/www/repos, so rebuilding k8s-repo does not re-download the mirror (the
# repo-sync script is incremental for k8s/docker and for every full Alma repo).
# The volume is created/attached automatically and is NOT deleted by --destroy
# (use --purge-repos-data to wipe it).
REPOS_VOLUME = "k8s-repo-repos"
REPOS_MOUNT_PATH = "/var/www/repos"


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
    parser.add_argument(
        "--purge-repos-data",
        action="store_true",
        help="with --destroy: also delete the persistent k8s-repo repos volume",
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


def repo_volume_exists(settings: Settings) -> bool:
    out = run(
        [
            "incus",
            "storage",
            "volume",
            "list",
            settings.storage_pool,
            "--format=compact",
        ],
        check=False,
        capture_output=True,
    ).stdout
    return REPOS_VOLUME in out


def ensure_repo_volume(settings: Settings) -> None:
    """Create the persistent repos volume if it does not exist yet."""
    if repo_volume_exists(settings):
        return
    run(["incus", "storage", "volume", "create", settings.storage_pool, REPOS_VOLUME])
    print(f"[data] created persistent repos volume {REPOS_VOLUME}")


def attach_repo_volume(name: str, settings: Settings) -> None:
    """Attach the persistent repos volume to the mirror VM at /var/www/repos."""
    ensure_repo_volume(settings)
    if not any(
        d == "repos"
        for d in run(
            ["incus", "config", "device", "list", name],
            check=False,
            capture_output=True,
        ).stdout.split()
    ):
        run(
            [
                "incus",
                "config",
                "device",
                "add",
                name,
                "repos",
                "disk",
                f"pool={settings.storage_pool}",
                f"source={REPOS_VOLUME}",
                f"path={REPOS_MOUNT_PATH}",
            ]
        )
        print(f"[data] attached {REPOS_VOLUME} -> {REPOS_MOUNT_PATH} on {name}")


def purge_repo_volume(settings: Settings) -> None:
    """Delete the persistent repos volume (wipes all mirrored data)."""
    if not repo_volume_exists(settings):
        return
    run(["incus", "storage", "volume", "delete", settings.storage_pool, REPOS_VOLUME])
    print(f"[data] purged persistent repos volume {REPOS_VOLUME}")


def user_data(name: str, settings: Settings) -> str:
    hosts = "\n".join(f"      {s['ip']} {n}" for n, s in VMS.items())
    with open(settings.ssh_pub_key) as f:
        key = f.read().strip()
    hosts_file = f"      127.0.0.1   localhost\n{hosts}"
    # Alma repos are fully managed: cloud-init writes the rendered templates
    # (see deploy/_alma_repos.py) at the NJU upstream. Role per VM: the mirror
    # VM (k8s-repo) reproduces the stock enable-all state, and k8s nodes enable
    # the same full set (the internal mirror now serves every repo).
    # deploy/repo.py and deploy/prepare.py later push the same render under
    # their own role.
    consumer = "mirror" if name == config.REPO_MIRROR_HOSTNAME else "node"
    repo_files = "\n".join(
        f"  - path: /etc/yum.repos.d/{dest}\n"
        "    content: |\n"
        + "\n".join(
            f"      {line}" for line in _alma_repos.render_alma_repo(
                src, dest, config.ALMA_UPSTREAM_BASE, consumer=consumer
            ).splitlines()
        )
        for dest, src in sorted(_alma_repos.alma_repo_templates().items())
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
{repo_files}
runcmd:
  - dnf clean all
  - dnf -y upgrade
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
    if name == config.REPO_MIRROR_HOSTNAME:
        attach_repo_volume(name, settings)
    run(["incus", "start", name])
    run(["incus", "wait", name, "agent"], check=False)


def destroy_vm(name: str, *, purge_data: bool, settings: Settings) -> None:
    if not instance_exists(name):
        print(f"[skip] {name} does not exist")
        return
    run(["incus", "delete", "--force", name])
    print(f"[done] {name} destroyed")
    if purge_data and name == config.REPO_MIRROR_HOSTNAME:
        purge_repo_volume(settings)


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
            (lambda n: destroy_vm(n, purge_data=args.purge_repos_data, settings=settings))
            if args.destroy
            else (lambda n: create_vm(n, settings=settings))
        ),
        args.parallel,
    )


if __name__ == "__main__":
    settings, args = parse_args()
    main(settings, args)
