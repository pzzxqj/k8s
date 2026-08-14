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
ALMA_DEST = f"{MIRROR_ROOT}/{config.ALMA_SERVED_PATH}"
K8S_SRC_BASE = f"https://pkgs.k8s.io/core:/stable:/v{config.K8S_MINOR}/rpm"
DOCKER_SRC_BASE = "https://download.docker.com/linux/centos/10/x86_64/stable"

SYNC_SCRIPT = "/usr/local/bin/repo-sync.sh"
MIRROR_TEMPLATES = config.REPO_ROOT / "templates" / "mirror"

# 1. Point the VM's own AlmaLinux repos at the NJU upstream mirror. The stock
# files use a dynamic mirrorlist (mirrors.almalinux.org) plus a commented
# repo.almalinux.org baseurl; NJU is a static mirror, so we comment the
# mirrorlist and switch the commented baseurl to NJU. Only the primary section
# of each file references repo.almalinux.org, so the disabled debuginfo/source
# sections (vault.almalinux.org) are left untouched. Idempotent: once edited,
# the patterns no longer match. These repos are reused by repo-sync.sh as the
# Alma mirror source. Run before packages so the installs below also resolve
# from NJU.
server.shell(
    name="Point all AlmaLinux repos at the NJU upstream mirror",
    commands=[
        (
            "sed -i "
            "-e 's|^mirrorlist=|# mirrorlist=|' "
            f"-e 's|^# baseurl=https://repo.almalinux.org/almalinux/|baseurl={config.ALMA_UPSTREAM_BASE}/|' "
            "/etc/yum.repos.d/almalinux-*.repo"
        ),
        "dnf clean all",
    ],
    _sudo=True,
)

# 2. Packages (reposync comes from dnf-plugins-core on AlmaLinux 10's dnf4)
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

# 3. Upstream source repos — k8s + docker only. Alma sources are the VM's own
# baseos/appstream repos, repointed to NJU above (packages are downloaded
# unsigned; signatures are preserved in the RPMs themselves).
files.template(
    name="Write upstream mirror-sources.repo",
    src=str(MIRROR_TEMPLATES / "mirror-sources.repo.j2"),
    dest="/etc/yum.repos.d/mirror-sources.repo",
    k8s_base=K8S_SRC_BASE,
    docker_base=DOCKER_SRC_BASE,
    _sudo=True,
)

# 4. Sync script (idempotent; also invoked by the systemd timer)
files.template(
    name="Write repo-sync.sh",
    src=str(MIRROR_TEMPLATES / "repo-sync.sh.j2"),
    dest=SYNC_SCRIPT,
    mode="755",
    mirror_root=MIRROR_ROOT,
    k8s_dest=K8S_DEST,
    docker_dest=DOCKER_DEST,
    alma_dest=ALMA_DEST,
    k8s_minor=config.K8S_MINOR,
    _sudo=True,
)

# 5. nginx server block + SELinux relabel so nginx may serve /var/www/repos
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

# 6. Periodic sync via systemd timer
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

# 7. Serve it (nginx.conf's stock default server must not shadow our block, so
# reload after writing config). nginx is independent of the sync below.
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

# 8. First sync, run in the background with a log so provisioning returns
# promptly instead of blocking on a long dnf run. repo-sync.timer also runs it
# daily. Monitor progress with:
#     ssh -i ~/.ssh/id_ed25519 admin@<mirror> 'sudo tail -f /var/log/repo-sync.log'
LOG_FILE = "/var/log/repo-sync.log"
server.shell(
    name="Launch initial repo sync in background (log: /var/log/repo-sync.log)",
    commands=[
        f"nohup {SYNC_SCRIPT} > {LOG_FILE} 2>&1 &",
        "echo started",
    ],
    _sudo=True,
)

# 9. Wait for the background sync to finish, then sanity-check that the k8s and
# alma repodata are served over HTTP.
server.shell(
    name="Wait for sync to complete and verify repodata over HTTP",
    commands=[
        (
            "for i in $(seq 1 120); do "
            f"  grep -q '== done ==' {LOG_FILE} && break; "
            f"  if ! pgrep -f '{SYNC_SCRIPT}' >/dev/null; then break; fi; "
            "  echo -n '.'; sleep 5; "
            "done; echo; "
            f"grep -q '== done ==' {LOG_FILE} || echo '[warn] sync did not finish; see {LOG_FILE}' >&2; "
            f"for path in {config.K8S_REPO_SERVED_PATH} {config.ALMA_SERVED_PATH}; do "
            "  curl -fsS -o /dev/null -w '$path %{http_code}\\n' "
            "  http://127.0.0.1/$path/repodata/repomd.xml || echo '[warn] $path not served' >&2; "
            "done"
        )
    ],
)