"""Single source of truth for the k8s lab topology and offline layout.

Both the pyinfra deploy scripts (deploy/*) and the shell tooling derive their
values from here. Change a node/IP/memory in ONE place.

Artifact versions (k8s minor, Cilium chart, containerd.io RPM, ...) are pinned
in scripts/download_offline.py, which resolves the exact k8s patch and writes
the result into the offline bundle.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

# ---------- offline bundle layout ----------
OFFLINE_DIR = os.environ.get("OFFLINE_DIR", str(REPO_ROOT / "offline")).rstrip("/")
NODE_OFFLINE_DIR = "/opt/k8s-offline"

SSH_USER = "admin"

# ---------- internal repo mirror (k8s-repo VM) ----------
REPO_MIRROR_HOSTNAME = "k8s-repo"
REPO_MIRROR_IP = "10.98.68.13"
# Client-facing baseurl (the mirror serves RPM repos over plain HTTP on :80).
REPO_MIRROR_URL = f"http://{REPO_MIRROR_IP}"
K8S_MINOR = "1.36"
# Path of the mirrored k8s repo as served on the mirror (mirrors pkgs.k8s.io layout).
K8S_REPO_SERVED_PATH = f"k8s/core:/stable:/v{K8S_MINOR}/rpm"
# Mirrored containerd/docker stable repo path on the mirror.
DOCKER_REPO_SERVED_PATH = "docker/linux/centos/10/x86_64/stable"

# ---------- topology ----------
MASTER_HOSTNAME = "k8s-master"
MASTER_IP = "10.98.68.10"
WORKER_IPS = ["10.98.68.11", "10.98.68.12"]
ALL_NODES = [MASTER_IP, *WORKER_IPS]

# VM specs keyed by Incus instance name (used by incus/incus_vms.py)
VMS = {
    "k8s-master": {"vcpu": 4, "memory": "6GiB", "disk": "20GiB", "ip": MASTER_IP},
    "k8s-worker-1": {"vcpu": 4, "memory": "3GiB", "disk": "20GiB", "ip": WORKER_IPS[0]},
    "k8s-worker-2": {"vcpu": 4, "memory": "3GiB", "disk": "20GiB", "ip": WORKER_IPS[1]},
    REPO_MIRROR_HOSTNAME: {
        "vcpu": 2,
        "memory": "2GiB",
        "disk": "30GiB",
        "ip": REPO_MIRROR_IP,
    },
}
