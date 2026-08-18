# pyinfra group data: learning environment (inventories/learning.py).
#
# The learning env has NO internal mirror: its "mirror source" IS the upstream,
# so the repo tasks point straight at NJU / pkgs.k8s.io / download.docker.com.
# See mirror/config.py for the canonical upstream constants.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

alma_base = config.ALMA_UPSTREAM_BASE  # https://mirrors.nju.edu.cn/almalinux
k8s_repo_base = config.K8S_UPSTREAM_BASE  # https://pkgs.k8s.io/core:/stable:/v1.36/rpm
docker_repo_base = config.DOCKER_UPSTREAM_BASE  # https://download.docker.com/linux/centos/10/x86_64/stable
# Docker keeps the gpg key one level above the versioned stable tree
# (.../linux/centos/gpg); the mirror preserves that layout. Deriving it from the
# repo base keeps the host consistent with whichever docker upstream is used.
docker_gpg_key = f"{config.DOCKER_UPSTREAM_BASE.split('/10/', 1)[0]}/gpg"