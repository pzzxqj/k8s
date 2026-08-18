"""Capture a fresh AlmaLinux VM's almalinux*.repo files and vendor them as
managed Jinja templates under templates/alma-repo/.

The stock files from a freshly-booted AlmaLinux 10 cloud VM are the starting
point. Each vendored ``<dest>.repo.j2`` differs from stock in exactly three
ways (this is the whole point of the managed-file model):

  * every ``mirrorlist=`` line is commented out (fully intranet),
  * the primary section's ``# baseurl=https://repo.almalinux.org/almalinux/``
    is uncommented and the host becomes the Jinja var ``alma_base``,
  * the primary section's ``enabled=`` becomes the Jinja var ``enabled``.

The disabled debuginfo/source sections (vault.almalinux.org) are kept verbatim.

Run on a machine with incus access (network implied for image pulls):

    uv run python scripts/snapshot_alma_repos.py                       # launch scratch VM, capture, destroy
    uv run python scripts/snapshot_alma_repos.py --instance alma-ref \
        --no-cleanup                                                   # reuse an already-running VM
    uv run python scripts/snapshot_alma_repos.py --dry-run             # show what would change, don't write
"""

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "deploy"))
sys.path.insert(0, str(ROOT / "incus"))


import _alma_repos
from _incus import instance_running, run

IMAGE = "images:almalinux/10/cloud"
DEFAULT_INSTANCE = "alma-repo-ref"

PRIMARY_BASEURL = "# baseurl=https://repo.almalinux.org/almalinux/"


def launch_scratch(name: str) -> None:
    print(f"[launch] creating scratch VM {name} from {IMAGE}")
    run(["incus", "init", IMAGE, name, "--vm"], capture_output=True)
    run(
        ["incus", "config", "device", "add", name, "agent", "disk", "source=agent:config"],
        capture_output=True,
    )
    run(["incus", "start", name], capture_output=True)
    run(["incus", "wait", name, "agent"], check=False, capture_output=True)
    print("[launch] VM running")


def destroy(name: str) -> None:
    run(["incus", "delete", "--force", name], capture_output=True)
    print(f"[done] destroyed scratch VM {name}")


def capture_repo_files(name: str) -> list[str]:
    for _ in range(60):
        out = run(
            ["incus", "exec", name, "--", "ls", "/etc/yum.repos.d"],
            check=False,
            capture_output=True,
        )
        if out.returncode == 0:
            files = [
                line.strip()
                for line in out.stdout.splitlines()
                if line.strip().startswith("almalinux") and line.strip().endswith(".repo")
            ]
            if files:
                return sorted(files)
        run(["sleep", "1"], capture_output=True)
    sys.exit("error: could not list /etc/yum.repos.d on the reference VM")


def fetch_raw(name: str, repo_file: str, tmp: Path) -> str:
    src = f"{name}/etc/yum.repos.d/{repo_file}"
    dest = tmp / repo_file
    run(["incus", "file", "pull", src, str(dest)], capture_output=True)
    return dest.read_text()


def transform(raw: str) -> str:
    """Apply the managed-file edits described in the module docstring.

    Fails loudly if the primary baseurl we expect to parameterize is missing.
    """
    out = []
    section_count = 0
    in_primary = False
    primary_baseurl_hit = False
    for line in raw.splitlines():
        if line.startswith("[") and line.endswith("]"):
            in_primary = section_count == 0
            section_count += 1
        if in_primary:
            if line.startswith("mirrorlist="):
                line = f"# {line}"
            elif line.startswith(PRIMARY_BASEURL):
                line = "baseurl={{ alma_base }}/" + line[len(PRIMARY_BASEURL):]
                primary_baseurl_hit = True
            elif line.startswith("enabled="):
                line = "enabled={{ enabled }}"
        else:
            if line.startswith("mirrorlist="):
                line = f"# {line}"
        out.append(line)
    if not primary_baseurl_hit:
        raise RuntimeError(
            f"primary section has no {PRIMARY_BASEURL!r} line; refusing to vendor unedited"
        )
    header = (
        "# Managed AlmaLinux repo file. Captured from a fresh AlmaLinux 10 cloud VM;\n"
        "# regenerate with scripts/snapshot_alma_repos.py. alma_base/enabled are the\n"
        "# only free variables: enabled=1 reproduces the stock enablement; the k8s\n"
        "# nodes enable the full repo set (see deploy/_alma_repos.py).\n"
    )
    return header + "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", default=DEFAULT_INSTANCE, help="VM to capture from")
    parser.add_argument("--no-cleanup", action="store_true", help="keep the scratch VM")
    parser.add_argument("--dry-run", action="store_true", help="show diffs without writing")
    args = parser.parse_args()

    tpl_dir = _alma_repos.alma_repo_templates_dir()
    tpl_dir.mkdir(parents=True, exist_ok=True)

    launched = not instance_running(args.instance)
    if launched:
        launch_scratch(args.instance)
    else:
        print(f"[reuse] capturing from running instance {args.instance}")

    tmp = ROOT / ".snapshot-alma-ref"
    try:
        tmp.mkdir(exist_ok=True)
        for repo_file in capture_repo_files(args.instance):
            rendered = transform(fetch_raw(args.instance, repo_file, tmp))
            dest = tpl_dir / f"{repo_file}.j2"
            if args.dry_run:
                if dest.exists() and dest.read_text() == rendered:
                    print(f"[ok] {dest.name} unchanged")
                else:
                    print(f"[~]  {dest.name} would be updated")
                continue
            dest.write_text(rendered)
            print(f"[write] {dest}")
        print(f"[done] {len(list(tpl_dir.glob('*.repo.j2')))} template(s) in {tpl_dir}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if launched and not args.no_cleanup:
            destroy(args.instance)


if __name__ == "__main__":
    main()
