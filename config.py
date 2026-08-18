"""Single source of truth for the k8s lab topology and offline layout.

Both the pyinfra deploy scripts (deploy/*) and the shell tooling derive their
values from here. Change a node/IP/memory in ONE place.

Upstream repo constants (K8S_MINOR / K8S_UPSTREAM_BASE / DOCKER_UPSTREAM_BASE /
ALMA_UPSTREAM_BASE) are shared with the intranet mirror and live in
mirror/config.py — the lab nodes install RPMs straight from those upstreams (no
internal mirror in the learning env).

Artifact versions: Cilium chart/CLI are pinned in scripts/download_offline.py; the
k8s version follows the host's local kubeadm (`kubeadm config images list`) and is
written into the offline bundle's k8s-version.txt.
"""

import os
from pathlib import Path

from mirror.config import (
    ALMA_UPSTREAM_BASE,  # noqa: F401  (re-exported: deploy/prepare.py uses config.ALMA_UPSTREAM_BASE)
    DOCKER_UPSTREAM_BASE,  # noqa: F401  (re-exported: deploy/prepare.py uses config.DOCKER_UPSTREAM_BASE)
    K8S_UPSTREAM_BASE,  # noqa: F401  (re-exported: deploy/prepare.py + download_offline.py)
)

REPO_ROOT = Path(__file__).resolve().parent

# ---------- offline bundle layout ----------
OFFLINE_DIR = os.environ.get("OFFLINE_DIR", str(REPO_ROOT / "offline")).rstrip("/")
NODE_OFFLINE_DIR = "/opt/k8s-offline"

SSH_USER = "admin"

# ---------- topology ----------
MASTER_HOSTNAME = "k8s-master"
MASTER_IP = "10.98.68.10"
# kube-apiserver secure port (kubeadm default 6443). Passed to Cilium as
# k8sServiceHost/Port in the kube-proxy-free setup (init.py).
APISERVER_PORT = "6443"
# Pod-network-agnostic service subnet; kubeadm networking.serviceSubnet.
SERVICE_SUBNET = "10.96.0.0/12"
WORKER_IPS = ["10.98.68.11", "10.98.68.12"]
ALL_NODES = [MASTER_IP, *WORKER_IPS]

# VM specs keyed by Incus instance name (used by incus/incus_vms.py)
VMS = {
    "k8s-master": {"vcpu": 4, "memory": "6GiB", "disk": "20GiB", "ip": MASTER_IP},
    "k8s-worker-1": {"vcpu": 4, "memory": "3GiB", "disk": "20GiB", "ip": WORKER_IPS[0]},
    "k8s-worker-2": {"vcpu": 4, "memory": "3GiB", "disk": "20GiB", "ip": WORKER_IPS[1]},
}