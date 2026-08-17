"""Vendored AlmaLinux repo templates: single source of truth.

The ``almalinux*.repo`` files on every lab host are fully managed: they are
pushed from pre-rendered templates under ``templates/alma-repo/`` (captured
from a fresh AlmaLinux 10 cloud VM, see ``scripts/snapshot_alma_repos.py``)
instead of being edited in place. Two variables vary per consumer:

  * ``alma_base``: which mirror the baseurl points at,
  * ``enabled``:  whether each repo's primary section is enabled. This is a
    consumer decision (``consumer`` kwarg):

  * ``consumer="mirror"`` (k8s-repo, the mirror VM, ``deploy/repo.py``)
    reproduces the ORIGINAL stock enablement captured in the template — on
    the AlmaLinux 10 cloud image every primary section ships ``enabled=1``,
    so the result is all repos enabled against the NJU upstream. Every
    arch-specific repo is then synced in full (x86_64, latest only) by the
    mirror's repo-sync.sh and served under the canonical layout.
  * ``consumer="node"`` (``deploy/prepare.py``, plus incus/incus_vms.py
    cloud-init for k8s nodes) enables the SAME full set, but points it at the
    internal mirror, which now serves all of those repos — so nodes resolve
    their packages exactly as a stock Alma install would, just from intranet.

Consumers:
  * deploy/repo.py        -> k8s-repo mirror VM, NJU upstream, mirror role
  * deploy/prepare.py     -> k8s nodes, internal mirror, node role
  * incus/incus_vms.py    -> cloud-init first boot, NJU upstream, role picked
                             per VM name
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jinja2

import config


def alma_repo_templates_dir() -> Path:
    """Directory holding ``<dest>.repo.j2`` templates."""
    return config.REPO_ROOT / "templates" / "alma-repo"


def alma_repo_templates() -> dict[str, Path]:
    """Map destination filename (almalinux-baseos.repo) -> local template path."""
    return {
        p.name.removesuffix(".repo.j2") + ".repo": p
        for p in alma_repo_templates_dir().glob("*.repo.j2")
    }


def alma_repo_enabled(dest_filename: str, *, consumer: str) -> int:
    """Whether a repo's primary section is enabled, per consumer role.

    Both ``"mirror"`` and ``"node"`` enable every arch-specific repo: the
    mirror reproduces the original stock.enabled state (all repos enabled on
    the AlmaLinux 10 cloud image) so repo-sync.sh can sync them all, and the
    nodes point the same full set at the internal mirror, which now serves all
    of them.
    """
    if consumer in ("mirror", "node"):
        return 1
    raise ValueError(f"unknown consumer: {consumer!r}")


def render_alma_repo(template: Path, dest_filename: str, alma_base: str, *, consumer: str) -> str:
    """Render a vendored template for a given mirror base url and role."""
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
    return env.from_string(template.read_text()).render(
        alma_base=alma_base,
        enabled=alma_repo_enabled(dest_filename, consumer=consumer),
    )


def alma_repo_consistent(dest: str, src: Path, alma_base: str, *, consumer: str) -> bool:
    """True when the installed /etc/yum.repos.d/<dest> matches its render.

    Used by deploy/repo.py and deploy/prepare.py to decide whether a `dnf clean
    all` is needed. Only ever called during a pyinfra deploy, so the pyinfra
    imports stay function-local (this module is also imported by non-pyinfra
    tooling: incus/incus_vms.py, scripts/snapshot_alma_repos.py).
    """
    from pyinfra.context import host
    from pyinfra.facts.files import FileContents

    lines = host.get_fact(FileContents, path=f"/etc/yum.repos.d/{dest}")
    if lines is None:
        return False
    rendered = render_alma_repo(src, dest, alma_base, consumer=consumer)
    return "\n".join(lines).rstrip("\n") == rendered.rstrip("\n")