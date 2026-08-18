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

Data: ``alma_base`` (group data: NJU for learning, the intranet mirror for
production). Idempotent: once the baseurl points at alma_base the line no
longer matches any known upstream and becomes a no-op; ``dnf clean all`` only
runs when an edit actually changed something.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyinfra.context import host
from pyinfra.facts.files import FindFiles
from pyinfra.operations import files, server

# Upstream bases the transform understands. When a baseurl already points at
# one of these it is re-pointed at alma_base; anything else (e.g. the vault
# debuginfo/source sections) is left alone.
KNOWN_UPSTREAMS = (
    "https://repo.almalinux.org/almalinux",
    "https://mirrors.nju.edu.cn/almalinux",
    "https://mirrors.aliyun.com/almalinux",
)

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
    for upstream in KNOWN_UPSTREAMS:
        edit_ops.append(
            files.replace(
                name=f"[{host.name}] {fname}: baseurl host -> {alma_base}",
                path=path,
                text=rf"^[ \t]*#?[ \t]*baseurl[ \t]*=[ \t]*{re.escape(upstream)}",
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