"""Provision the internal repo mirror VM (k8s-repo) with nginx + dnf reposync.

Mirrors the upstream RPM repos (pkgs.k8s.io Kubernetes, download.docker.com
containerd) into /var/www/repos and serves them over plain HTTP on :80. The
first sync is started manually (see below); repo-sync.timer re-runs it daily.

k8s/worker nodes consume this mirror via templates/kubernetes.repo.j2 and
templates/docker-ce.repo.j2 (see deploy/prepare.py).

Run:
    uv run pyinfra -y inventory.py deploy/repo.py --limit k8s_repo \
        --user admin --key ~/.ssh/id_ed25519

This script only provisions the mirror (config, packages, service units). It
does NOT run the sync; after the first provision (or after a rebuild) start it
manually (the oneshot service logs to journald):
    ssh -i ~/.ssh/id_ed25519 admin@<mirror> 'sudo systemctl start repo-sync.service'
    ssh -i ~/.ssh/id_ed25519 admin@<mirror> 'sudo journalctl -fu repo-sync.service'
repo-sync.timer re-runs it daily from then on.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _alma_repos
from pyinfra.context import host
from pyinfra.facts.files import FileContents, FindFiles
from pyinfra.facts.selinux import FileContext, FileContextMapping
from pyinfra.facts.server import Selinux
from pyinfra.operations import files, selinux, server, systemd

import config

MIRROR_ROOT = "/var/www/repos"
K8S_DEST = f"{MIRROR_ROOT}/{config.K8S_REPO_SERVED_PATH}"
DOCKER_DEST = f"{MIRROR_ROOT}/{config.DOCKER_REPO_SERVED_PATH}"
ALMA_DEST = f"{MIRROR_ROOT}/{config.ALMA_SERVED_PATH}"
K8S_SRC_BASE = f"https://pkgs.k8s.io/core:/stable:/v{config.K8S_MINOR}/rpm"
DOCKER_SRC_BASE = "https://download.docker.com/linux/centos/10/x86_64/stable"

SYNC_SCRIPT = "/usr/local/bin/repo-sync.sh"
MIRROR_TEMPLATES = config.REPO_ROOT / "templates" / "mirror"

# 1. Fully managed Alma repo files on the mirror VM. Every /etc/yum.repos.d/
# almalinux*.repo is pushed (not sed-edited) from the vendored templates in
# templates/alma-repo/ (captured from a fresh AlmaLinux 10 cloud VM, see
# scripts/snapshot_alma_repos.py), pointed at the NJU upstream mirror:
# mirrorlists are commented out, and the primary section of each file restores
# the original stock enablement (consumer="mirror" -> all enabled), because
# NJU serves every repo. The mirror only *syncs/serves* BaseOS+AppStream
# (learning lab: just the packages the cluster needs) — that scope is decided
# by repo-sync.sh's explicit --disablerepo/--enablerepo, not by these files.
# Any almalinux*.repo not covered by a template is removed, so the file set is
# fully declared. `dnf clean all` runs only when a managed file differs from
# its rendered template or a stray file is purged — on a converged VM this
# whole step is a noop. These repos are reused by repo-sync.sh as the Alma
# mirror source. Run before packages so the installs below also resolve from
# NJU.
tpl = _alma_repos.alma_repo_templates()
# FindFiles returns absolute paths; compare basenames against the template set.
remote = {
    Path(f).name
    for f in (host.get_fact(FindFiles, "/etc/yum.repos.d", fname="almalinux*.repo") or [])
}


def _alma_repo_consistent(dest: str, src) -> bool:
    lines = host.get_fact(FileContents, path=f"/etc/yum.repos.d/{dest}")
    if lines is None:
        return False
    rendered = _alma_repos.render_alma_repo(
        src, dest, config.ALMA_UPSTREAM_BASE, consumer="mirror"
    )
    return "\n".join(lines).rstrip("\n") == rendered.rstrip("\n")


need_clean = any(
    not _alma_repo_consistent(d, s) for d, s in tpl.items()
) or any(f not in tpl for f in remote)

for dest, src in sorted(tpl.items()):
    files.template(
        name=f"Point {dest} at the NJU upstream mirror",
        src=str(src),
        dest=f"/etc/yum.repos.d/{dest}",
        mode="0644",
        alma_base=config.ALMA_UPSTREAM_BASE,
        enabled=_alma_repos.alma_repo_enabled(dest, consumer="mirror"),
        _sudo=True,
    )
for stray in sorted(set(remote) - set(tpl)):
    files.file(
        name=f"Remove unmanaged Alma repo {Path(stray).name}",
        path=f"/etc/yum.repos.d/{Path(stray).name}",
        present=False,
        _sudo=True,
    )
if need_clean:
    server.shell(
        name="Clear dnf metadata cache (Alma repo files changed)",
        commands=["dnf clean all"],
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
    alma_arch=config.ALMA_ARCH,
    k8s_minor=config.K8S_MINOR,
    _sudo=True,
)

# 5. nginx server block + a conditional SELinux fcontext rule. The rule only
# matters when SELinux is loaded: skip it entirely when mode == disabled, then
# consult the actual policy state — the FileContextMapping fact (is the rule
# present/wrong?) and the FileContext fact (does /var/www/repos carry a
# mismatched label?) — and only register the mapping when one of them differs,
# so a converged run reports no changes. file_context_mapping writes the policy
# rule (declaratively, -a vs -m) but relabels nothing itself; applying it to
# already-existing files is a one-time manual step (only needed when the disk
# holds unlabeled/wrong-labeled files, e.g. right after enabling SELinux):
#     ssh -i ~/.ssh/id_ed25519 admin@<mirror> 'sudo restorecon -RF /var/www/repos'
# New files dropped in by each sync get relabeled automatically by repo-sync.sh
# (guarded restorecon at the end of the sync).
repo_conf = files.template(
    name="Write nginx repo server block",
    src=str(MIRROR_TEMPLATES / "nginx-repo.conf.j2"),
    dest="/etc/nginx/conf.d/repo.conf",
    mirror_root=MIRROR_ROOT,
    _sudo=True,
)
if host.get_fact(Selinux).get("mode") == "disabled":
    print("[skip] SELinux disabled; no fcontext rule needed")
else:
    mapping = (
        host.get_fact(FileContextMapping, target=r"/var/www/repos(/.*)?", _sudo=True) or {}
    )
    fctx = host.get_fact(FileContext, path=MIRROR_ROOT) or {}
    if mapping.get("type") != "httpd_sys_content_t" or fctx.get("type") != "httpd_sys_content_t":
        selinux.file_context_mapping(
            name="Map /var/www/repos to httpd_sys_content_t",
            target=r"/var/www/repos(/.*)?",
            se_type="httpd_sys_content_t",
            _sudo=True,
        )
    else:
        print("[skip] fcontext rule and /var/www/repos already httpd_sys_content_t")

def _unit_consistent(dest: str) -> bool:
    """True when the installed unit file matches its (static) template."""
    lines = host.get_fact(FileContents, path=f"/etc/systemd/system/{dest}")
    if lines is None:
        return False
    rendered = (MIRROR_TEMPLATES / f"{dest}.j2").read_text()
    return "\n".join(lines).rstrip("\n") == rendered.rstrip("\n")


# 6. Periodic sync via systemd timer. daemon-reload is only needed when a unit
# file changed (fresh VM or template edit); once installed the enable/start are
# pure no-ops, so a converged run reports no changes.
need_systemd_reload = not (
    _unit_consistent("repo-sync.service") and _unit_consistent("repo-sync.timer")
)
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
systemd.service(
    name="Enable repo-sync.timer",
    service="repo-sync.timer",
    enabled=True,
    running=True,
    daemon_reload=need_systemd_reload,
    _sudo=True,
)
# 7. Serve it (nginx.conf's stock default server must not shadow our block, so
# reload after writing config). nginx is independent of the sync below.
systemd.service(
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
    _if=repo_conf.did_change,
)