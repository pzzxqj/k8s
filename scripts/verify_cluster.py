#!/usr/bin/env python3
"""Controller-side cluster verification using the official kubernetes client.

Reads a kubeconfig (default: <repo>/offline/admin.conf, fetched from the master
by `just verify`; override with --kubeconfig) and checks the lab's invariants:

  * every node is Ready (with its kubelet version),
  * the Cilium agent DaemonSet is fully scheduled and ready,
  * there is NO kube-proxy DaemonSet (Cilium eBPF replaces it, kube-proxy free),
  * every kube-system pod is Running or Succeeded,
  * the coredns Deployment is fully available.

Exits non-zero when any check fails.

    uv run python scripts/verify_cluster.py
    uv run python scripts/verify_cluster.py --kubeconfig /path/admin.conf
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from urllib3.exceptions import HTTPError as Urllib3HTTPError
from yaml import YAMLError

import config as project_config


def check(ok: bool, msg: str, failures: list[str]) -> bool:
    print(("  [ok]   " if ok else "  [FAIL] ") + msg)
    if not ok:
        failures.append(msg)
    return ok


# ---------------------------------------------------------------------------
# Adapter layer: kubernetes API objects (dynamically generated, untyped) ->
# plain JSON-able dicts, so the check functions below are pure and testable
# against plain fixtures. `core`/`apps` are annotated Any to keep the dynamic
# model noise out of pyright's inference.
# ---------------------------------------------------------------------------


def _camel(name: str) -> str:
    """snake_case -> camelCase (e.g. desired_number_scheduled -> desiredNumberScheduled)."""
    head, _, tail = name.partition("_")
    return head + tail.replace("_", " ").title().replace(" ", "")


def _camel_keys(obj: Any) -> Any:
    """Recursively convert dict keys to the k8s API's camelCase JSON shape.

    to_dict() emits snake_case keys, while the API JSON (and the plain-dict
    fixtures the checks are tested against) uses camelCase — normalize so the
    check functions see exactly what the API returns.
    """
    if isinstance(obj, dict):
        return {_camel(k): _camel_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_camel_keys(item) for item in obj]
    return obj


def _to_dicts(objs) -> list[dict]:
    return [_camel_keys(json.loads(json.dumps(o.to_dict(), default=str))) for o in objs]


def _nodes(core: Any) -> list[dict]:
    return _to_dicts(core.list_node().items)


def _daemonsets(apps: Any) -> list[dict]:
    return _to_dicts(apps.list_namespaced_daemon_set(namespace="kube-system").items)


def _deployments(apps: Any) -> list[dict]:
    return _to_dicts(apps.list_namespaced_deployment(namespace="kube-system").items)


def _pods(core: Any) -> list[dict]:
    return _to_dicts(core.list_namespaced_pod(namespace="kube-system").items)


# ---------------------------------------------------------------------------
# Checks (pure; operate on plain dicts)
# ---------------------------------------------------------------------------


def _node_ready(node: dict) -> bool:
    for cond in node.get("status", {}).get("conditions", []):
        if cond.get("type") == "Ready":
            return cond.get("status") == "True"
    return False


def _node_internal_ip(node: dict) -> str:
    for addr in node.get("status", {}).get("addresses", []):
        if addr.get("type") == "InternalIP":
            return addr.get("address") or "?"
    return "?"


def _pod_problem(pod: dict) -> str:
    """Human reason for a non-Running/Succeeded pod, if any."""
    for status in pod.get("status", {}).get("containerStatuses", []):
        waiting = status.get("state", {}).get("waiting") if status.get("state") else None
        if waiting and waiting.get("reason"):
            return (
                f"{status.get('name')}: {waiting['reason']} "
                f"({waiting.get('message') or ''})"
            ).strip()
    return pod.get("status", {}).get("phase") or "?"


def check_nodes(nodes: list[dict], failures: list[str]) -> None:
    print(f"== nodes ({len(nodes)}) ==")
    for node in sorted(nodes, key=_node_internal_ip):
        name = node["metadata"]["name"]
        ip = _node_internal_ip(node)
        kver = node.get("status", {}).get("nodeInfo", {}).get("kubeletVersion") or "?"
        check(_node_ready(node), f"{name} ({ip}) Ready, kubelet {kver}", failures)


def check_daemonsets(dss: list[dict], failures: list[str]) -> None:
    print("== kube-system workloads ==")
    daemonsets = {ds["metadata"]["name"]: ds for ds in dss}

    cilium = daemonsets.get("cilium")
    if cilium is not None:
        desired = cilium.get("status", {}).get("desiredNumberScheduled") or 0
        ready = cilium.get("status", {}).get("numberReady") or 0
        check(
            desired > 0 and ready == desired,
            f"cilium DaemonSet ready ({ready}/{desired})",
            failures,
        )
    else:
        check(False, "cilium DaemonSet is missing in kube-system", failures)

    check(
        "kube-proxy" not in daemonsets,
        "no kube-proxy DaemonSet (kube-proxy free)",
        failures,
    )


def check_deployments(deployments: list[dict], failures: list[str]) -> None:
    by_name = {dep["metadata"]["name"]: dep for dep in deployments}
    coredns = by_name.get("coredns")
    if coredns is not None:
        replicas = coredns.get("status", {}).get("replicas") or 0
        available = coredns.get("status", {}).get("availableReplicas") or 0
        check(
            replicas > 0 and available == replicas,
            f"coredns Deployment available ({available}/{replicas})",
            failures,
        )
    else:
        check(False, "coredns Deployment is missing in kube-system", failures)


def check_pods(pods: list[dict], failures: list[str]) -> None:
    bad = [
        pod for pod in pods if pod.get("status", {}).get("phase") not in ("Running", "Succeeded")
    ]
    check(
        not bad,
        f"kube-system pods healthy ({len(pods) - len(bad)}/{len(pods)} Running/Succeeded)",
        failures,
    )
    for pod in bad:
        print(f"        - {pod['metadata']['name']}: {_pod_problem(pod)}")


# ---------------------------------------------------------------------------
# main (assembly only)
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kubeconfig",
        default=str(Path(project_config.OFFLINE_DIR) / "admin.conf"),
        help="kubeconfig file (default: <repo>/offline/admin.conf)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    kubeconfig = Path(args.kubeconfig)
    if not kubeconfig.is_file():
        sys.exit(f"[error] {kubeconfig} not found - run `just verify` or point --kubeconfig")

    try:
        config.load_kube_config(config_file=str(kubeconfig))
        core: Any = client.CoreV1Api()
        apps: Any = client.AppsV1Api()
    except (ConfigException, YAMLError) as e:
        sys.exit(f"[error] could not load kubeconfig {kubeconfig}: {e}")

    failures: list[str] = []
    print(f"== cluster check via {kubeconfig.name} ==")

    try:
        check_nodes(_nodes(core), failures)
        check_daemonsets(_daemonsets(apps), failures)
        check_deployments(_deployments(apps), failures)
        check_pods(_pods(core), failures)
    except (ApiException, Urllib3HTTPError) as e:
        sys.exit(f"[error] cluster query failed (is the API server up?): {e}")

    if failures:
        print(f"\n== FAILED: {len(failures)} check(s) ==")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\n== all checks passed ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
