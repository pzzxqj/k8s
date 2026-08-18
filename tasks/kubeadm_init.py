"""Atomic task (control plane): bootstrap the cluster with kubeadm.

kube-proxy is NOT installed (--skip-phases=addon/kube-proxy): Cilium fully
replaces it via eBPF (see tasks/cilium.py / README "kube-proxy free"). The
advertise address / control-plane endpoint come from the host's ssh_hostname
data, so learning (.68.10) and production (.90.220) need no special-casing.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.facts.server import Command
from pyinfra.operations import files, server

import config
from deploy import _common

offline_dir = host.data.get("node_offline_dir", config.NODE_OFFLINE_DIR)


def installed_k8s_version() -> str:
    raw = (host.get_fact(Command, "kubeadm version -o json 2>/dev/null || true") or "").strip()
    try:
        return str(json.loads(raw)["clientVersion"]["gitVersion"])
    except (ValueError, KeyError, TypeError):
        return (
            host.get_fact(Command, f"cat {offline_dir}/k8s-version.txt") or "v0.0.0"
        ).strip()


files.template(
    name=f"[{host.name}] Write kubeadm init configuration",
    src=str(config.REPO_ROOT / "templates" / "kubeadm.yaml.j2"),
    dest=_common.KUBEADM_YAML,
    k8s_version=installed_k8s_version(),
    master_ip=host.data.ssh_hostname,
    apiserver_port=host.data.apiserver_port,
    service_subnet=host.data.service_subnet,
    _sudo=True,
)

server.shell(
    name=f"[{host.name}] Run kubeadm init, skipping the kube-proxy addon",
    commands=[
        (
            "kubeadm init --config /etc/kubernetes/kubeadm.yaml "
            "--skip-phases=addon/kube-proxy"
        )
    ],
    _sudo=True,
    _if=lambda: not _common.safe_file_exists(_common.ADMIN_CONF),
)