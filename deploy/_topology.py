"""Controller-side topology helper: derive the cluster shape from the inventory.

pyinfra adds every host from ``inventories/<name>.py`` to the auto group
``<name>``. This module self-locates that file from ``host.groups``, runpy-loads
it and derives the cluster-level facts the deploy/task scripts need:

  * which control-plane host bootstraps kubeadm (the FIRST entry),
  * whether the control plane is HA (more than one entry),
  * the shared controlPlaneEndpoint: the Keepalived VIP from group data when the
    control plane is HA, else the bootstrap master's ssh_hostname (classic
    single-master mode, byte-for-byte the previous behaviour),
  * the Keepalived priority (control_plane list position) and the HAProxy
    backend list (every control-plane ssh_hostname).

The inventory file is the only source of truth — nothing here reads config.VMS
or the VM-provisioning code, so VM creation and k8s deployment stay decoupled.
"""

from __future__ import annotations

import os
import runpy
import sys
from dataclasses import dataclass
from pathlib import Path

from pyinfra.context import host
from pyinfra.facts.server import Command

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@dataclass(frozen=True)
class Topology:
    inventory_file: Path | None
    control_plane: tuple[str, ...]
    workers: tuple[str, ...]
    ips: dict[str, str]

    @property
    def bootstrap(self) -> str | None:
        return self.control_plane[0] if self.control_plane else None

    @property
    def ha(self) -> bool:
        return len(self.control_plane) > 1

    @property
    def n_masters(self) -> int:
        return len(self.control_plane)

    @property
    def n_workers(self) -> int:
        return len(self.workers)

    def is_bootstrap(self, name: str) -> bool:
        return name == self.bootstrap

    def master_ip(self, name: str) -> str:
        return self.ips.get(name, name)

    def control_plane_ips(self) -> list[tuple[str, str]]:
        return [(n, self.ips.get(n, n)) for n in self.control_plane]

    def keepalived_priority(self, name: str) -> int:
        if name not in self.control_plane:
            return 0
        return 200 - self.control_plane.index(name) * 50

    def vrrp_peer_ips(self, name: str) -> list[str]:
        my_ip = self.ips.get(name, name)
        return [ip for _, ip in self.control_plane_ips() if ip != my_ip]

    @property
    def endpoint_ip(self) -> str:
        """The shared apiserver endpoint IP.

        With an HA control plane the group-data `control_plane_endpoint` (the
        Keepalived VIP) wins; otherwise fall back to the bootstrap master's IP so
        a single control-plane node behaves exactly like the original design.
        """
        if self.ha and host.data.get("control_plane_endpoint"):
            return str(host.data.get("control_plane_endpoint"))
        if self.bootstrap:
            return self.ips.get(self.bootstrap, self.bootstrap)
        return ""

    @property
    def endpoint(self) -> str:
        port = host.data.get("apiserver_port", "6443")
        return f"{self.endpoint_ip}:{port}"


def inventory_file() -> Path | None:
    """Self-locate the inventory file via the pyinfra auto group."""
    for group in host.groups:
        path = Path("inventories") / f"{group}.py"
        if path.is_file():
            return path
    env = os.environ.get("K8S_INVENTORY")
    if env:
        return Path(env)
    return None


def _entry_name(entry) -> str:
    return entry[0] if isinstance(entry, tuple) and len(entry) == 2 else str(entry)


def _entry_ip(entry) -> str:
    if isinstance(entry, tuple) and len(entry) == 2:
        return str(entry[1].get("ssh_hostname", entry[0]))
    return str(entry)


def topology() -> Topology:
    inv = inventory_file()
    ns = runpy.run_path(str(inv)) if inv else {}
    cp_entries = ns.get("control_plane") or []
    wk_entries = ns.get("workers") or []
    control_plane = tuple(_entry_name(e) for e in cp_entries)
    workers = tuple(_entry_name(e) for e in wk_entries)
    ips = {}
    for e in [*cp_entries, *wk_entries]:
        ips[_entry_name(e)] = _entry_ip(e)
    return Topology(
        inventory_file=inv,
        control_plane=control_plane,
        workers=workers,
        ips=ips,
    )


def vrrp_interface() -> str:
    """The interface Keepalived should bind: host data override, else the
    default-route interface (auto-detected, so Incus enp5s0 / bare-metal ethX
    both work without per-env config)."""
    override = host.data.get("vrrp_interface")
    if override:
        return str(override)
    detected = (host.get_fact(Command, "ip route | awk '/default/{print $5; exit}'") or "").strip()
    return detected or "eth0"
