"""Atomic task: sysctl settings required by kubelet / CNI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.operations import server

from deploy import _common

sysctl_params = {
    "net.bridge.bridge-nf-call-iptables": 1,
    "net.bridge.bridge-nf-call-ip6tables": 1,
    "net.ipv4.ip_forward": 1,
}
for key, value in sysctl_params.items():
    server.sysctl(
        name=f"[{host.name}] Ensure sysctl {key} = {value}",
        key=key,
        value=value,
        persist=True,
        persist_file=_common.SYSCTL_CONF,
        _sudo=True,
    )