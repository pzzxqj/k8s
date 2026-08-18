"""Atomic task (control plane): write the worker join command.

Only regenerated when the file is missing or its bootstrap token has expired,
so re-running init does not rotate the token every time. The host fetches it
via `just join` into $OFFLINE_DIR/join-command.txt.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.facts.server import Command
from pyinfra.operations import server

JOIN_CMD_MASTER = "/etc/kubernetes/join-command.txt"


def join_command_current() -> bool:
    raw = host.get_fact(Command, f"sudo cat {JOIN_CMD_MASTER} 2>/dev/null || true") or ""
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


server.shell(
    name=f"[{host.name}] Write worker join command to {JOIN_CMD_MASTER}",
    commands=[f"kubeadm token create --print-join-command > {JOIN_CMD_MASTER}"],
    _sudo=True,
    _if=lambda: not join_command_current(),
)