# pyinfra inventory: k8s lab nodes (AlmaLinux 10 VMs on incusbr0).
# Groups k8s_master / k8s_workers / k8s_nodes derive from config.py.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ALL_NODES, MASTER_IP, WORKER_IPS

k8s_nodes = ALL_NODES
k8s_master = [MASTER_IP]
k8s_workers = WORKER_IPS