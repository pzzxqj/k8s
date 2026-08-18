"""Prepare nodes for kubeadm (k8s test + production share this orchestrator).

Everything the nodes install comes from repos the repo tasks just pointed at
the right source (upstream for the k8s test cluster, intranet mirror for
production): Alma base packages, containerd.io, k8s RPMs. Container images +
Cilium come from the offline bundle at /opt/k8s-offline (pushed by
scripts/k8s_download_offline.py, rsync, no pyinfra).

Workflow:
    uv run python scripts/k8s_download_offline.py --inventory inventories/k8s_test.py
    uv run pyinfra -y inventories/k8s_test.py deploy/k8s_prepare.py
    (or inventories/k8s_production.py for the production cluster)

Idempotent: re-runs are no-change and never restart a running kubelet.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra import local

# dnf sources first (repos feed every dnf operation below).
local.include("tasks/alma_repos.py")
local.include("tasks/kubernetes_repo.py")
local.include("tasks/docker_ce_repo.py")

# Kernel / SELinux / swap preparation.
local.include("tasks/kernel_modules_extra.py")
local.include("tasks/kernel_modules.py")
local.include("tasks/sysctl.py")
local.include("tasks/swap.py")
local.include("tasks/selinux.py")

# containerd + k8s RPMs (installed from the repos configured above).
local.include("tasks/k8s_containerd.py")
local.include("tasks/k8s_rpms.py")

# Container images + runtime service.
local.include("tasks/k8s_images.py")
local.include("tasks/kubelet_service.py")