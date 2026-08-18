"""Atomic task (bootstrap control plane): bootstrap the cluster with kubeadm.

kube-proxy is NOT installed (--skip-phases=addon/kube-proxy): Cilium fully
replaces it via eBPF (see tasks/cilium.py / README "kube-proxy free"). Only the
FIRST control-plane node (deploy/_topology.py, first entry of the inventory's
`control_plane` list) runs `kubeadm init`; additional control planes join via
deploy/k8s_init_cp.py. The advertise address comes from the host's ssh_hostname,
while the controlPlaneEndpoint is the shared HA endpoint (Keepalived VIP when the
control plane is HA, else the single master's own IP — no behaviour change). With
an HA control plane the apiserver is bound to the node's own IP (bind-address)
so it never collides with HAProxy on the VIP.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.facts.files import File
from pyinfra.facts.server import Command
from pyinfra.operations import files, server

import config
from deploy import _common, _topology

offline_dir = host.data.get("node_offline_dir", config.NODE_OFFLINE_DIR)
topo = _topology.topology()


def installed_k8s_version() -> str:
    raw = (host.get_fact(Command, "kubeadm version -o json 2>/dev/null || true") or "").strip()
    try:
        return str(json.loads(raw)["clientVersion"]["gitVersion"])
    except (ValueError, KeyError, TypeError):
        return (
            host.get_fact(Command, f"cat {offline_dir}/k8s-version.txt") or "v0.0.0"
        ).strip()


def bootstrap_node() -> bool:
    return topo.is_bootstrap(host.name)


files.template(
    name=f"[{host.name}] Write kubeadm init configuration",
    src=str(config.REPO_ROOT / "templates" / "kubeadm.yaml.j2"),
    dest=_common.KUBEADM_YAML,
    k8s_version=installed_k8s_version(),
    master_ip=host.data.ssh_hostname,
    apiserver_port=host.data.apiserver_port,
    service_subnet=host.data.service_subnet,
    control_plane_endpoint=topo.endpoint if topo.ha else "",
    bind_address=host.data.ssh_hostname if topo.ha else "",
    _sudo=True,
    _if=bootstrap_node,
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
    _if=lambda: bootstrap_node()
    and host.get_fact(File, path=_common.ADMIN_CONF) is None,
)
