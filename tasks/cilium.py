"""Atomic task (bootstrap control plane): install Cilium CLI + CNI from the offline chart.

kubeProxyReplacement=true + k8sServiceHost/Port = full eBPF service LB with NO
kube-proxy (kubeadm skipped its addon in tasks/kubeadm_init.py). useDigest=false:
the chart pins image digests (@sha256:) but our offline bundle imports images by
tag only, so containerd can't resolve digest refs offline -> ImagePullBackOff.

The k8sServiceHost is the shared HA endpoint (Keepalived VIP when the control
plane is HA, else this node's own IP), and the install runs only once from the
bootstrap control plane.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.facts.server import Command
from pyinfra.operations import files, server

import config
from deploy import _topology

offline_dir = host.data.get("node_offline_dir", config.NODE_OFFLINE_DIR)
topo = _topology.topology()


def cilium_cli_current() -> bool:
    src = f"{offline_dir}/cilium/cilium"
    dst = "/usr/local/bin/cilium"
    return (
        host.get_fact(
            Command,
            f"sudo test -f {dst} && sudo cmp -s {src} {dst} && echo yes || echo no",
        )
        or ""
    ).strip() == "yes"


def cilium_installed() -> bool:
    return (
        host.get_fact(
            Command,
            "kubectl -n kube-system get daemonset cilium >/dev/null 2>&1 && echo yes || echo no",
        )
        or ""
    ).strip() == "yes"


def bootstrap_node() -> bool:
    return topo.is_bootstrap(host.name)


files.copy(
    name=f"[{host.name}] Install Cilium CLI to /usr/local/bin",
    src=f"{offline_dir}/cilium/cilium",
    dest="/usr/local/bin/",
    overwrite=True,
    _sudo=True,
    _if=lambda: bootstrap_node() and not cilium_cli_current(),
)

server.shell(
    name=f"[{host.name}] Install Cilium CNI from local chart (kube-proxy free)",
    commands=[
        (
            "cilium install "
            f"--chart-directory {offline_dir}/cilium/chart "
            "--set kubeProxyReplacement=true "
            f"--set k8sServiceHost={topo.endpoint_ip} "
            f"--set k8sServicePort={host.data.apiserver_port} "
            "--set nodePort.enabled=true "
            "--set hostPort.enabled=true "
            "--set operator.replicas=1 "
            "--set hubble.enabled=false "
            "--set image.useDigest=false "
            "--set operator.image.useDigest=false "
            "--set envoy.image.useDigest=false"
        ),
    ],
    _if=lambda: bootstrap_node() and not cilium_installed(),
)
