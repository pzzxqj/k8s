"""Vendored AlmaLinux repo templates: single source of truth.

The ``almalinux*.repo`` files on every lab node are fully managed: they are
pushed from pre-rendered templates under ``templates/alma-repo/`` (captured
from a fresh AlmaLinux 10 cloud VM, see ``scripts/snapshot_alma_repos.py``)
instead of being edited in place. Two variables vary per consumer:

  * ``alma_base``: which mirror the baseurl points at,
  * ``enabled``:  whether each repo's primary section is enabled. This is a
    consumer decision (``consumer`` kwarg):

  * ``consumer="node"`` (``deploy/prepare.py``, plus incus/incus_vms.py
    cloud-init for k8s nodes) enables the SAME full set — the AlmaLinux 10
    cloud image ships ``enabled=1`` for every primary section — and points it
    at the NJU upstream, so nodes resolve their packages exactly as a stock
    Alma install would, straight from the internet.

Consumers:
  * deploy/prepare.py     -> k8s nodes, NJU upstream, node role
  * incus/incus_vms.py    -> cloud-init first boot, NJU upstream, node role
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

    ``"node"`` enables every arch-specific repo: this reproduces the stock
    enablement of the AlmaLinux 10 cloud image (all repos enabled), and the
    nodes point that full set at the NJU upstream, which serves all of them.
    """
    if consumer == "node":
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

    Used by deploy/prepare.py to decide whether a `dnf clean all` is needed.
    Only ever called during a pyinfra deploy, so the pyinfra
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