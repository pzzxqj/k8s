#!/usr/bin/env python3
"""Download everything k8s/containerd/Cilium related onto the HOST, producing an
offline bundle under ./offline that deploy/prepare.py uploads to the nodes.

k8s/containerd RPMs are installed from the internal repo mirror (deploy/repo.py),
so they are NOT part of the bundle unless you pass --no-rpms to skip them;
container images + Cilium artifacts always come from this bundle. Base packages
(container-selinux) come from the node's ONLINE dnf repos.

Requires on the host: docker (for image export). HTTP downloads go through httpx.

    uv run python scripts/download_offline.py            # build ./offline
    uv run python scripts/download_offline.py --no-rpms   # RPMs come from the mirror
    OFFLINE_DIR=/path uv run python scripts/download_offline.py
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parent.parent
HELM_DIR = Path(tempfile.gettempdir()) / "k8s-offline-helm"
SHA256_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")

K8S_CONTROL_PLANE_IMAGES = (
    "registry.k8s.io/kube-apiserver",
    "registry.k8s.io/kube-controller-manager",
    "registry.k8s.io/kube-scheduler",
)
K8S_COMMON_IMAGES = ("registry.k8s.io/kube-proxy",)
K8S_COMMON_PINNED_IMAGES = (
    "registry.k8s.io/pause:3.10.2",
    "registry.k8s.io/coredns/coredns:v1.14.2",
)
ETCD_IMAGE = "registry.k8s.io/etcd:3.6.8-0"

# Pin the digest exactly as the chart renders it (sanity-checked below).
CILIUM_IMAGES = (
    "quay.io/cilium/cilium-envoy:v1.37.5-1782911245-7cffc778c923f68a77954a53b1a98d6b5353f004",
)


def _local_name(tag: str) -> str:
    """ElementTree tag -> local name (strips the XML namespace)."""
    return tag.rsplit("}", 1)[-1]


@dataclass(frozen=True)
class Settings:
    offline_dir: Path
    k8s_minor: str
    k8s_version: str | None
    cilium_chart_ver: str
    cilium_cli_ver: str
    containerd_rpm: str
    helm_ver: str
    arch: str
    docker_arch: str
    helm_arch: str
    cilium_arch: str
    no_rpms: bool = False

    @property
    def pkgs_base(self) -> str:
        return f"https://pkgs.k8s.io/core:/stable:/v{self.k8s_minor}/rpm"

    def control_plane_images(self, k8s_ver: str) -> list[str]:
        return [f"{img}:v{k8s_ver}" for img in K8S_CONTROL_PLANE_IMAGES] + [ETCD_IMAGE]

    def common_images(self, k8s_ver: str) -> list[str]:
        return [f"{img}:v{k8s_ver}" for img in K8S_COMMON_IMAGES] + list(
            K8S_COMMON_PINNED_IMAGES
        )

    def all_cilium_images(self) -> list[str]:
        return [
            f"quay.io/cilium/cilium:v{self.cilium_chart_ver}",
            f"quay.io/cilium/operator-generic:v{self.cilium_chart_ver}",
            *CILIUM_IMAGES,
        ]


def parse_args() -> Settings:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline-dir",
        type=Path,
        help="output directory (default: $OFFLINE_DIR or <repo>/offline)",
    )
    parser.add_argument(
        "--no-rpms",
        action="store_true",
        help=(
            "skip k8s/containerd RPM download — they are now installed from the "
            "internal repo mirror (deploy/repo.py), not from the offline bundle"
        ),
    )
    ns = parser.parse_args()

    arch = os.environ.get("ARCH", "x86_64")
    if arch != "x86_64":
        parser.error(f"Unsupported host arch: {arch}")

    offline_dir = Path(
        ns.offline_dir or os.environ.get("OFFLINE_DIR") or (REPO_ROOT / "offline")
    )

    return Settings(
        offline_dir=offline_dir,
        k8s_minor=os.environ.get("K8S_MINOR", "1.36"),
        k8s_version=os.environ.get("K8S_VERSION"),
        cilium_chart_ver=os.environ.get("CILIUM_CHART_VER", "1.20.0"),
        cilium_cli_ver=os.environ.get("CILIUM_CLI_VER", "0.19.7"),
        containerd_rpm=os.environ.get(
            "CONTAINERD_RPM", "containerd.io-2.3.3-1.el10.x86_64.rpm"
        ),
        helm_ver=os.environ.get("HELM_VER", "3.18.4"),
        arch=arch,
        docker_arch="x86_64",
        helm_arch="amd64",
        cilium_arch="amd64",
        no_rpms=ns.no_rpms,
    )


def new_client() -> httpx.Client:
    return httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=30.0),
        transport=httpx.HTTPTransport(retries=3),
    )


def http_get(client: httpx.Client, url: str) -> bytes:
    resp = client.get(url)
    resp.raise_for_status()
    return resp.content


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


class RpmRepo:
    """Metadata of a dnf RPM repo, fetched once and reused for all lookups.

    Packages map to their (version, location-href) pairs as parsed from
    primary.xml.gz — no repeated downloads, no regex-on-XML.
    """

    def __init__(self, client: httpx.Client, base_url: str, arch: str):
        self.client: httpx.Client = client
        self.base_url: str = base_url
        self.arch: str = arch
        self._packages: dict[str, list[tuple[str, str]]] | None = None
        self._patch_versions: list[str] = []

    def _load(self) -> None:
        repomd = ET.fromstring(
            http_get(self.client, f"{self.base_url}/repodata/repomd.xml")
        )
        primary_href = next(
            (
                loc.get("href")
                for data in repomd
                if _local_name(data.tag) == "data" and data.get("type") == "primary"
                for loc in data
                if _local_name(loc.tag) == "location" and loc.get("href")
            ),
            None,
        )
        if not primary_href:
            raise RuntimeError(
                f"no primary metadata href in {self.base_url}/repodata/repomd.xml"
            )
        # href is already "repodata/<hash>-primary.xml.gz"; keep just the filename
        primary_href = primary_href.rsplit("/", 1)[-1]

        primary = gzip.decompress(
            http_get(self.client, f"{self.base_url}/repodata/{primary_href}")
        )
        packages: dict[str, list[tuple[str, str]]] = {}
        patches: list[str] = []
        for pkg in ET.fromstring(primary):
            if _local_name(pkg.tag) != "package":
                continue
            name = ver = href = None
            for child in pkg:
                tag = _local_name(child.tag)
                if tag == "name":
                    name = child.text
                elif tag == "version":
                    ver = child.get("ver")
                elif tag == "location":
                    href = child.get("href")
            if name and ver and href:
                if href.startswith(f"{self.arch}/"):
                    packages.setdefault(name, []).append((ver, href))
                patches.append(ver)
        self._packages = packages
        self._patch_versions = patches

    def packages(self) -> dict[str, list[tuple[str, str]]]:
        if self._packages is None:
            self._load()
        assert self._packages is not None
        return self._packages

    def latest_k8s_patch(self, minor: str) -> str:
        self.packages()  # ensure metadata is loaded
        version = max(
            Version(v) for v in self._patch_versions if v.startswith(f"{minor}.")
        )
        return str(version)

    def newest(self, name: str) -> str | None:
        """Location href of the newest build of a package (e.g. cri-tools)."""
        versions = self.packages().get(name)
        if not versions:
            return None
        return max(versions, key=lambda pkg: Version(pkg[0]))[1]

    def exact(self, name: str, ver: str) -> str | None:
        """Location href of a package with the exact version ver."""
        versions = self.packages().get(name)
        if not versions:
            return None
        return next((href for v, href in versions if v == ver), None)


def setup_layout(settings: Settings) -> None:
    for sub in ("rpms", "images", "cilium", "tools"):
        (settings.offline_dir / sub).mkdir(parents=True, exist_ok=True)


def resolve_k8s_version(settings: Settings, repo: RpmRepo) -> str:
    if settings.k8s_version:
        return settings.k8s_version
    print(f"[*] Resolving latest kubelet patch for v{settings.k8s_minor} ...")
    return repo.latest_k8s_patch(settings.k8s_minor)


def download_k8s_rpms(
    settings: Settings, repo: RpmRepo, client: httpx.Client, k8s_ver: str
) -> None:
    for spec in ("kubelet", "kubeadm", "kubectl"):
        href = repo.exact(spec, k8s_ver)
        if href is None:
            sys.exit(f"[error] Could not locate {spec}-{k8s_ver} rpm in repo")
        download(
            client,
            f"{settings.pkgs_base}/{href}",
            settings.offline_dir / "rpms" / Path(href).name,
        )

    for pkg in ("cri-tools", "kubernetes-cni"):
        href = repo.newest(pkg)
        if href is None:
            sys.exit(f"[error] Could not locate {pkg} rpm in repo")
        download(
            client,
            f"{settings.pkgs_base}/{href}",
            settings.offline_dir / "rpms" / Path(href).name,
        )

    print("[*] Downloading containerd.io RPM ...")
    containerd_url = (
        f"https://download.docker.com/linux/centos/10/{settings.docker_arch}"
        f"/stable/Packages/{settings.containerd_rpm}"
    )
    download(
        client, containerd_url, settings.offline_dir / "rpms" / settings.containerd_rpm
    )


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


def ensure_helm(settings: Settings, client: httpx.Client) -> Path | None:
    helm_bin = HELM_DIR / "helm"
    if helm_bin.is_file():
        return helm_bin
    try:
        tarball = HELM_DIR / "helm.tar.gz"
        download(
            client,
            f"https://get.helm.sh/helm-v{settings.helm_ver}-linux-{settings.helm_arch}.tar.gz",
            tarball,
        )
        extract_archive_member(tarball, f"linux-{settings.helm_arch}/helm", helm_bin)
        return helm_bin
    except (httpx.HTTPError, OSError, KeyError, EOFError) as exc:
        print(
            f"[!] helm unavailable ({exc}), using pinned Cilium image list",
            file=sys.stderr,
        )
        return None


def render_cilium_images(
    helm_bin: Path,
    chart_dir: Path,
) -> list[str] | None:
    rendered = subprocess.run(
        [
            str(helm_bin),
            "template",
            "kube-system",
            str(chart_dir),
            "-n",
            "kube-system",
            "--set",
            "kubeProxyReplacement=false",
            "--set",
            "operator.replicas=1",
            "--set",
            "hubble.enabled=false",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if rendered.returncode != 0:
        print(f"[!] helm template failed: {rendered.stderr.strip()}", file=sys.stderr)
        return None
    return parse_image_refs(rendered.stdout)


def parse_image_refs(rendered_yaml: str) -> list[str]:
    """Collect every `image:` string out of the rendered (multi-doc) manifests."""
    images: set[str] = set()
    for doc in yaml.safe_load_all(rendered_yaml):
        walk_images(doc, images)
    return sorted(images)


def walk_images(node: object, acc: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "image" and isinstance(value, str):
                acc.add(value)
            else:
                walk_images(value, acc)
    elif isinstance(node, list):
        for item in node:
            walk_images(item, acc)


def strip_digest(ref: str) -> str:
    return SHA256_DIGEST.sub("", ref)


def sanity_check_cilium_images(settings: Settings, rendered: list[str] | None) -> None:
    if not rendered:
        return
    expected = {strip_digest(img) for img in rendered}
    pinned = set(settings.all_cilium_images())
    for img in sorted(pinned - expected):
        print(
            (
                f"[!] Cilium image '{img}' not in rendered chart list — "
                "check CILIUM_IMAGES"
            ),
            file=sys.stderr,
        )
    for img in sorted(expected - pinned):
        print(
            f"[!] chart renders extra Cilium image '{img}' — add it to CILIUM_IMAGES",
            file=sys.stderr,
        )


def safe_image_name(ref: str) -> str:
    return ref.translate(str.maketrans("/:", "--"))


def docker_pull_save(ref: str, dest: Path) -> None:
    if dest.is_file() and dest.stat().st_size > 0:
        print(f"  [skip] {dest}")
        return
    print(f"[*] docker pull + save {ref}")
    subprocess.run(["docker", "pull", ref], check=True)
    subprocess.run(["docker", "save", "-o", str(dest), ref], check=True)


def write_image_plan(settings: Settings, k8s_ver: str) -> None:
    images_dir = settings.offline_dir / "images"
    control = settings.control_plane_images(k8s_ver)
    common = settings.common_images(k8s_ver)
    cilium = settings.all_cilium_images()

    all_images = sorted(set(control) | set(common) | set(cilium))
    (images_dir / "images.txt").write_text("".join(f"{img}\n" for img in all_images))
    (settings.offline_dir / "k8s-version.txt").write_text(f"v{k8s_ver}\n")

    # "master" images only go on the control-plane node; the rest on every node.
    plan = [f"master {img}" for img in control]
    plan += [f"all {img}" for img in common + cilium]
    (images_dir / "import-plan.txt").write_text("".join(f"{line}\n" for line in plan))

    for scope, images in (
        ("control-plane", control),
        ("common", common),
        ("cilium", cilium),
    ):
        print(
            f"[*] Pulling + saving {scope} container images (this can take a while) ..."
        )
        for img in images:
            fname = safe_image_name(img)
            if scope == "control-plane":
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


def main() -> int:
    settings = parse_args()
    setup_layout(settings)

    with new_client() as client:
        repo = RpmRepo(client, settings.pkgs_base, settings.docker_arch)

        k8s_ver = resolve_k8s_version(settings, repo)
        print(f"[*] Kubernetes version: v{k8s_ver}")

        if not settings.no_rpms:
            print("[*] Downloading k8s RPMs ...")
            download_k8s_rpms(settings, repo, client, k8s_ver)

        download_cilium_artifacts(settings, client)

        print("[*] Downloading helm (host-side, for image-list resolution) ...")
        helm_bin = ensure_helm(settings, client)

    cilium_images_dir = settings.offline_dir / "images"
    chart_dir = settings.offline_dir / "cilium" / "chart"
    if helm_bin and chart_dir.is_dir():
        rendered = render_cilium_images(helm_bin, chart_dir)
        if rendered is not None:
            tags = [strip_digest(img) for img in rendered]
            (cilium_images_dir / "cilium-images.txt").write_text(
                "".join(f"{img}\n" for img in rendered)
            )
            (cilium_images_dir / "cilium-images.tags.txt").write_text(
                "".join(f"{img}\n" for img in sorted(set(tags)))
            )
            print("[*] Cilium images required:")
            for img in sorted(set(tags)):
                print(img)
            sanity_check_cilium_images(settings, rendered)

    write_image_plan(settings, k8s_ver)
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
