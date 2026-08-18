"""Single source of truth for the LEARNING k8s lab topology and offline layout.

VM/network topology lives here (used by incus/incus_vms.py + Justfile). The
pyinfra node lists live in inventories/learning.py (Host Data); repo sources in
group_data/.

Upstream repo constants (K8S_MINOR / K8S_UPSTREAM_BASE / DOCKER_UPSTREAM_BASE /
ALMA_UPSTREAM_BASE) are shared with the intranet mirror and live in
mirror/config.py — the learning cluster's "mirror source" IS those upstreams,
while production points at the intranet mirror (group_data/production.py).

Artifact versions: Cilium chart/CLI are pinned in scripts/k8s_download_offline.py; the
k8s version follows the host's local kubeadm (`kubeadm config images list`) and is
written into the offline bundle's k8s-version.txt.
"""

import os
from pathlib import Path

from mirror.config import (
    ALMA_UPSTREAM_BASE,  # noqa: F401  (re-exported: group_data/learning.py uses config.ALMA_UPSTREAM_BASE)
    DOCKER_UPSTREAM_BASE,  # noqa: F401  (re-exported: group_data/learning.py uses config.DOCKER_UPSTREAM_BASE)
    K8S_UPSTREAM_BASE,  # noqa: F401  (re-exported: group_data/learning.py + k8s_download_offline.py)
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

# VM specs keyed by Incus instance name (used by incus/incus_vms.py)
VMS = {
    "k8s-master": {"vcpu": 4, "memory": "6GiB", "disk": "20GiB", "ip": MASTER_IP},
    "k8s-worker-1": {"vcpu": 4, "memory": "3GiB", "disk": "20GiB", "ip": WORKER_IPS[0]},
    "k8s-worker-2": {"vcpu": 4, "memory": "3GiB", "disk": "20GiB", "ip": WORKER_IPS[1]},
}