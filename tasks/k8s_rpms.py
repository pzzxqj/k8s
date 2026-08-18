"""Atomic task: install the k8s RPMs (kubelet/kubeadm/kubectl/cri-tools/CNI).

The RPMs resolve from the repos configured by the repo tasks
(alma/kubernetes/docker-ce): upstream for learning, intranet mirror for
production. dnf only installs missing packages (present packages are never
upgraded), so a converged re-run touches nothing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.operations import dnf

dnf.packages(
    name=f"[{host.name}] Install k8s RPMs + nftables",
    packages=[
        "kubelet",
        "kubeadm",
        "kubectl",
        "cri-tools",
        "kubernetes-cni",
        "nftables",
    ],
    _sudo=True,
)