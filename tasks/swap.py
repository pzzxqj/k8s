"""Atomic task: swap off (comment /etc/fstab entries, swapoff -a)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.facts.files import FileContents, FindInFile
from pyinfra.operations import files, server

from deploy import _common

swap_regex = r"^[^#].*swap.*$"
files.replace(
    name=f"[{host.name}] Comment out active swap lines in /etc/fstab",
    path=_common.FSTAB,
    text=rf"({swap_regex})",
    replace=r"# \1",
    extended_regex=True,
    _if=lambda: bool(
        host.get_fact(FindInFile, path=_common.FSTAB, pattern=swap_regex)
    ),
    _sudo=True,
)
server.shell(
    name=f"[{host.name}] Turn off any active swap",
    commands=["swapoff -a"],
    _sudo=True,
    # /proc/swaps always carries a header line; extra lines mean active swap
    _if=lambda: len(host.get_fact(FileContents, path="/proc/swaps") or []) > 1,
)