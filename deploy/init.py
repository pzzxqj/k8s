"""Initialize the control-plane node (k8s-master) with kubeadm + Cilium.

Run ONLY against the master, after deploy/prepare.py:

    uv run pyinfra -y inventory.py deploy/init.py --limit k8s_master \
        --user admin --key ~/.ssh/id_ed25519

Idempotent: skips kubeadm init once /etc/kubernetes/admin.conf exists. It also
writes the worker join command to /etc/kubernetes/join-command.txt so the host
can fetch it (see `just join`) and feed it to deploy/join.py.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common
from pyinfra.context import host
from pyinfra.facts.server import Command
from pyinfra.operations import files, server

import config

if not _common.is_master():
    print(f"[skip] not the control-plane node ({config.MASTER_HOSTNAME})")
    raise SystemExit(0)

# Resolve the running k8s version from the installed kubeadm (single source of
# truth, since RPMs now come from the internal mirror). Fall back to the
# offline bundle's k8s-version.txt if kubeadm isn't query-able yet.


def installed_k8s_version() -> str:
    raw = (
        host.get_fact(Command, "kubeadm version -o json 2>/dev/null || true") or ""
    ).strip()
    try:
        return str(json.loads(raw)["clientVersion"]["gitVersion"])
    except (ValueError, KeyError, TypeError):
        return (
            host.get_fact(
                Command, f"cat {config.NODE_OFFLINE_DIR}/k8s-version.txt"
            )
            or "v0.0.0"
        ).strip()


k8s_version = installed_k8s_version()
cluster_ready = _common.safe_file_exists(_common.ADMIN_CONF)

# 1. kubeadm configuration
files.template(
    name="Write kubeadm init configuration",
    src=str(config.REPO_ROOT / "templates" / "kubeadm.yaml.j2"),
    dest=_common.KUBEADM_YAML,
    k8s_version=k8s_version,
    master_ip=config.MASTER_IP,
    service_subnet="10.96.0.0/12",
    _sudo=True,
)

# 2. Bootstrap the control plane (once)
server.shell(
    name="Run kubeadm init (control plane bootstrap)",
    commands=["kubeadm init --config /etc/kubernetes/kubeadm.yaml"],
    _sudo=True,
    _if=lambda: not cluster_ready,
)

# 3. Give the SSH user admin kubeconfig (idempotent)
server.shell(
    name=f"Install admin kubeconfig for {config.SSH_USER}",
    commands=[
        f"install -d -o {config.SSH_USER} -g {config.SSH_USER} /home/{config.SSH_USER}/.kube",
        (
            f"install -o {config.SSH_USER} -g {config.SSH_USER} -m 600 "
            "/etc/kubernetes/admin.conf "
            f"/home/{config.SSH_USER}/.kube/config"
        ),
    ],
    _sudo=True,
)

# 4. Install Cilium CLI + chart from the offline bundle
server.shell(
    name="Install Cilium CLI to /usr/local/bin",
    commands=[f"install -m 755 {config.NODE_OFFLINE_DIR}/cilium/cilium /usr/local/bin/cilium"],
    _sudo=True,
)

# 5. Install Cilium CNI (offline: chart is local, images already preloaded).
# Run as {config.SSH_USER} so the CLI picks up ~/.kube/config. The inline check makes this
# idempotent without depending on pyinfra's fact cache.
server.shell(
    name="Install Cilium CNI from local chart",
    commands=[
        (
            "kubectl -n kube-system get daemonset cilium >/dev/null 2>&1 "
            "&& echo '[skip] Cilium already installed' "
            "|| cilium install "
            "--chart-directory /opt/k8s-offline/cilium/chart "
            "--set kubeProxyReplacement=false "
            "--set operator.replicas=1 "
            "--set hubble.enabled=false"
        ),
    ],
)

# 6. Generate the worker join command (host fetches it via `just join`)
server.shell(
    name="Write worker join command to /etc/kubernetes/join-command.txt",
    commands=[
        "kubeadm token create --print-join-command > /etc/kubernetes/join-command.txt",
    ],
    _sudo=True,
)
