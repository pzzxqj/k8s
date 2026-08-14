"""Shared helpers for the pyinfra deploy scripts in this directory.

Each deploy script starts with the 3-line bootstrap:

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # this dir
    import _common  # noqa: E402
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.facts.server import Command

import config

FSTAB = "/etc/fstab"
SELINUX_CONFIG = "/etc/selinux/config"
CONTAINERD_CONFIG = "/etc/containerd/config.toml"
MODULES_CONF = "/etc/modules-load.d/k8s.conf"
SYSCTL_CONF = "/etc/sysctl.d/k8s.conf"

ADMIN_CONF = "/etc/kubernetes/admin.conf"
KUBELET_CONF = "/etc/kubernetes/kubelet.conf"
KUBEADM_YAML = "/etc/kubernetes/kubeadm.yaml"
JOIN_CMD_DST = f"{config.NODE_OFFLINE_DIR}/join-command.txt"


def is_master() -> bool:
    """True when this inventory host is the control-plane node."""
    return host.name == config.MASTER_IP


def safe_file_exists(path: str) -> bool:
    """Check a (possibly 0600 root-only) remote file's existence.

    Plain pyinfra facts can't read root-only files (returns False) and the
    Command fact errors on a non-zero exit — so probe with an always-exit-0
    `sudo test` wrapper.
    """
    return (
        host.get_fact(Command, f"sudo test -e {path} && echo yes || echo no") or ""
    ).strip() == "yes"
