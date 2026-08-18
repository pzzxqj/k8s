"""Shared helpers for the pyinfra deploy scripts and tasks.

Every deploy orchestrator (deploy/*.py) starts with the bootstrap that puts the
repo root on sys.path, then the tasks (tasks/*.py), loaded via local.include,
import `from deploy import _common` and `import config` the same way.

Host/role data is data-driven (inventory Host Data + group_data), so the
"control plane" role is group membership (`control_plane` group exists in every
inventory) and the SSH user comes from host data (admin for the k8s test
cluster, zhch for k8s production).
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
JOIN_CMD_DST = f"{host.data.get('node_offline_dir', config.NODE_OFFLINE_DIR)}/join-command.txt"


def is_control_plane() -> bool:
    """True when this host is in the `control_plane` group (any env)."""
    return "control_plane" in host.groups


def ssh_user() -> str:
    """The SSH user for this host (Host Data), falling back to config."""
    return str(host.data.get("ssh_user") or config.SSH_USER)


def safe_file_exists(path: str) -> bool:
    """Check a (possibly 0600 root-only) remote file's existence.

    Plain pyinfra facts can't read root-only files (returns False) and the
    Command fact errors on a non-zero exit — so probe with an always-exit-0
    `sudo test` wrapper.
    """
    return (
        host.get_fact(Command, f"sudo test -e {path} && echo yes || echo no") or ""
    ).strip() == "yes"