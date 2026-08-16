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
      so the result is all repos enabled against the NJU upstream. Note that
      which repos get *synced/served* (BaseOS + AppStream only — this is a
      learning lab that mirrors just what the cluster needs) is a separate
      decision made by the mirror's repo-sync.sh via explicit
      ``--disablerepo/* --enablerepo=baseos,appstream``, not by these files.
    * ``consumer="node"`` (``deploy/prepare.py``, plus incus/incus_vms.py
      cloud-init for k8s nodes) enables only BaseOS/AppStream, because the
      node's baseurl points at the internal mirror which serves just those.

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


def _is_baseos_or_appstream(dest_filename: str) -> bool:
    return "baseos" in dest_filename or "appstream" in dest_filename


def alma_repo_enabled(dest_filename: str, *, consumer: str) -> int:
    """Whether a repo's primary section is enabled, per consumer role.

    ``"mirror"`` reproduces the original stock.enabled state (all repos are
    enabled on the AlmaLinux 10 cloud image); ``"node"`` enables only the
    BaseOS/AppStream repos the internal mirror actually serves.
    """
    if consumer == "mirror":
        return 1
    if consumer == "node":
        return 1 if _is_baseos_or_appstream(dest_filename) else 0
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