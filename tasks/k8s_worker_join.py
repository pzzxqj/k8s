"""Atomic task (workers): join the cluster with the fetched join command."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.operations import files, server

import config
from deploy import _common

join_cmd_src = f"{config.OFFLINE_DIR}/join-command.txt"

files.put(
    name=f"[{host.name}] Upload worker join command",
    src=join_cmd_src,
    dest=_common.JOIN_CMD_DST,
    _sudo=True,
)

server.shell(
    name=f"[{host.name}] Run kubeadm join",
    commands=[f"bash {_common.JOIN_CMD_DST}"],
    _sudo=True,
    # kubelet.conf is 0600 root, so check existence via sudo rather than facts
    _if=lambda: not _common.safe_file_exists(_common.KUBELET_CONF),
)