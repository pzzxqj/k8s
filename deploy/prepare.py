"""Prepare AlmaLinux 10 nodes for kubeadm with containerd + Cilium (offline).

AlmaLinux 10's own repo packages (kernel-modules-extra, container-selinux, ...)
are installed via ONLINE dnf. Everything k8s-related (containerd.io,
kubelet/kubeadm/kubectl, container images, Cilium) comes from the LOCAL offline
bundle produced by scripts/download_offline.sh:

    ./scripts/download_offline.sh                    # build ./offline on the host
    uv run pyinfra -y inventory.py deploy/prepare.py --user tux --key ~/.ssh/id_ed25519

Runs against every node (master + workers). The kubeadm init/join step is
handled separately by deploy/init.py (master) / deploy/join.py (workers).
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common  # noqa: E402
import config  # noqa: E402

from pyinfra.context import host  # noqa: E402
from pyinfra.facts.files import FindInFile  # noqa: E402
from pyinfra.facts.server import Command, KernelVersion, Selinux  # noqa: E402
from pyinfra.operations import files, server  # noqa: E402

is_master = _common.is_master()

# NOTE: the SFTP subsystem is enabled in cloud-init (incus/incus_vms.py); it is
# required by files.put / files.sync / dnf.repo.

# 1. Online: AlmaLinux base packages (kernel-modules-extra, dnf tooling)
kernel_version = host.get_fact(KernelVersion)
kernel_modules_pkg = f"kernel-modules-extra-{re.sub(r'\.[^.]*$', '', kernel_version)}"
server.packages(
    name=f"Ensure {kernel_modules_pkg} is installed (Alma repo, online)",
    packages=[kernel_modules_pkg],
    _sudo=True,
)

# container-selinux is a hard dependency of the containerd.io RPM and is only
# available from the online Alma repos — install it before the offline RPMs.
server.packages(
    name="Ensure container-selinux is installed (containerd.io dep, online)",
    packages=["container-selinux"],
    _sudo=True,
)

# 2. Load required kernel modules
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

# 3. sysctl settings required by kubelet / CNI
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

# 4. Swap off
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

# 5. SELinux permissive
files.line(
    name="Set SELINUX=permissive in /etc/selinux/config",
    path=_common.SELINUX_CONFIG,
    line="^SELINUX=enforcing$",
    replace="SELINUX=permissive",
    present=True,
    _if=lambda: bool(
        host.get_fact(FindInFile, path=_common.SELINUX_CONFIG, pattern="^SELINUX=enforcing$")
    ),
    _sudo=True,
)
server.shell(
    name="Set the current SELinux mode to permissive",
    commands=["setenforce 0"],
    _if=lambda: host.get_fact(Selinux).get("mode") == "enabled",
    _sudo=True,
)

# 6. Upload the offline bundle (RPMs, image tarballs, Cilium CLI + chart)
files.sync(
    name="Upload offline bundle to nodes",
    src=config.OFFLINE_DIR,
    dest=config.NODE_OFFLINE_DIR,
    delete=False,
    _sudo=True,
)

# 7. Install containerd.io + kubelet/kubeadm/kubectl from local RPMs.
# dnf resolves any missing deps from the node's ONLINE Alma repos (allowed —
# only the k8s-related components are forced offline). Skipped when kubelet is
# already present so re-running prep never reinstalls/restarts the runtime.
rpm_db_has = lambda pkg: (  # noqa: E731
    (host.get_fact(Command, f"rpm -q {pkg} >/dev/null 2>&1 && echo yes || echo no") or "").strip()
    == "yes"
)
server.shell(
    name="Install containerd.io + k8s RPMs from local offline bundle",
    commands=[f"dnf install -y {config.NODE_OFFLINE_DIR}/rpms/*.rpm"],
    _sudo=True,
    _if=lambda: not rpm_db_has("kubelet"),
)

# 8. containerd config: systemd cgroup driver, matching pause image, CNI dirs
files.template(
    name="Write containerd config with systemd cgroup driver",
    src=str(config.REPO_ROOT / "templates" / "containerd-config.toml.j2"),
    dest=_common.CONTAINERD_CONFIG,
    sandbox_image="registry.k8s.io/pause:3.10.2",
    _sudo=True,
)

# 9. Start containerd
server.service(
    name="Enable and start containerd",
    service="containerd",
    running=True,
    enabled=True,
    _sudo=True,
)

# 10. Preload container images into the k8s.io namespace. Master also gets the
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

# 11. kubelet ships its own systemd unit; enable it (start happens at init/join).
# systemctl enable (not a running-state op) so re-running prep never stops a
# kubelet that is already serving a joined node.
server.shell(
    name="Enable kubelet at boot",
    commands=["systemctl enable kubelet"],
    _sudo=True,
)
