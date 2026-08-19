"""Atomic task: point the server's existing almalinux*.repo files at a base.

In-place edit, NOT a managed full-file template: the vendor-from-VM / template
approach was dropped. For every almalinux*.repo present on the host we only

  * comment out every active ``mirrorlist=`` line, and
  * rewrite the ``baseurl=`` line (active or the stock commented ``# baseurl=``)
    so the upstream host becomes ``alma_base`` while the URL tail
    ($releasever/<RepoDir>/$basearch/os/, debuginfo/source sections, gpg keys,
    enablement...) is kept byte-for-byte — a commented baseurl is also
    un-commented, so the repo stays resolvable once mirrorlist is gone.

Because the URL tail is preserved, whatever architecture the machine originally
had ($basearch) is retained — no per-host arch bookkeeping is needed and the
mirror simply serves both x86_64 and x86_64_v2 paths.

Data: ``alma_base`` (group data: NJU for the k8s test env, the intranet mirror
for k8s production). Idempotent: once the baseurl points at alma_base the line
is skipped (no op is generated) and ``dnf clean all`` only runs when an edit
actually changed something.

Detection is structural, not a host whitelist: a baseurl whose path's first
segment is ``almalinux`` (``scheme://host/almalinux``) is a mirror source and
gets re-pointed at ``alma_base``; the vault debuginfo/source sections use
``/vault/`` and are left alone, as is any non-mirror baseurl.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.facts.files import FileContents, FindFiles
from pyinfra.operations import files, server

# Mirror-source format shared by every Alma mirror: scheme://host/almalinux.
# Group 1 is the ``scheme://host/almalinux`` prefix used as the replace target
# (the URL tail is preserved by matching only this prefix).
MIRROR_SOURCE_RE = re.compile(r"^(https?://[^/]+/almalinux)(?=/|$)")

alma_base = host.data.alma_base

opfiles = (
    host.get_fact(FindFiles, path="/etc/yum.repos.d", fname="almalinux*.repo") or []
)
edit_ops: list = []
for fname in sorted(Path(p).name for p in opfiles):
    path = f"/etc/yum.repos.d/{fname}"
    edit_ops.append(
        files.replace(
            name=f"[{host.name}] {fname}: disable mirrorlist",
            path=path,
            text=r"^[ \t]*mirrorlist=",
            replace="# mirrorlist=",
            _sudo=True,
        )
    )
    for line in host.get_fact(FileContents, path=path) or []:
        m = re.match(r"^[ \t]*#?[ \t]*baseurl[ \t]*=[ \t]*(\S+)", line)
        if not m:
            continue
        src = MIRROR_SOURCE_RE.match(m.group(1))
        if not src or m.group(1).startswith(alma_base):
            continue
        edit_ops.append(
            files.replace(
                name=f"[{host.name}] {fname}: baseurl host -> {alma_base}",
                path=path,
                text=rf"^[ \t]*#?[ \t]*baseurl[ \t]*=[ \t]*{re.escape(src.group(1))}",
                replace=f"baseurl={alma_base}",
                extended_regex=True,
                _sudo=True,
            )
        )

server.shell(
    name=f"[{host.name}] dnf clean all (alma repos changed)",
    commands=["dnf clean all"],
    _sudo=True,
    _if=lambda: any(op.did_change() for op in edit_ops),
)