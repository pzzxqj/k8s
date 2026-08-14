"""Prepare AlmaLinux 10 nodes for kubeadm with containerd + Cilium.

Everything the nodes install comes from intranet sources only:
  * AlmaLinux base packages (kernel-modules-extra, container-selinux, ...) ->
    BaseOS/AppStream mirrored on the internal vm (deploy/repo.py), via
    /etc/yum.repos.d/almalinux-mirror.repo
  * k8s/containerd RPMs -> the same mirror, via kubernetes.repo + docker-ce.repo
  * container images + Cilium -> the offline bundle at /opt/k8s-offline, already
    pushed there by scripts/download_offline.py (rsync, no pyinfra).

Workflow:
    uv run pyinfra -y inventory.py deploy/repo.py --limit k8s_repo --user admin
    uv run python scripts/download_offline.py            # build ./offline + rsync to nodes
    uv run pyinfra -y inventory.py deploy/prepare.py --user admin --key ~/.ssh/id_ed25519

Runs against every node (master + workers). The kubeadm init/join step is
handled separately by deploy/init.py (master) / deploy/join.py (workers).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common
from pyinfra.context import host
from pyinfra.facts.files import FindInFile
from pyinfra.facts.server import Command, Selinux
from pyinfra.operations import files, server

import config

is_master = _common.is_master()

# NOTE: the SFTP subsystem is enabled in cloud-init (incus/incus_vms.py); it is
# required by files.put / dnf.repo.

# 1. Fully intranet dnf: replace the node's online AlmaLinux repos with the
# internal mirror (repo.py mirrors BaseOS + AppStream), then all of the dnf
# installs below (kernel-modules-extra, container-selinux, ...) resolve from
# the mirror only. The original files are moved aside (not deleted) so the
# change is reversible; `mv` globbing nothing on re-runs is a no-op.
server.shell(
    name="Disable online AlmaLinux repo files",
    commands=[
        "mkdir -p /etc/yum.repos.d/online-backup",
        "mv -f /etc/yum.repos.d/almalinux-*.repo /etc/yum.repos.d/online-backup/ || true",
    ],
    _sudo=True,
)
files.template(
    name="Point AlmaLinux necessary packages at the internal mirror",
    src=str(config.REPO_ROOT / "templates" / "almalinux-mirror.repo.j2"),
    dest="/etc/yum.repos.d/almalinux-mirror.repo",
    mirror_url=config.REPO_MIRROR_URL,
    alma_path=config.ALMA_SERVED_PATH,
    _sudo=True,
)

# 2. Mirror-provided AlmaLinux base packages (kernel-modules-extra, dnf
# tooling). Pin kernel-modules-extra to the RUNNING kernel: `dnf install
# kernel-modules-extra` would otherwise pull the newest patch whose module
# tree doesn't match the booted kernel, leaving br_netfilter unloadable.
running_kernel = (host.get_fact(Command, "uname -r") or "").strip()
server.shell(
    name=f"Ensure kernel-modules-extra matches running kernel ({running_kernel})",
    commands=[f"dnf install -y kernel-modules-extra-{running_kernel}"],
    _sudo=True,
)

# container-selinux is a hard dependency of the containerd.io RPM and is only
# available from the AlmaLinux repos — install it before the offline RPMs.
server.packages(
    name="Ensure container-selinux is installed (containerd.io dep)",
    packages=["container-selinux"],
    _sudo=True,
)

# 3. Load required kernel modules
for module in ["overlay", "br_netfilter"]:
    files.line(
        name=f"Persist kernel module {module} for boot",
        path=_common.MODULES_CONF,
        line=module,
        present=True,
        _sudo=True,
    )
    server.modprobe(
        name=f"Ensure kernel module {module} is loaded",
        module=module,
        present=True,
        _sudo=True,
    )

# 4. sysctl settings required by kubelet / CNI
sysctl_params = {
    "net.bridge.bridge-nf-call-iptables": 1,
    "net.bridge.bridge-nf-call-ip6tables": 1,
    "net.ipv4.ip_forward": 1,
}
for key, value in sysctl_params.items():
    server.sysctl(
        name=f"Ensure sysctl {key} = {value}",
        key=key,
        value=value,
        persist=True,
        persist_file=_common.SYSCTL_CONF,
        _sudo=True,
    )

# 5. Swap off
swap_regex = r"^[^#].*swap.*$"
files.line(
    name="Comment out active swap lines in /etc/fstab",
    path=_common.FSTAB,
    line=swap_regex,
    replace=r"# \g<0>",
    flags=["E"],
    present=True,
    _if=lambda: bool(host.get_fact(FindInFile, path=_common.FSTAB, pattern=swap_regex)),
    _sudo=True,
)
server.shell(
    name="Turn off any active swap",
    commands=["swapoff -a"],
    _sudo=True,
)

# 6. SELinux permissive
files.line(
    name="Set SELINUX=permissive in /etc/selinux/config",
    path=_common.SELINUX_CONFIG,
    line="^SELINUX=enforcing$",
    replace="SELINUX=permissive",
    present=True,
    _if=lambda: bool(
        host.get_fact(
            FindInFile, path=_common.SELINUX_CONFIG, pattern="^SELINUX=enforcing$"
        )
    ),
    _sudo=True,
)
server.shell(
    name="Set the current SELinux mode to permissive",
    commands=["setenforce 0"],
    _if=lambda: host.get_fact(Selinux).get("mode") == "enabled",
    _sudo=True,
)

# 7. The offline bundle (container images, Cilium CLI/chart) was already rsynced
# to /opt/k8s-offline by scripts/download_offline.py — no upload here.

# 8. Point dnf at the internal repo mirror (deploy/repo.py serves it, see
# templates/kubernetes.repo.j2) and install containerd.io + kubelet/kubeadm/
# kubectl from there. All base deps already resolve from the mirror via
# /etc/yum.repos.d/almalinux-mirror.repo. Skipped when kubelet is already
# present so re-running prep never reinstalls/restarts the runtime.
def rpm_db_has(pkg: str) -> bool:
    return (
        (
            host.get_fact(Command, f"rpm -q {pkg} >/dev/null 2>&1 && echo yes || echo no")
            or ""
        ).strip()
        == "yes"
    )


files.template(
    name="Point dnf at the internal kubernetes mirror",
    src=str(config.REPO_ROOT / "templates" / "kubernetes.repo.j2"),
    dest="/etc/yum.repos.d/kubernetes.repo",
    mirror_url=config.REPO_MIRROR_URL,
    k8s_repo_path=config.K8S_REPO_SERVED_PATH,
    _sudo=True,
)
files.template(
    name="Point dnf at the internal containerd mirror",
    src=str(config.REPO_ROOT / "templates" / "docker-ce.repo.j2"),
    dest="/etc/yum.repos.d/docker-ce.repo",
    mirror_url=config.REPO_MIRROR_URL,
    docker_repo_path=config.DOCKER_REPO_SERVED_PATH,
    _sudo=True,
)
server.shell(
    name="Install containerd.io + k8s RPMs from the internal mirror",
    commands=[
        "dnf install -y kubelet kubeadm kubectl cri-tools kubernetes-cni containerd.io"
    ],
    _sudo=True,
    _if=lambda: not rpm_db_has("kubelet"),
)

# 9. containerd config: systemd cgroup driver, matching pause image, CNI dirs
files.template(
    name="Write containerd config with systemd cgroup driver",
    src=str(config.REPO_ROOT / "templates" / "containerd-config.toml.j2"),
    dest=_common.CONTAINERD_CONFIG,
    sandbox_image="registry.k8s.io/pause:3.10.2",
    _sudo=True,
)

# 10. Start containerd
server.service(
    name="Enable and start containerd",
    service="containerd",
    running=True,
    enabled=True,
    _sudo=True,
)

# 11. Preload container images into the k8s.io namespace. Master also gets the
# control-plane-only images (kube-apiserver etc.), workers only the common ones.
files.put(
    name="Upload image import helper",
    src=str(config.REPO_ROOT / "scripts" / "import_images.sh"),
    dest=f"{config.NODE_OFFLINE_DIR}/import_images.sh",
    mode="755",
    _sudo=True,
)
server.shell(
    name="Preload container images into containerd",
    commands=[
        f"bash {config.NODE_OFFLINE_DIR}/import_images.sh {'master' if is_master else 'all'}"
    ],
    _sudo=True,
)

# 12. kubelet ships its own systemd unit; enable it (start happens at init/join).
# systemctl enable (not a running-state op) so re-running prep never stops a
# kubelet that is already serving a joined node.
server.shell(
    name="Enable kubelet at boot",
    commands=["systemctl enable kubelet"],
    _sudo=True,
)
