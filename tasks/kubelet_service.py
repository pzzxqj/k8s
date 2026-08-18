"""Atomic task: enable kubelet at boot (start happens at init/join).

running=None: enable only, never touch the running state, so re-running
prepare never stops a kubelet already serving a joined node.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.operations import systemd

systemd.service(
    name=f"[{host.name}] Enable kubelet at boot",
    service="kubelet",
    enabled=True,
    running=None,  # pyright: ignore[reportArgumentType] - enable only
    _sudo=True,
)