"""Atomic task: preload the container images (offline bundle) into containerd.

The bundle was already rsynced to /opt/k8s-offline by
scripts/k8s_download_offline.py (upload needs no pyinfra). Control-plane nodes get
the control-plane-only images too; workers just the common set.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.facts.files import FileContents
from pyinfra.facts.server import Command
from pyinfra.operations import files, server

import config
from deploy import _common

offline_dir = host.data.get("node_offline_dir", config.NODE_OFFLINE_DIR)


def images_imported() -> bool:
    """True when every image this node needs is already in containerd."""
    scope = "master" if _common.is_control_plane() else "all"
    plan = host.get_fact(FileContents, path=f"{offline_dir}/images/import-plan.txt") or []
    wanted: set[str] = set()
    for line in plan:
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        s, ref = parts
        if s == "all" or (s == "master" and scope == "master"):
            wanted.add(ref)
    if not wanted:
        return False
    present = set(
        (
            host.get_fact(
                Command, "sudo ctr -n k8s.io images ls -q 2>/dev/null || true"
            )
            or ""
        ).split()
    )
    return wanted <= present


files.put(
    name=f"[{host.name}] Upload image import helper",
    src=str(config.REPO_ROOT / "scripts" / "k8s_import_images.sh"),
    dest=f"{offline_dir}/import_images.sh",
    mode="755",
    _sudo=True,
)
server.shell(
    name=f"[{host.name}] Preload container images into containerd",
    commands=[
        (
            f"bash {offline_dir}/import_images.sh "
            f"{'master' if _common.is_control_plane() else 'all'}"
        )
    ],
    _sudo=True,
    _if=lambda: not images_imported(),
)