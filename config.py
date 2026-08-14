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
}
