"""Join worker nodes to the kubeadm cluster.

Run ONLY against the `workers` group, after the master is initialized and the
join command has been fetched from it into $OFFLINE_DIR/join-command.txt
(`just join` does this automatically):

    uv run pyinfra -y inventories/learning.py deploy/k8s_join.py --limit workers
    uv run pyinfra -y inventories/production.py deploy/k8s_join.py --limit workers

Idempotent: skips a node once /etc/kubernetes/kubelet.conf exists.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra import local

import config

JOIN_CMD_SRC = f"{config.OFFLINE_DIR}/join-command.txt"

if not Path(JOIN_CMD_SRC).is_file():
    raise SystemExit(
        f"[error] {JOIN_CMD_SRC} not found — run `just join` or fetch the join "
        "command from the master first"
    )

local.include("tasks/k8s_worker_join.py")