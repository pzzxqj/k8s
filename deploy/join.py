"""Join worker nodes to the kubeadm cluster created by deploy/init.py.

Run ONLY against the workers, after the master is initialized and the join
command has been fetched from it into $OFFLINE_DIR/join-command.txt
(scripts/cluster.sh does this automatically):

    uv run pyinfra -y inventory.py deploy/join.py --limit k8s_workers \
        --user admin --key ~/.ssh/id_ed25519

Idempotent: skips a node once /etc/kubernetes/kubelet.conf exists.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common
from pyinfra.operations import files, server

import config

JOIN_CMD_SRC = f"{config.OFFLINE_DIR}/join-command.txt"

if not Path(JOIN_CMD_SRC).is_file():
    raise SystemExit(
        f"[error] {JOIN_CMD_SRC} not found — run cluster.sh or fetch the join "
        "command from the master first"
    )

# kubelet.conf is 0600 root, so check existence via sudo rather than reading it
joined = _common.safe_file_exists(_common.KUBELET_CONF)

files.put(
    name="Upload worker join command",
    src=JOIN_CMD_SRC,
    dest=_common.JOIN_CMD_DST,
    _sudo=True,
)

server.shell(
    name="Run kubeadm join",
    commands=[f"bash {_common.JOIN_CMD_DST}"],
    _sudo=True,
    _if=lambda: not joined,
)
