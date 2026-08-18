"""Single source of truth for the intranet RPM mirror (real env).

Deliberately independent of the k8s lab's config.py: the mirror is a general
intranet RPM repository (nginx + lftp/dnf-reposync), serving any internal
client — the k8s clusters just happen to consume it. The lab config imports
the shared upstream constants from here (see ../config.py).

Deploy:
    uv run pyinfra -y {MIRROR_HOST} mirror/repo.py --user {MIRROR_USER}

(No --key: authentication goes through the local ssh-agent / ssh_config.)
"""

# ---------- mirror machine (real env; ssh alias `mirror`) ----------
MIRROR_HOST = "192.168.90.201"
MIRROR_USER = "zhch"
# nginx document root + SELinux httpd_sys_content_t target.
MIRROR_ROOT = "/srv/repos"

# ---------- upstream sources the mirror syncs ----------
# The sync scripts (mirror/scripts/*.sh) carry their own environment-overridable
# shell defaults; these constants are the canonical Python-side values, shared
# with the k8s lab nodes that now point straight at the upstreams.
K8S_MINOR = "1.36"
K8S_UPSTREAM_BASE = f"https://pkgs.k8s.io/core:/stable:/v{K8S_MINOR}/rpm"
DOCKER_UPSTREAM_BASE = "https://download.docker.com/linux/centos/10/x86_64/stable"
ALMA_UPSTREAM_BASE = "https://mirrors.nju.edu.cn/almalinux"