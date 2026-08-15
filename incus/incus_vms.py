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
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))

import _alma_repos

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

# The mirror VM's data lives on a persistent incus custom volume mounted at
# /var/www/repos, so rebuilding k8s-repo does not re-download the mirror (the
# repo-sync script is incremental for k8s/docker, and its Alma closure sync now
# downloads only missing rpms). The volume is created/attached automatically
# and is NOT deleted by --destroy (use --purge-repos-data to wipe it).
REPOS_VOLUME = "k8s-repo-repos"
REPOS_MOUNT_PATH = "/var/www/repos"
STORAGE_POOL = os.environ.get("INCUS_STORAGE_POOL", "default")


def run(cmd: list[str], *, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, check=check, **kwargs)


def vm_exists(name: str) -> bool:
    out = run(
        ["incus", "list", "--format=compact", name],
        check=False,
        capture_output=True,
    ).stdout
    return bool(out) and name in out


def repo_volume_exists() -> bool:
    out = run(
        ["incus", "storage", "volume", "list", STORAGE_POOL, "--format=compact"],
        check=False,
        capture_output=True,
    ).stdout
    return REPOS_VOLUME in out


def ensure_repo_volume() -> None:
    """Create the persistent repos volume if it does not exist yet."""
    if repo_volume_exists():
        return
    run(["incus", "storage", "volume", "create", STORAGE_POOL, REPOS_VOLUME])
    print(f"[data] created persistent repos volume {REPOS_VOLUME}")


def attach_repo_volume(name: str) -> None:
    """Attach the persistent repos volume to the mirror VM at /var/www/repos."""
    ensure_repo_volume()
    if not any(d == "repos" for d in run(
        ["incus", "config", "device", "list", name],
        check=False,
        capture_output=True,
    ).stdout.split()):
        run(
            [
                "incus",
                "config",
                "device",
                "add",
                name,
                "repos",
                "disk",
                f"pool={STORAGE_POOL}",
                f"source={REPOS_VOLUME}",
                f"path={REPOS_MOUNT_PATH}",
            ]
        )
        print(f"[data] attached {REPOS_VOLUME} -> {REPOS_MOUNT_PATH} on {name}")


def purge_repo_volume() -> None:
    """Delete the persistent repos volume (wipes all mirrored data)."""
    if not repo_volume_exists():
        return
    run(["incus", "storage", "volume", "delete", STORAGE_POOL, REPOS_VOLUME])
    print(f"[data] purged persistent repos volume {REPOS_VOLUME}")


def user_data(name: str) -> str:
    hosts = "\n".join(f"      {s['ip']} {n}" for n, s in VMS.items())
    with open(SSH_PUB_KEY) as f:
        key = f.read().strip()
    hosts_file = f"      127.0.0.1   localhost\n{hosts}"
    # Alma repos are fully managed: cloud-init writes the rendered templates
    # (see deploy/_alma_repos.py) at the NJU upstream. Role per VM: the mirror
    # VM (k8s-repo) reproduces the stock enable-all state, k8s nodes enable
    # only BaseOS/AppStream (internal mirror serves just those). deploy/repo.py
    # and deploy/prepare.py later push the same render under their own role.
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
{repo_files}
runcmd:
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
    if name == config.REPO_MIRROR_HOSTNAME:
        attach_repo_volume(name)
    run(["incus", "start", name])
    run(["incus", "wait", name, "agent"], check=False)


def destroy_vm(name: str, *, purge_data: bool = False) -> None:
    if not vm_exists(name):
        print(f"[skip] {name} does not exist")
        return
    run(["incus", "delete", "--force", name])
    print(f"[done] {name} destroyed")
    if purge_data and name == config.REPO_MIRROR_HOSTNAME:
        purge_repo_volume()


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
    parser.add_argument(
        "--purge-repos-data",
        action="store_true",
        help="with --destroy: also delete the persistent k8s-repo repos volume",
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
    run_parallel(
        selected,
        (lambda n: destroy_vm(n, purge_data=args.purge_repos_data)) if args.destroy else create_vm,
        args.parallel,
    )


main()
