"""Initialize the control-plane node (k8s-master) with kubeadm + Cilium.

Run ONLY against the master, after deploy/prepare.py:

    uv run pyinfra -y inventory.py deploy/init.py --limit k8s_master \
        --user admin --key ~/.ssh/id_ed25519

Idempotent: re-runs are no-change. kubeadm init is skipped once
/etc/kubernetes/admin.conf exists; the admin kubeconfig and Cilium CLI are only
(re)installed when they drift; `cilium install` and the join-command write are
gated on facts, so the join token is only rotated when the stored one has
expired. The worker join command is written to
/etc/kubernetes/join-command.txt so the host can fetch it (see `just join`)
and feed it to deploy/join.py.
"""

import json
import re
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

JOIN_CMD_MASTER = "/etc/kubernetes/join-command.txt"

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


# Idempotency predicates, evaluated by `_if` at execute time (pyinfra facts are
# cached per run, so each check reflects the remote state at that point).


def admin_kubeconfig_current() -> bool:
    """True when ~{SSH_USER}/.kube/config is an up-to-date copy of admin.conf."""
    dst = f"/home/{config.SSH_USER}/.kube/config"
    return (
        host.get_fact(
            Command,
            f"sudo test -f {dst} && sudo cmp -s {_common.ADMIN_CONF} {dst} && echo yes || echo no",
        )
        or ""
    ).strip() == "yes"


def cilium_cli_current() -> bool:
    """True when /usr/local/bin/cilium matches the offline bundle's binary.

    files.copy with overwrite=True always re-copies (no checksum diffing), so
    gate it on this predicate to keep converged re-runs a no-change.
    """
    src = f"{config.NODE_OFFLINE_DIR}/cilium/cilium"
    dst = "/usr/local/bin/cilium"
    return (
        host.get_fact(
            Command,
            f"sudo test -f {dst} && sudo cmp -s {src} {dst} && echo yes || echo no",
        )
        or ""
    ).strip() == "yes"


def cilium_installed() -> bool:
    """True when the Cilium agent DaemonSet is already deployed."""
    return (
        host.get_fact(
            Command,
            "kubectl -n kube-system get daemonset cilium >/dev/null 2>&1 && echo yes || echo no",
        )
        or ""
    ).strip() == "yes"


def join_command_current() -> bool:
    """True when join-command.txt holds a bootstrap token kubeadm still lists."""
    raw = (
        host.get_fact(Command, f"sudo cat {JOIN_CMD_MASTER} 2>/dev/null || true")
        or ""
    )
    m = re.search(r"--token\s+(\S+)", raw)
    if not m:
        return False
    listed = (
        host.get_fact(
            Command,
            f"sudo kubeadm token list 2>/dev/null | grep -qF -- {m.group(1)} && echo yes || echo no",
        )
        or ""
    ).strip()
    return listed == "yes"


k8s_version = installed_k8s_version()
cluster_ready = _common.safe_file_exists(_common.ADMIN_CONF)

# 1. kubeadm configuration
files.template(
    name="Write kubeadm init configuration",
    src=str(config.REPO_ROOT / "templates" / "kubeadm.yaml.j2"),
    dest=_common.KUBEADM_YAML,
    k8s_version=k8s_version,
    master_ip=config.MASTER_IP,
    apiserver_port=config.APISERVER_PORT,
    service_subnet=config.SERVICE_SUBNET,
    _sudo=True,
)

# 2. Bootstrap the control plane (once). kube-proxy is NOT installed
# (--skip-phases=addon/kube-proxy): Cilium fully replaces it via eBPF
# (see step 5). Leaving kube-proxy in place alongside Cilium's netfilter
# rules is what previously broke node egress when kube-proxy ran in
# nftables mode (README "常见问题").
server.shell(
    name="Run kubeadm init, skipping the kube-proxy addon (control plane bootstrap)",
    commands=["kubeadm init --config /etc/kubernetes/kubeadm.yaml --skip-phases=addon/kube-proxy"],
    _sudo=True,
    _if=lambda: not cluster_ready,
)

# 3. Give the SSH user admin kubeconfig. The .kube dir is declared declaratively;
# the 0600 root-only admin.conf copy has no declarative remote-rename equivalent,
# so the install shell is gated on a content cmp — a converged run skips it.
files.directory(
    name=f"Ensure ~{config.SSH_USER}/.kube exists",
    path=f"/home/{config.SSH_USER}/.kube",
    user=config.SSH_USER,
    group=config.SSH_USER,
    mode="700",
    _sudo=True,
)
server.shell(
    name=f"Install admin kubeconfig for {config.SSH_USER}",
    commands=[
        (
            f"install -o {config.SSH_USER} -g {config.SSH_USER} -m 600 "
            f"{_common.ADMIN_CONF} /home/{config.SSH_USER}/.kube/config"
        ),
    ],
    _sudo=True,
    _if=lambda: not admin_kubeconfig_current(),
)

# 4. Install Cilium CLI from the offline bundle (files.copy with overwrite=True
# re-copies unconditionally, so a checksum gate keeps converged runs a no-change
# instead of re-copying the 151MB binary on every run).
files.copy(
    name="Install Cilium CLI to /usr/local/bin",
    src=f"{config.NODE_OFFLINE_DIR}/cilium/cilium",
    dest="/usr/local/bin/",
    overwrite=True,
    _sudo=True,
    _if=lambda: not cilium_cli_current(),
)

# 5. Install Cilium CNI (offline: chart is local, images already preloaded).
# Run as {config.SSH_USER} so the CLI picks up ~/.kube/config; the installed
# DaemonSet check is gated via `_if` (evaluated at execute time).
# useDigest=false: the 1.20 chart pins image digests (@sha256:), but our bundle
# imports images by tag only (docker save + ctr import registers tags, not repo
# digests), so containerd can't resolve the digest refs offline -> ImagePullBackOff.
# kubeProxyReplacement=true + k8sServiceHost/Port = full eBPF service LB with NO
# kube-proxy (kubeadm skipped its addon above). The explicit API server
# host/port is required because nothing would otherwise DNAT the 10.96.0.1
# "kubernetes" ClusterIP at bootstrap time (that's kube-proxy's usual job;
# Cilium only learns it from these flags before its own BPF LB is live).
server.shell(
    name="Install Cilium CNI from local chart (kube-proxy free)",
    commands=[
        (
            "cilium install "
            "--chart-directory /opt/k8s-offline/cilium/chart "
            "--set kubeProxyReplacement=true "
            "--set k8sServiceHost=" + config.MASTER_IP + " "
            "--set k8sServicePort=" + config.APISERVER_PORT + " "
            "--set nodePort.enabled=true "
            "--set hostPort.enabled=true "
            "--set operator.replicas=1 "
            "--set hubble.enabled=false "
            "--set image.useDigest=false "
            "--set operator.image.useDigest=false "
            "--set envoy.image.useDigest=false"
        ),
    ],
    _if=lambda: not cilium_installed(),
)

# 6. Write the worker join command (host fetches it via `just join`). Only
# regenerated when the file is missing or its bootstrap token has expired, so
# re-running init.py does not rotate the token every time.
server.shell(
    name="Write worker join command to /etc/kubernetes/join-command.txt",
    commands=[
        f"kubeadm token create --print-join-command > {JOIN_CMD_MASTER}",
    ],
    _sudo=True,
    _if=lambda: not join_command_current(),
)
