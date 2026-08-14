"""Provision the internal repo mirror VM (k8s-repo) with nginx + dnf reposync.

Mirrors the upstream RPM repos (pkgs.k8s.io Kubernetes, download.docker.com
containerd) into /var/www/repos and serves them over plain HTTP on :80. The
first sync runs at provision time; repo-sync.timer re-runs it daily.

k8s/worker nodes consume this mirror via templates/kubernetes.repo.j2 and
templates/docker-ce.repo.j2 (see deploy/prepare.py).

Run:
    uv run pyinfra -y inventory.py deploy/repo.py --limit k8s_repo \
        --user admin --key ~/.ssh/id_ed25519
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pyinfra.operations import files, server

import config

MIRROR_ROOT = "/var/www/repos"
K8S_DEST = f"{MIRROR_ROOT}/{config.K8S_REPO_SERVED_PATH}"
DOCKER_DEST = f"{MIRROR_ROOT}/{config.DOCKER_REPO_SERVED_PATH}"
K8S_SRC_BASE = f"https://pkgs.k8s.io/core:/stable:/v{config.K8S_MINOR}/rpm"
DOCKER_SRC_BASE = "https://download.docker.com/linux/centos/10/x86_64/stable"

SYNC_SCRIPT = "/usr/local/bin/repo-sync.sh"
MIRROR_TEMPLATES = config.REPO_ROOT / "templates" / "mirror"

# 1. Packages (reposync comes from dnf-plugins-core on AlmaLinux 10's dnf4)
server.packages(
    name="Install nginx, createrepo_c, dnf reposync and semanage",
    packages=[
        "nginx",
        "createrepo_c",
        "dnf-plugins-core",
        "policycoreutils-python-utils",
    ],
    _sudo=True,
)

# 2. Upstream source repos — used ONLY by reposync on the mirror (packages are
# downloaded unsigned; signatures are preserved in the RPMs themselves).
files.template(
    name="Write upstream mirror-sources.repo",
    src=str(MIRROR_TEMPLATES / "mirror-sources.repo.j2"),
    dest="/etc/yum.repos.d/mirror-sources.repo",
    k8s_base=K8S_SRC_BASE,
    docker_base=DOCKER_SRC_BASE,
    _sudo=True,
)

# 3. Sync script (idempotent; also invoked by the systemd timer)
files.template(
    name="Write repo-sync.sh",
    src=str(MIRROR_TEMPLATES / "repo-sync.sh.j2"),
    dest=SYNC_SCRIPT,
    mode="755",
    mirror_root=MIRROR_ROOT,
    k8s_dest=K8S_DEST,
    docker_dest=DOCKER_DEST,
    k8s_minor=config.K8S_MINOR,
    _sudo=True,
)

# 4. nginx server block + SELinux relabel so nginx may serve /var/www/repos
files.template(
    name="Write nginx repo server block",
    src=str(MIRROR_TEMPLATES / "nginx-repo.conf.j2"),
    dest="/etc/nginx/conf.d/repo.conf",
    mirror_root=MIRROR_ROOT,
    _sudo=True,
)
server.shell(
    name="Relabel /var/www/repos for nginx (SELinux)",
    commands=[
        "semanage fcontext -a -t httpd_sys_content_t '/var/www/repos(/.*)?' || true",
        "restorecon -R /var/www/repos 2>/dev/null || true",
    ],
    _sudo=True,
)

# 5. Periodic sync via systemd timer
files.template(
    name="Write repo-sync.service",
    src=str(MIRROR_TEMPLATES / "repo-sync.service.j2"),
    dest="/etc/systemd/system/repo-sync.service",
    _sudo=True,
)
files.template(
    name="Write repo-sync.timer",
    src=str(MIRROR_TEMPLATES / "repo-sync.timer.j2"),
    dest="/etc/systemd/system/repo-sync.timer",
    _sudo=True,
)
server.shell(
    name="Enable repo-sync.timer",
    commands=[
        "systemctl daemon-reload",
        "systemctl enable --now repo-sync.timer",
    ],
    _sudo=True,
)

# 6. First sync. Long-running on a fresh mirror (downloads the whole docker
# stable repo); skipped RPMs make re-runs fast.
server.shell(
    name="Run initial repo sync",
    commands=[SYNC_SCRIPT],
    _sudo=True,
)

# 7. Serve it (nginx.conf's stock default server must not shadow our block, so
# reload after writing config)
server.service(
    name="Enable and start nginx",
    service="nginx",
    running=True,
    enabled=True,
    _sudo=True,
)
server.shell(
    name="Reload nginx to pick up repo.conf",
    commands=["nginx -t && systemctl reload nginx || true"],
    _sudo=True,
)

# 8. Sanity check that the k8s repo metadata is served over HTTP
server.shell(
    name="Verify repodata is reachable over HTTP",
    commands=[
        ("curl -fsS -o /dev/null -w '%{http_code}\\n' "
         "http://127.0.0.1/"
         f"{config.K8S_REPO_SERVED_PATH}/repodata/repomd.xml")
    ],
)