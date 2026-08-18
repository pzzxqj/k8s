"""Atomic task: manage /etc/yum.repos.d/kubernetes.repo.

The repo is fully managed: when the host's ``repos`` data includes "kubernetes"
the file is rendered (templates/kubernetes.repo.j2) against ``k8s_repo_base``
(group data); otherwise the managed file is removed. ``dnf clean all`` runs only
when the file actually changed.

Subset handling is per-host (via ``_if``), never a module-level branch, so one
task file serves the k8s test cluster (upstream pkgs.k8s.io) and k8s production
(mirror) and mixed hosts within an inventory.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.operations import files, server

import config

DEST = "/etc/yum.repos.d/kubernetes.repo"
k8s_repo_base = host.data.k8s_repo_base


def wanted() -> bool:
    return "kubernetes" in (host.data.get("repos") or [])


push = files.template(
    name=f"[{host.name}] render kubernetes.repo -> {k8s_repo_base}",
    src=str(config.REPO_ROOT / "templates" / "kubernetes.repo.j2"),
    dest=DEST,
    repo_base=k8s_repo_base,
    _sudo=True,
    _if=wanted,
)
remove = files.file(
    name=f"[{host.name}] remove kubernetes.repo (not in repos subset)",
    path=DEST,
    present=False,
    _sudo=True,
    _if=lambda: not wanted(),
)

server.shell(
    name=f"[{host.name}] dnf clean all (kubernetes.repo changed)",
    commands=["dnf clean all"],
    _sudo=True,
    _if=lambda: push.did_change() or remove.did_change(),
)