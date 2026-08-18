"""Atomic task (additional control planes): join with the control-plane command.

Runs `kubeadm join --control-plane` using the command generated on the bootstrap
master by tasks/k8s_join_command.py (fetched into $OFFLINE_DIR/
join-control-plane-command.txt by `just join-cp`). Idempotent: skips once
/etc/kubernetes/kubelet.conf exists.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.operations import files, server

import config
from deploy import _common

join_cp_src = f"{config.OFFLINE_DIR}/join-control-plane-command.txt"

files.put(
    name=f"[{host.name}] Upload control-plane join command",
    src=join_cp_src,
    dest=_common.JOIN_CMD_DST,
    _sudo=True,
)

server.shell(
    name=f"[{host.name}] Run kubeadm join --control-plane",
    commands=[f"bash {_common.JOIN_CMD_DST}"],
    _sudo=True,
    # kubelet.conf is 0600 root, so check existence via sudo rather than facts
    _if=lambda: not _common.safe_file_exists(_common.KUBELET_CONF),
)
