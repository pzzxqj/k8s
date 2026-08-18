"""Atomic task: install + configure containerd (systemd cgroup driver).

container-selinux (a hard dependency of the containerd.io RPM) comes from the
Alma repos and is installed first. containerd is left running + enabled; the
kubelet start happens at init/join.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.operations import dnf, files, server

import config
from deploy import _common

dnf.packages(
    name=f"[{host.name}] Ensure container-selinux is installed (containerd.io dep)",
    packages=["container-selinux"],
    _sudo=True,
)

dnf.packages(
    name=f"[{host.name}] Install containerd.io",
    packages=["containerd.io"],
    _sudo=True,
)

files.template(
    name=f"[{host.name}] Write containerd config with systemd cgroup driver",
    src=str(config.REPO_ROOT / "templates" / "containerd-config.toml.j2"),
    dest=_common.CONTAINERD_CONFIG,
    sandbox_image="registry.k8s.io/pause:3.10.2",
    _sudo=True,
)

server.service(
    name=f"[{host.name}] Enable and start containerd",
    service="containerd",
    running=True,
    enabled=True,
    _sudo=True,
)