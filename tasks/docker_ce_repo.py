"""Atomic task: manage /etc/yum.repos.d/docker-ce.repo.

Same contract as tasks/kubernetes_repo.py: rendered when the host's ``repos``
data includes "docker-ce", removed otherwise, ``dnf clean all`` only on change.
Data: ``docker_repo_base`` + ``docker_gpg_key`` (group data; the gpg key path
differs between upstream download.docker.com and the mirror).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.operations import files, server

import config

DEST = "/etc/yum.repos.d/docker-ce.repo"
docker_repo_base = host.data.docker_repo_base
docker_gpg_key = host.data.docker_gpg_key


def wanted() -> bool:
    return "docker-ce" in (host.data.get("repos") or [])


push = files.template(
    name=f"[{host.name}] render docker-ce.repo -> {docker_repo_base}",
    src=str(config.REPO_ROOT / "templates" / "docker-ce.repo.j2"),
    dest=DEST,
    repo_base=docker_repo_base,
    gpg_key=docker_gpg_key,
    _sudo=True,
    _if=wanted,
)
remove = files.file(
    name=f"[{host.name}] remove docker-ce.repo (not in repos subset)",
    path=DEST,
    present=False,
    _sudo=True,
    _if=lambda: not wanted(),
)

server.shell(
    name=f"[{host.name}] dnf clean all (docker-ce.repo changed)",
    commands=["dnf clean all"],
    _sudo=True,
    _if=lambda: push.did_change() or remove.did_change(),
)