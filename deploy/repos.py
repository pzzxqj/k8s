"""Repos-only orchestrator: point a server's dnf sources where its data says.

Environment-agnostic — there is no "test" or "production" branch here. The
atomic repo tasks read the source bases from group data (k8s_test.py = upstream,
k8s_production.py = intranet mirror) and the per-host ``repos`` subset from Host
Data. Any server (including non-k8s hosts that only want the Alma source) can be
added to an inventory with, say, ``"repos": ["alma"]`` and managed with this.

Run against any file-based inventory:
    uv run pyinfra -y inventories/k8s_production.py deploy/repos.py
    uv run pyinfra -y inventories/k8s_production.py deploy/repos.py --limit control_plane
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra import local

local.include("tasks/alma_repos.py")
local.include("tasks/kubernetes_repo.py")
local.include("tasks/docker_ce_repo.py")