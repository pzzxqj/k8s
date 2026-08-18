#!/usr/bin/env python3
"""Download the container images + Cilium artifacts the cluster needs, producing
an offline bundle under ./offline and rsyncing it to every node's
/opt/k8s-offline (upload needs no pyinfra).

k8s/containerd RPMs are installed by deploy/k8s_prepare.py straight from the upstream
repos (pkgs.k8s.io / download.docker.com), so they are NOT part of the bundle;
only container images + Cilium artifacts are.

The k8s image list comes straight from the LOCAL kubeadm (must match the version
the nodes will install from pkgs.k8s.io): `kubeadm config images list` emits
every image kubeadm init/join will reference, so no upstream-side version
resolution is needed. Before building, the host kubeadm version is verified
against the newest kubelet the upstream pkgs.k8s.io repo serves (host-side
`dnf repoquery` via --repofrompath) and aborts on mismatch; if the host has no
dnf (or dnf cannot resolve pkgs.k8s.io) the check degrades to a warning. Cilium
images are a fixed list for the pinned chart version (no helm rendering).

Requires on the host: docker (for image export), kubeadm, rsync + ssh access to
the nodes, and a reachable pkgs.k8s.io for the version check. HTTP downloads go
through httpx.

The upload targets come from a pyinfra inventory file (Host Data drives
ssh_hostname/ssh_user), so the k8s test and production clusters work identically
— only the --inventory differs.

    uv run python scripts/k8s_download_offline.py                       # k8s_test (default)
    uv run python scripts/k8s_download_offline.py --no-upload
    uv run python scripts/k8s_download_offline.py --inventory inventories/k8s_production.py
    uv run python scripts/k8s_download_offline.py --skip-version-check
    OFFLINE_DIR=/path uv run python scripts/k8s_download_offline.py
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

REPO_ROOT = Path(__file__).resolve().parent.parent

# Pin the digest exactly as the chart renders it (fixed list, no helm render).
CILIUM_IMAGES = (
    "quay.io/cilium/cilium-envoy:v1.37.5-1782911245-7cffc778c923f68a77954a53b1a98d6b5353f004",
)

MASTER_SCOPED = ("kube-apiserver", "kube-controller-manager", "kube-scheduler", "etcd")


@dataclass(frozen=True)
class Settings:
    offline_dir: Path
    cilium_chart_ver: str
    cilium_cli_ver: str
    cilium_arch: str
    ssh_key: Path
    inventory: Path
    group: str

    def all_cilium_images(self) -> list[str]:
        return [
            f"quay.io/cilium/cilium:v{self.cilium_chart_ver}",
            f"quay.io/cilium/operator-generic:v{self.cilium_chart_ver}",
            *CILIUM_IMAGES,
        ]


@dataclass(frozen=True)
class Options:
    no_upload: bool = False
    skip_version_check: bool = False


def load_targets(inventory: Path, group: str) -> list[tuple[str, str]]:
    """(ssh_hostname, ssh_user) pairs from an inventory file's Host Data.

    The inventory is a plain python module (pyinfra host lists); reusing it here
    keeps the single source of truth. Each entry is either a bare host string
    or a ``(name, data)`` tuple with ssh_hostname / ssh_user data keys.
    """
    if not inventory.is_file():
        sys.exit(f"[error] inventory not found: {inventory}")
    ns = runpy.run_path(str(inventory))
    entries = ns.get(group)
    if entries is None:
        sys.exit(
            f"[error] inventory {inventory} has no group {group!r} "
            f"(groups: {sorted(k for k, v in ns.items() if isinstance(v, (list, tuple)) and not k.startswith('_'))})"
        )
    targets: list[tuple[str, str]] = []
    for entry in entries:
        if isinstance(entry, tuple) and len(entry) == 2:
            name, data = entry
            ip = str(data.get("ssh_hostname", name))
            user = str(data.get("ssh_user", config.SSH_USER))
        else:
            ip = user = str(entry)
            user = config.SSH_USER
        targets.append((ip, user))
    return targets


def parse_args() -> tuple[Settings, Options]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline-dir",
        type=Path,
        help="output directory (default: $OFFLINE_DIR or <repo>/offline)",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="build the bundle but do not rsync it to the nodes",
    )
    parser.add_argument(
        "--skip-version-check",
        action="store_true",
        help="do not verify the host kubeadm matches the upstream k8s version",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("inventories/k8s_test.py"),
        help="inventory file whose nodes receive the bundle (default: k8s_test)",
    )
    parser.add_argument(
        "--group",
        default="nodes",
        help="group within the inventory to upload to (default: nodes)",
    )
    ns = parser.parse_args()

    arch = os.environ.get("ARCH", "x86_64")
    if arch != "x86_64":
        parser.error(f"Unsupported host arch: {arch}")

    offline_dir = Path(
        ns.offline_dir or os.environ.get("OFFLINE_DIR") or (REPO_ROOT / "offline")
    )

    return (
        Settings(
            offline_dir=offline_dir,
            cilium_chart_ver=os.environ.get("CILIUM_CHART_VER", "1.20.0"),
            cilium_cli_ver=os.environ.get("CILIUM_CLI_VER", "0.19.7"),
            cilium_arch="amd64",
            ssh_key=Path(
                os.environ.get("SSH_KEY", str(Path.home() / ".ssh" / "id_ed25519"))
            ),
            inventory=Path(
                os.environ.get("OFFLINE_INVENTORY", str(ns.inventory))
            ).resolve(),
            group=ns.group,
        ),
        Options(
            no_upload=ns.no_upload,
            skip_version_check=ns.skip_version_check,
        ),
    )


def new_client() -> httpx.Client:
    return httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=30.0),
        transport=httpx.HTTPTransport(retries=3),
    )


def download(client: httpx.Client, url: str, dest: Path) -> None:
    """Download url to dest; skips when dest already exists and is non-empty."""
    if dest.is_file() and dest.stat().st_size > 0:
        print(f"  [skip] {dest}")
        return
    print(f"  [download] {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    with client.stream("GET", url) as resp:
        resp.raise_for_status()
        with part.open("wb") as fh:
            for chunk in resp.iter_bytes(1 << 16):
                fh.write(chunk)
    part.replace(dest)


def setup_layout(settings: Settings) -> None:
    for sub in ("images", "cilium"):
        (settings.offline_dir / sub).mkdir(parents=True, exist_ok=True)


def kubeadm_version() -> str:
    """Exact k8s version of the LOCAL kubeadm (must match the mirror's RPMs)."""
    proc = subprocess.run(
        ["kubeadm", "version", "-o", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return str(json.loads(proc.stdout)["clientVersion"]["gitVersion"])


def kubeadm_images() -> list[str]:
    """Every image kubeadm will reference, from the local kubeadm binary."""
    proc = subprocess.run(
        ["kubeadm", "config", "images", "list"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def normalize_version(ver: str) -> str:
    return ver.lstrip("v")


def upstream_k8s_version() -> str | None:
    """The newest kubelet version pkgs.k8s.io currently serves (what the nodes
    would install), resolved from THIS host.

    Replaces the old check that ssh'd to the lab's mirror VM — the k8s test env
    has no internal mirror anymore and installs from pkgs.k8s.io directly.
    Returns None when dnf is missing or cannot resolve pkgs.k8s.io, so the
    caller degrades to a warning instead of failing.
    """
    if shutil.which("dnf") is None:
        print("[skip] no dnf on this host; k8s version check degraded to warning")
        return None
    base_query = (
        f"dnf --disablerepo='*' --repofrompath=pkgs,{config.K8S_UPSTREAM_BASE} "
        "-q repoquery --latest-limit 1 --qf '%{VERSION}' kubelet"
    )
    for query in (base_query, base_query.replace("dnf", "sudo dnf", 1)):
        proc = subprocess.run(
            query,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            if lines:
                return lines[-1]
    print("[skip] could not query pkgs.k8s.io; k8s version check degraded to warning")
    return None


def check_k8s_version() -> None:
    local_ver = normalize_version(kubeadm_version())
    upstream_ver = upstream_k8s_version()
    if upstream_ver is None:
        print("[warn] 未做版本比对 (宿主机无 dnf 或无法解析 pkgs.k8s.io); 可用 --skip-version-check 显式忽略")
        return
    upstream_ver = normalize_version(upstream_ver)
    if local_ver != upstream_ver:
        sys.exit(
            f"[error] 宿主机 kubeadm 版本 ({local_ver}) 与 pkgs.k8s.io 当前 kubelet ({upstream_ver}) 不一致, "
            "节点装到的 kubeadm 会引用不存在的预载镜像. "
            "请升级宿主机 kubeadm 或加 --skip-version-check"
        )
    print(f"[*] 版本校验通过: {local_ver} == {upstream_ver}")


def scope_for(image: str) -> str:
    """'master' = control-plane-only image; 'all' = every node needs it."""
    return "master" if any(name in image for name in MASTER_SCOPED) else "all"


def download_cilium_artifacts(settings: Settings, client: httpx.Client) -> None:
    chart_tgz = (
        settings.offline_dir / "cilium" / f"cilium-{settings.cilium_chart_ver}.tgz"
    )
    print(f"[*] Downloading Cilium {settings.cilium_chart_ver} helm chart ...")
    download(client, f"https://helm.cilium.io/{chart_tgz.name}", chart_tgz)
    extract_chart(chart_tgz, settings.offline_dir / "cilium")

    cilium_bin = settings.offline_dir / "cilium" / "cilium"
    if not cilium_bin.is_file():
        print(f"[*] Downloading Cilium CLI v{settings.cilium_cli_ver} ...")
        cli_tgz = settings.offline_dir / "cilium" / "cilium-cli.tar.gz"
        download(
            client,
            (
                f"https://github.com/cilium/cilium-cli/releases/download/"
                f"v{settings.cilium_cli_ver}/cilium-linux-{settings.cilium_arch}.tar.gz"
            ),
            cli_tgz,
        )
        extract_archive_member(cli_tgz, "cilium", cilium_bin)
        cli_tgz.unlink()


def extract_chart(chart_tgz: Path, cilium_dir: Path) -> None:
    chart_dir = cilium_dir / "chart"
    if chart_dir.is_dir():
        return
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(chart_tgz) as tf:
            tf.extractall(tmp)
        top = next(p for p in Path(tmp).iterdir() if p.is_dir())
        shutil.move(str(top), str(chart_dir))


def extract_archive_member(tarball: Path, member_name: str, dest: Path) -> None:
    with tarfile.open(tarball) as tf:
        member = tf.extractfile(member_name)
        if member is None:
            raise RuntimeError(f"{member_name} not found in {tarball}")
        dest.write_bytes(member.read())
    dest.chmod(0o755)


def safe_image_name(ref: str) -> str:
    return ref.translate(str.maketrans("/:", "--"))


def docker_pull_save(ref: str, dest: Path) -> None:
    if dest.is_file() and dest.stat().st_size > 0:
        print(f"  [skip] {dest}")
        return
    print(f"[*] docker pull + save {ref}")
    subprocess.run(["docker", "pull", ref], check=True)
    subprocess.run(["docker", "save", "-o", str(dest), ref], check=True)


def write_image_plan(settings: Settings, k8s_imgs: list[str]) -> None:
    images_dir = settings.offline_dir / "images"
    cilium = settings.all_cilium_images()

    all_images = sorted(set(k8s_imgs) | set(cilium))
    (images_dir / "images.txt").write_text("".join(f"{img}\n" for img in all_images))

    # "master" images only go on the control-plane node; the rest on every node.
    plan = [f"{scope_for(img)} {img}" for img in k8s_imgs]
    plan += [f"all {img}" for img in cilium]
    (images_dir / "import-plan.txt").write_text("".join(f"{line}\n" for line in plan))

    print("[*] Pulling + saving container images (this can take a while) ...")
    for img in sorted(all_images):
        fname = safe_image_name(img)
        if scope_for(img) == "master":
            fname = f"master-{fname}"
        docker_pull_save(img, images_dir / f"{fname}.tar")


def write_manifest(offline_dir: Path) -> None:
    files = sorted(p for p in offline_dir.rglob("*") if p.is_file())
    with (offline_dir / "MANIFEST.txt").open("w") as fh:
        for p in files:
            fh.write(f"./{p.relative_to(offline_dir)}\n")
        fh.write("---\n")
        for p in files:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            fh.write(f"{digest}  ./{p.relative_to(offline_dir)}\n")


def project_version() -> str:
    """Version from pyproject.toml (single source of truth), or 'unknown'."""
    try:
        import tomllib

        with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
            return str(tomllib.load(fh)["project"]["version"])
    except (OSError, KeyError, ValueError):
        return "unknown"


def git_commit() -> str:
    """Short HEAD commit of the repo that built this bundle, or 'unknown'."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def write_deploy_version(offline_dir: Path) -> None:
    """Record the script version + commit that produced the bundle.

    Rsynced to every node (offline/deploy-version.txt) for deployment provenance.
    """
    built = datetime.datetime.now().astimezone().date().isoformat()
    (offline_dir / "deploy-version.txt").write_text(
        f"{project_version()} @ {git_commit()} ({built})\n"
    )


def rsync_args(settings: Settings) -> list[str]:
    return [
        "rsync",
        "-az",
        "--rsync-path=sudo rsync",
        "-e",
        (
            "ssh -i "
            f"{shlex_quote(str(settings.ssh_key))} "
            "-o StrictHostKeyChecking=accept-new"
        ),
    ]


def shlex_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def upload_offline(settings: Settings) -> None:
    src = f"{settings.offline_dir}/"
    for ip, user in load_targets(settings.inventory, settings.group):
        dest = f"{user}@{ip}:{config.NODE_OFFLINE_DIR}/"
        print(f"[*] rsync bundle -> {dest}")
        subprocess.run([*rsync_args(settings), src, dest], check=True)


def main() -> int:
    settings, options = parse_args()
    setup_layout(settings)

    k8s_ver = kubeadm_version()
    k8s_imgs = kubeadm_images()
    print(f"[*] Kubernetes version: {k8s_ver}")
    if not options.skip_version_check:
        check_k8s_version()
    (settings.offline_dir / "k8s-version.txt").write_text(f"{k8s_ver}\n")

    with new_client() as client:
        download_cilium_artifacts(settings, client)

    write_image_plan(settings, k8s_imgs)
    write_deploy_version(settings.offline_dir)
    write_manifest(settings.offline_dir)

    du = subprocess.run(
        ["du", "-sh", str(settings.offline_dir)],
        check=False,
        capture_output=True,
        text=True,
    )
    print(f"\n[*] Done. Bundle: {settings.offline_dir}")
    if du.returncode == 0:
        print(du.stdout.strip())

    if options.no_upload:
        print("[*] Skipped upload (--no-upload)")
    else:
        upload_offline(settings)

    return 0


if __name__ == "__main__":
    sys.exit(main())
