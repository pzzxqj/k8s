"""Atomic task (workers): join the cluster with the fetched join command."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.facts.files import File
from pyinfra.operations import files, server

import config
from deploy import _common

join_cmd_src = f"{config.OFFLINE_DIR}/join-command.txt"
join_cmd_dst = f"{host.data.get('node_offline_dir', config.NODE_OFFLINE_DIR)}/join-command.txt"

files.put(
    name=f"[{host.name}] Upload worker join command",
    src=join_cmd_src,
    dest=join_cmd_dst,
    _sudo=True,
)

server.shell(
    name=f"[{host.name}] Run kubeadm join",
    commands=[f"bash {join_cmd_dst}"],
    _sudo=True,
    # kubelet.conf is in a root-owned dir, so check existence via the File fact
    # (stat works because /etc/kubernetes is world-traversable) rather than a
    # non-root test.
    _if=lambda: host.get_fact(File, path=_common.KUBELET_CONF) is None,
)
