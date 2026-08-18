"""Shared subprocess helpers for Incus-driven scripts.

Thin wrappers used by incus/incus_vms.py so the ``incus ...`` plumbing lives in
one place.

The underscore name marks this as an internal module (same convention as
deploy/_common.py); it is not a CLI script.
"""

import subprocess


def run(cmd: list[str], *, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, check=check, **kwargs)


def instance_exists(name: str) -> bool:
    """True when an Incus instance (VM/container) with this name exists."""
    out = run(
        ["incus", "list", "--format=compact", name],
        check=False,
        capture_output=True,
    ).stdout
    return bool(out) and name in out


def instance_running(name: str) -> bool:
    """True when the named Incus instance exists and reports RUNNING."""
    out = run(
        ["incus", "list", "--format=compact", name],
        check=False,
        capture_output=True,
    ).stdout
    return bool(out) and "RUNNING" in out
