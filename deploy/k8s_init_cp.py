"""Join additional control-plane nodes to the kubeadm cluster (HA only).

Run against the `control_plane` group AFTER deploy/k8s_init.py bootstrapped the
first master and `just join-cp` fetched the control-plane join command from it.
Only relevant when the inventory has >1 control-plane host; the bootstrap host
and single-master clusters self-skip (decided by deploy/_topology.py):

    uv run pyinfra -y inventories/k8s_test.py deploy/k8s_init_cp.py --limit control_plane
    uv run pyinfra -y inventories/k8s_production.py deploy/k8s_init_cp.py --limit control_plane

Idempotent: skips a node once /etc/kubernetes/kubelet.conf exists.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra import local
from pyinfra.context import host

import config
from deploy import _topology

topo = _topology.topology()

if "control_plane" not in host.groups:
    print("[skip] not the control-plane group")
    raise SystemExit(0)

if not topo.ha:
    print("[skip] single control plane (no HA) — nothing to join")
    raise SystemExit(0)

if topo.is_bootstrap(host.name):
    print(f"[skip] {host.name} is the bootstrap control plane — nothing to join")
    raise SystemExit(0)

CP_JOIN_SRC = f"{config.OFFLINE_DIR}/join-control-plane-command.txt"
if not Path(CP_JOIN_SRC).is_file():
    raise SystemExit(
        f"[error] {CP_JOIN_SRC} not found — run `just join-cp` to fetch it from "
        "the bootstrap master first"
    )

local.include("tasks/k8s_control_plane_join.py")
local.include("tasks/kubeconfig.py")
