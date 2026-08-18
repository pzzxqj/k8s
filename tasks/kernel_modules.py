"""Atomic task: load and persist the kernel modules kubelet/CNI need."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.operations import files, server

from deploy import _common

for module in ["overlay", "br_netfilter"]:
    files.line(
        name=f"[{host.name}] Persist kernel module {module} for boot",
        path=_common.MODULES_CONF,
        line=module,
        present=True,
        _sudo=True,
    )
    server.modprobe(
        name=f"[{host.name}] Ensure kernel module {module} is loaded",
        module=module,
        present=True,
        _sudo=True,
    )