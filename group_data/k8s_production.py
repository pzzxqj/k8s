# pyinfra group data: k8s production environment (inventories/k8s_production.py).
#
# Production points all three dnf sources at the intranet mirror
# (192.168.90.201). URLs are derived from mirror/config.py (single source of
# truth), which mirrors the exact same repositories the mirror syncs.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mirror.config import K8S_MINOR, MIRROR_HOST

alma_base = f"http://{MIRROR_HOST}/almalinux"
k8s_repo_base = f"http://{MIRROR_HOST}/kubernetes/core:/stable:/v{K8S_MINOR}/rpm"
docker_repo_base = f"http://{MIRROR_HOST}/docker-ce/linux/centos/10/x86_64/stable"
docker_gpg_key = f"http://{MIRROR_HOST}/docker-ce/linux/centos/gpg"