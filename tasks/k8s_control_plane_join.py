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
from pyinfra.facts.files import File
from pyinfra.operations import files, server

import config
from deploy import _common

join_cp_src = f"{config.OFFLINE_DIR}/join-control-plane-command.txt"
join_cmd_dst = f"{host.data.get('node_offline_dir', config.NODE_OFFLINE_DIR)}/join-command.txt"

files.put(
    name=f"[{host.name}] Upload control-plane join command",
    src=join_cp_src,
    dest=join_cmd_dst,
    _sudo=True,
)

server.shell(
    name=f"[{host.name}] Run kubeadm join --control-plane",
    commands=[f"bash {join_cmd_dst}"],
    _sudo=True,
    # kubelet.conf is in a root-owned dir, so check existence via the File fact
    # (stat works because /etc/kubernetes is world-traversable) rather than a
    # non-root test.
    _if=lambda: host.get_fact(File, path=_common.KUBELET_CONF) is None,
)
