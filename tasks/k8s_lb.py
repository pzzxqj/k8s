"""Atomic task (control plane, HA only): Keepalived VIP + HAProxy kube-apiserver LB.

Relevant only when the inventory declares more than one control-plane node (the
"ha" topology flag): every master runs HAProxy (frontend bound to the shared VIP
on apiserver_port -> every master's apiserver) and Keepalived VRRP (unicast,
priority derived from the control_plane list position) which owns the VIP and
only runs HAProxy on the current owner via the notify scripts, so HAProxy never
clashes with the local kube-apiserver (which binds the node's own IP after the
`bind-address` extra arg in templates/kubeadm.yaml.j2).

Single-master clusters skip every op (all gated on is_control_plane() and
topology.ha), preserving the classic no-LB layout.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.operations import dnf, files, server, systemd

import config
from deploy import _common, _topology

topo = _topology.topology()


def lb_active() -> bool:
    return _common.is_control_plane() and topo.ha


dnf.packages(
    name=f"[{host.name}] Install haproxy + keepalived",
    packages=["haproxy", "keepalived"],
    _sudo=True,
    _if=lb_active,
)

keepalived_conf = files.template(
    name=f"[{host.name}] Render keepalived.conf (VIP {topo.endpoint_ip})",
    src=str(config.REPO_ROOT / "templates" / "keepalived.conf.j2"),
    dest="/etc/keepalived/keepalived.conf",
    vrrp_interface=_topology.vrrp_interface(),
    priority=topo.keepalived_priority(host.name),
    my_ip=topo.master_ip(host.name),
    peer_ips=topo.vrrp_peer_ips(host.name),
    vip=topo.endpoint_ip,
    vip_cidr=host.data.get("vrrp_cidr", "24"),
    vrrp_pass=host.data.get("vrrp_pass", "k8svrrp1"),
    _sudo=True,
    _if=lb_active,
)

haproxy_cfg = files.template(
    name=f"[{host.name}] Render haproxy.cfg ({topo.n_masters} apiserver backends)",
    src=str(config.REPO_ROOT / "templates" / "haproxy.cfg.j2"),
    dest="/etc/haproxy/haproxy.cfg",
    vip=topo.endpoint_ip,
    apiserver_port=host.data.get("apiserver_port", config.APISERVER_PORT),
    control_plane_ips=topo.control_plane_ips(),
    _sudo=True,
    _if=lb_active,
)

files.put(
    name=f"[{host.name}] Install keepalived notify script",
    src=str(config.REPO_ROOT / "scripts" / "k8s_lb_master.sh"),
    dest="/usr/local/bin/k8s-lb-master.sh",
    mode="755",
    _sudo=True,
    _if=lb_active,
)

systemd.service(
    name=f"[{host.name}] Enable and start keepalived",
    service="keepalived",
    running=True,
    enabled=True,
    _if=lb_active,
)

server.shell(
    name=f"[{host.name}] Reload keepalived after config change",
    commands=["systemctl restart keepalived"],
    _sudo=True,
    _if=lambda: lb_active() and keepalived_conf.did_change(),
)

server.shell(
    name=f"[{host.name}] Reload haproxy after config change (if running)",
    commands=["systemctl is-active -q haproxy && systemctl restart haproxy || true"],
    _sudo=True,
    _if=lambda: lb_active() and haproxy_cfg.did_change(),
)
