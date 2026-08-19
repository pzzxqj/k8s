# pyinfra inventory: k8s TEST cluster (Incus lab, 10.98.68.x).

# Derived from config.py (single source of truth for the topology): the IPs
# here come straight from config.MASTER_IP / config.WORKER_IPS, so scaling the
# cluster is a one-file change (config.py) — the inventory follows
# automatically. Host NAME is the logical label (pyinfra.host.name); connection
# details are Host DATA via the SSH connector keys (ssh_hostname / ssh_user)
# plus our own data (repos: which dnf source files this host manages). The three
# repo tasks (tasks/alma_repos.py, tasks/kubernetes_repo.py,
# tasks/docker_ce_repo.py) default to all three sources; drop entries here to
# skip a source per host.

# In the k8s test environment the "intranet mirror" IS the upstream: repos are
# pointed straight at NJU / pkgs.k8s.io / download.docker.com (see
# group_data/k8s_test.py). The test env has no internal mirror.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

REPOS = ["alma", "kubernetes", "docker-ce"]


def _host(name: str, ip: str) -> tuple:
    return (name, {"ssh_hostname": ip, "ssh_user": config.SSH_USER, "repos": REPOS})


control_plane = [_host("k8s-master", config.MASTER_IP)]
workers = [_host(f"k8s-worker-{i + 1}", ip) for i, ip in enumerate(config.WORKER_IPS)]

nodes = control_plane + workers