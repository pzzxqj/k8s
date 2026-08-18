"""Atomic task: put SELinux in permissive mode (config + live mode)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.facts.files import FindInFile
from pyinfra.facts.server import Command
from pyinfra.operations import files, server

from deploy import _common

files.line(
    name=f"[{host.name}] Set SELINUX=permissive in /etc/selinux/config",
    path=_common.SELINUX_CONFIG,
    line="^SELINUX=enforcing$",
    replace="SELINUX=permissive",
    present=True,
    _if=lambda: bool(
        host.get_fact(
            FindInFile, path=_common.SELINUX_CONFIG, pattern="^SELINUX=enforcing$"
        )
    ),
    _sudo=True,
)
server.shell(
    name=f"[{host.name}] Set the current SELinux mode to permissive",
    commands=["setenforce 0"],
    # Selinux fact only reports enabled/disabled (sestatus), not the enforcing
    # mode — probe the current mode so a converged run no-ops.
    _if=lambda: (
        (host.get_fact(Command, "getenforce 2>/dev/null || echo unknown") or "unknown").strip()
        == "Enforcing"
    ),
    _sudo=True,
)