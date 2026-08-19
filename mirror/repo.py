"""Provision the intranet RPM mirror ({MIRROR_HOST}, ssh alias `mirror`).

Reproduces the current real deployment exactly (verified converged, zero-change
on the live mirror):

  * nginx serving {MIRROR_ROOT} over plain HTTP on :80 via
    /etc/nginx/conf.d/repos.conf (default_server, no autoindex)
  * lftp + dnf-reposync scripts in /usr/local/bin (mirror/scripts/*.sh,
    byte-identical to what is deployed) with three oneshot systemd services and
    timers: alma daily, docker-ce 00:20, kubernetes 00:40 (all Persistent=true)
  * firewalld http open + SELinux fcontext {MIRROR_ROOT} -> httpd_sys_content_t

The mirror's OWN /etc/yum.repos.d/ is intentionally NOT managed: syncs run
directly against HTTPS upstreams (NJU / Aliyun / pkgs.k8s.io via
--repofrompath), so no almalinux-*.repo management and no mirror-sources.repo
are needed.

Run (first sync ran manually; the timers re-run daily from then on):
    uv run pyinfra -y {MIRROR_HOST} mirror/repo.py --user {MIRROR_USER}

(No --key: authentication goes through the local ssh-agent / ssh_config.)

After the first provision (or a rebuild) the sync itself is still manual:
    ssh mirror 'sudo systemctl start alma-repo-sync.service docker-ce-repo-sync.service kubernetes-repo-sync.service'
    ssh mirror 'sudo journalctl -fu alma-repo-sync.service'
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pyinfra.context import host
from pyinfra.facts.files import FileContents
from pyinfra.facts.selinux import FileContext, FileContextMapping
from pyinfra.facts.server import Command, Selinux
from pyinfra.operations import files, selinux, server, systemd

import config  # mirror/config.py

MIRROR_DIR = Path(__file__).resolve().parent
MIRROR_ROOT = config.MIRROR_ROOT

SYNC_SCRIPTS = [
    "alma-repo-sync.sh",
    "docker-ce-repo-sync.sh",
    "kubernetes-repo-sync.sh",
]
SYNC_UNITS = [
    "alma-repo-sync.service",
    "alma-repo-sync.timer",
    "docker-ce-repo-sync.service",
    "docker-ce-repo-sync.timer",
    "kubernetes-repo-sync.service",
    "kubernetes-repo-sync.timer",
]
TIMERS = [u for u in SYNC_UNITS if u.endswith(".timer")]

# 1. Packages (reposync comes from dnf-plugins-core on AlmaLinux 10's dnf4).
server.packages(
    name="Install nginx, lftp, dnf reposync and semanage",
    packages=[
        "nginx",
        "lftp",
        "dnf-plugins-core",
        "policycoreutils-python-utils",
    ],
    _sudo=True,
)

# 2. Sync scripts, rendered verbatim from mirror/scripts/ (no jinja vars -> the
# rendered output is byte-identical to the deployed files). files.template is
# used (not files.put) because the mirror has no SFTP subsystem.
for script in SYNC_SCRIPTS:
    files.template(
        name=f"Install {script}",
        src=str(MIRROR_DIR / "scripts" / script),
        dest=f"/usr/local/bin/{script}",
        mode="755",
        _sudo=True,
    )

# 3. Systemd units + timers. daemon-reload only when a unit changed (fresh VM
# or template edit); enable/start then are pure no-ops on a converged mirror.
def _unit_consistent(dest: str) -> bool:
    """True when the installed unit file matches its static template."""
    lines = host.get_fact(FileContents, path=f"/etc/systemd/system/{dest}")
    if lines is None:
        return False
    local = (MIRROR_DIR / "templates" / dest).read_text()
    return "\n".join(lines).rstrip("\n") == local.rstrip("\n")


need_systemd_reload = any(not _unit_consistent(u) for u in SYNC_UNITS)
for unit in SYNC_UNITS:
    files.template(
        name=f"Write {unit}",
        src=str(MIRROR_DIR / "templates" / unit),
        dest=f"/etc/systemd/system/{unit}",
        _sudo=True,
    )
for timer in TIMERS:
    systemd.service(
        name=f"Enable {timer}",
        service=timer,
        enabled=True,
        running=True,
        daemon_reload=need_systemd_reload,
        _sudo=True,
    )

# 4. nginx server block (reload only when the config actually changed).
repo_conf = files.template(
    name="Write nginx repo server block",
    src=str(MIRROR_DIR / "templates" / "repos.conf.j2"),
    dest="/etc/nginx/conf.d/repos.conf",
    mirror_root=MIRROR_ROOT,
    _sudo=True,
)
systemd.service(
    name="Enable and start nginx",
    service="nginx",
    running=True,
    enabled=True,
    _sudo=True,
)
server.shell(
    name="Reload nginx to pick up repos.conf",
    commands=["nginx -t && systemctl reload nginx || true"],
    _sudo=True,
    _if=repo_conf.did_change,
)

# 5. firewalld: serve :80. runtime http already open -> noop (add permanent
# rule + reload only on a fresh box). pyinfra has no firewalld operation, so
# the check is a guarded shell.
systemd.service(
    name="Enable and start firewalld",
    service="firewalld",
    running=True,
    enabled=True,
    _sudo=True,
)
server.shell(
    name="Allow http service in the firewalld default zone",
    commands=["firewall-cmd --permanent --add-service=http && firewall-cmd --reload"],
    _sudo=True,
    _if=lambda: "http" not in (
        host.get_fact(Command, "firewall-cmd --list-services", _sudo=True) or ""
    ).split(),
)

# 6. SELinux fcontext rule. The rule only matters when SELinux is loaded: skip
# it entirely when mode == disabled, then consult the actual policy state — the
# FileContextMapping fact (is the rule present/wrong?) and the FileContext fact
# (does MIRROR_ROOT carry a mismatched label?) — and only register the mapping
# when one of them differs, so a converged run reports no changes.
# file_context_mapping writes the policy rule (declaratively, -a vs -m) but
# relabels nothing itself; applying it to already-existing files is a one-time
# manual step (only needed when the disk holds unlabeled files, e.g. right
# after enabling SELinux):
#     ssh mirror 'sudo restorecon -RF {MIRROR_ROOT}'
# New files dropped in by each sync get relabeled automatically by the scripts
# (guarded restorecon at the end of each sync).
if host.get_fact(Selinux).get("mode") == "disabled":
    print("[skip] SELinux disabled; no fcontext rule needed")
else:
    target = rf"{MIRROR_ROOT}(/.*)?"
    mapping = (
        host.get_fact(FileContextMapping, target=target, _sudo=True) or {}
    )
    fctx = host.get_fact(FileContext, path=MIRROR_ROOT) or {}
    if mapping.get("type") != "httpd_sys_content_t" or fctx.get("type") != "httpd_sys_content_t":
        selinux.file_context_mapping(
            name=f"Map {MIRROR_ROOT} to httpd_sys_content_t",
            target=target,
            se_type="httpd_sys_content_t",
            _sudo=True,
        )
    else:
        print(f"[skip] fcontext rule and {MIRROR_ROOT} already httpd_sys_content_t")