"""Atomic task (bootstrap control plane): write the worker + control-plane join commands.

Two files are (re)generated only when missing or their bootstrap token has
expired, so re-running init never rotates a live token:

  * /etc/kubernetes/join-command.txt              — plain `kubeadm join` (workers)
  * /etc/kubernetes/join-control-plane-command.txt — `kubeadm join --control-plane`
    with `--certificate-key` (additional control planes, HA only; certs are
    uploaded via `kubeadm init phase upload-certs --upload-certs`, which also
    works when extending an already-single-master cluster).

The host fetches them via `just join` / `just join-cp` into $OFFLINE_DIR/.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.facts.server import Command
from pyinfra.operations import server

from deploy import _topology

JOIN_CMD_MASTER = "/etc/kubernetes/join-command.txt"
JOIN_CP_CMD_MASTER = "/etc/kubernetes/join-control-plane-command.txt"

topo = _topology.topology()


def _token_current(path: str) -> bool:
    raw = host.get_fact(Command, f"sudo cat {path} 2>/dev/null || true") or ""
    m = re.search(r"--token\s+(\S+)", raw)
    if not m:
        return False
    listed = (
        host.get_fact(
            Command,
            f"sudo kubeadm token list 2>/dev/null | grep -qF -- {m.group(1)} && echo yes || echo no",
        )
        or ""
    ).strip()
    return listed == "yes"


def bootstrap_node() -> bool:
    return topo.is_bootstrap(host.name)


server.shell(
    name=f"[{host.name}] Write worker join command to {JOIN_CMD_MASTER}",
    commands=[f"kubeadm token create --print-join-command > {JOIN_CMD_MASTER}"],
    _sudo=True,
    _if=lambda: bootstrap_node() and not _token_current(JOIN_CMD_MASTER),
)

server.shell(
    name=f"[{host.name}] Write control-plane join command to {JOIN_CP_CMD_MASTER}",
    commands=[
        (
            "certkey=$(kubeadm init phase upload-certs --upload-certs 2>/dev/null "
            "| grep -oP 'certificate key is: \\K\\S+' | tail -n1); "
            f"kubeadm token create --print-join-command --control-plane "
            f"--certificate-key \"$certkey\" > {JOIN_CP_CMD_MASTER}"
        )
    ],
    _sudo=True,
    _if=lambda: topo.ha
    and bootstrap_node()
    and not _token_current(JOIN_CP_CMD_MASTER),
)
