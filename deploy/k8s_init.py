"""Initialize the control-plane node with kubeadm + Cilium.

Orchestrates the control-plane atomic tasks (run the same way in every
environment). Run ONLY against the `control_plane` group, after
deploy/k8s_prepare.py:

    uv run pyinfra -y inventories/k8s_test.py deploy/k8s_init.py --limit control_plane
    uv run pyinfra -y inventories/k8s_production.py deploy/k8s_init.py --limit control_plane

Idempotent: kubeadm init is skipped once /etc/kubernetes/admin.conf exists; the
admin kubeconfig and Cilium CLI are only (re)installed on drift; `cilium
install` and the join-command write are gated on facts, so the join token is
only rotated when the stored one has expired.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra import local

from deploy import _common

if not _common.is_control_plane():
    print("[skip] not the control-plane node (missing 'control_plane' group)")
    raise SystemExit(0)

local.include("tasks/kubeadm_init.py")
local.include("tasks/kubeconfig.py")
local.include("tasks/cilium.py")
local.include("tasks/k8s_join_command.py")