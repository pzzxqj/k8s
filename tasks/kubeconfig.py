"""Atomic task (control plane): give the SSH user an admin kubeconfig."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.facts.server import Command
from pyinfra.operations import files, server

from deploy import _common


def admin_kubeconfig_current() -> bool:
    dst = f"/home/{_common.ssh_user()}/.kube/config"
    return (
        host.get_fact(
            Command,
            f"sudo test -f {dst} && sudo cmp -s {_common.ADMIN_CONF} {dst} && echo yes || echo no",
        )
        or ""
    ).strip() == "yes"


files.directory(
    name=f"[{host.name}] Ensure ~{_common.ssh_user()}/.kube exists",
    path=f"/home/{_common.ssh_user()}/.kube",
    user=_common.ssh_user(),
    group=_common.ssh_user(),
    mode="700",
    _sudo=True,
)
server.shell(
    name=f"[{host.name}] Install admin kubeconfig for {_common.ssh_user()}",
    commands=[
        (
            f"install -o {_common.ssh_user()} -g {_common.ssh_user()} -m 600 "
            f"{_common.ADMIN_CONF} /home/{_common.ssh_user()}/.kube/config"
        ),
    ],
    _sudo=True,
    # admin.conf only exists after kubeadm init / join --control-plane, so skip
    # hosts that have not joined yet (e.g. additional masters during `just init`).
    _if=lambda: _common.safe_file_exists(_common.ADMIN_CONF)
    and not admin_kubeconfig_current(),
)