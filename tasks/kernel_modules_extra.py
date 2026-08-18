"""Atomic task: pin kernel-modules-extra to the RUNNING kernel.

`dnf install kernel-modules-extra` would otherwise pull the newest patch whose
module tree doesn't match the booted kernel, leaving br_netfilter unloadable.
Gated on rpm so a converged node skips the dnf call.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.facts.server import Command
from pyinfra.operations import server

running_kernel = (host.get_fact(Command, "uname -r") or "").strip()


def rpm_db_has(pkg: str) -> bool:
    return (
        host.get_fact(
            Command, f"rpm -q {pkg} >/dev/null 2>&1 && echo yes || echo no"
        )
        or ""
    ).strip() == "yes"


server.shell(
    name=f"[{host.name}] Ensure kernel-modules-extra matches running kernel ({running_kernel})",
    commands=[f"dnf install -y kernel-modules-extra-{running_kernel}"],
    _sudo=True,
    _if=lambda: not rpm_db_has(f"kernel-modules-extra-{running_kernel}"),
)