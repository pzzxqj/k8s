#!/usr/bin/env bash
# Download everything k8s/containerd/Cilium related onto the HOST, producing an
# offline bundle under ./offline that deploy/prepare.py uploads to the nodes.
#
# Nodes only ever install from these local files; everything else (base
# packages like container-selinux) comes from the node's ONLINE dnf repos.
#
# Requires on the host: curl, docker (for image export), gzip/zcat.
#
#     ./download_offline.sh            # build ./offline
#     OFFLINE_DIR=/path ./download_offline.sh
#
set -euo pipefail

cd "$(dirname "$0")"

OFFLINE_DIR="${OFFLINE_DIR:-offline}"
CILIUM_CHART_VER="${CILIUM_CHART_VER:-1.20.0}"
CILIUM_CLI_VER="${CILIUM_CLI_VER:-0.19.7}"
CONTAINERD_RPM="${CONTAINERD_RPM:-containerd.io-2.3.3-1.el10.x86_64.rpm}"
HELM_VER="${HELM_VER:-3.18.4}"
ARCH="$(uname -m)"

if [ "$ARCH" = "x86_64" ]; then
    DOCKER_ARCH="x86_64"
    HELM_ARCH="amd64"
    CILIUM_ARCH="amd64"
else
    echo "Unsupported host arch: $ARCH" >&2
    exit 1
fi

mkdir -p "$OFFLINE_DIR"/{rpms,images,cilium,tools}

fetch_if_missing() { # <local-path> <url>  (resumable; skips when present)
    if [ -s "$1" ]; then
        echo "  [skip] $1"
        return
    fi
    curl -fL --retry 3 -o "$1" "$2"
}

# ---------------------------------------------------------------------------
# 1. Resolve the latest patch version of the chosen k8s minor from pkgs.k8s.io
# ---------------------------------------------------------------------------
K8S_MINOR="${K8S_MINOR:-1.36}"
PKGS_BASE="https://pkgs.k8s.io/core:/stable:/v${K8S_MINOR}/rpm"

pkgs_primary() {
    local repomd fname
    repomd="$(curl -fsSL "$PKGS_BASE/repodata/repomd.xml")"
    fname="$(printf '%s\n' "$repomd" | sed -n 's/.*href="repodata\/\([^"]*primary[^"]*\)".*/\1/p' | head -1)"
    curl -fsSL "$PKGS_BASE/repodata/$fname" | zcat
}

K8S_VER="${K8S_VERSION:-}"
if [ -z "$K8S_VER" ]; then
    echo "[*] Resolving latest kubelet patch for v$K8S_MINOR ..."
    K8S_VER="$(
        pkgs_primary \
            | grep -oE "ver=\"${K8S_MINOR}\.[0-9]+\"" \
            | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' \
            | sort -V | tail -1
    )"
fi
echo "[*] Kubernetes version: v$K8S_VER"

# ---------------------------------------------------------------------------
# 2. RPMs (containerd.io + kubelet/kubeadm/kubectl/cri-tools/kubernetes-cni)
# ---------------------------------------------------------------------------
rpm_path() { # <pkg> <ver>  ->  prints repo-relative path like x86_64/kubelet-...rpm
    local pkg="$1" ver="$2"
    pkgs_primary | grep -oE "href=\"${DOCKER_ARCH}/${pkg}-${ver}-[^\"]*\.${DOCKER_ARCH}\.rpm\"" \
        | head -1 | sed -e 's/^href="//' -e 's/"$//'
}

K8S_RPMS=(
    "kubelet $K8S_VER"
    "kubeadm $K8S_VER"
    "kubectl $K8S_VER"
)
echo "[*] Downloading k8s RPMs ..."
for spec in "${K8S_RPMS[@]}"; do
    pkg="${spec%% *}"
    ver="${spec##* }"
    rel="$(rpm_path "$pkg" "$ver")"
    if [ -z "$rel" ]; then
        echo "Could not locate $pkg-$ver rpm in repo" >&2
        exit 1
    fi
    fetch_if_missing "$OFFLINE_DIR/rpms/$(basename "$rel")" "$PKGS_BASE/$rel"
done

# cri-tools and kubernetes-cni are versioned independently: take the newest in repo
for pkg in cri-tools kubernetes-cni; do
    rel="$(pkgs_primary | grep -oE "href=\"${DOCKER_ARCH}/${pkg}-[^\"]*\.${DOCKER_ARCH}\.rpm\"" | sed -e 's/^href="//' -e 's/"$//' | sort -V | tail -1)"
    fetch_if_missing "$OFFLINE_DIR/rpms/$(basename "$rel")" "$PKGS_BASE/$rel"
done

echo "[*] Downloading containerd.io RPM ..."
fetch_if_missing "$OFFLINE_DIR/rpms/$CONTAINERD_RPM" \
    "https://download.docker.com/linux/centos/10/${DOCKER_ARCH}/stable/Packages/$CONTAINERD_RPM"

# ---------------------------------------------------------------------------
# 3. Cilium: helm chart + cilium CLI (binaries land in offline/cilium)
# ---------------------------------------------------------------------------
echo "[*] Downloading Cilium $CILIUM_CHART_VER helm chart ..."
fetch_if_missing "$OFFLINE_DIR/cilium/cilium-$CILIUM_CHART_VER.tgz" \
    "https://helm.cilium.io/cilium-$CILIUM_CHART_VER.tgz"
if [ ! -d "$OFFLINE_DIR/cilium/chart" ]; then
    tar -xzf "$OFFLINE_DIR/cilium/cilium-$CILIUM_CHART_VER.tgz" -C "$OFFLINE_DIR/cilium/"
    mv "$OFFLINE_DIR/cilium/cilium" "$OFFLINE_DIR/cilium/chart"
fi

echo "[*] Downloading Cilium CLI v$CILIUM_CLI_VER ..."
if [ ! -x "$OFFLINE_DIR/cilium/cilium" ]; then
    fetch_if_missing "$OFFLINE_DIR/cilium/cilium-cli.tar.gz" \
        "https://github.com/cilium/cilium-cli/releases/download/v${CILIUM_CLI_VER}/cilium-linux-${CILIUM_ARCH}.tar.gz"
    tar -xzf "$OFFLINE_DIR/cilium/cilium-cli.tar.gz" -C "$OFFLINE_DIR/cilium/" cilium
    rm -f "$OFFLINE_DIR/cilium/cilium-cli.tar.gz"
fi

# ---------------------------------------------------------------------------
# 4. Cilium chart image list (authoritative, by digest) via helm template
# ---------------------------------------------------------------------------
echo "[*] Downloading helm (host-side, for image-list resolution) ..."
curl -fL --retry 3 -o /tmp/opencode/helm.tar.gz \
    "https://get.helm.sh/helm-v${HELM_VER}-linux-${HELM_ARCH}.tar.gz" 2>/dev/null || true
if [ ! -x /tmp/opencode/helm ] && [ -f /tmp/opencode/helm.tar.gz ]; then
    tar -xzf /tmp/opencode/helm.tar.gz -C /tmp/opencode linux-${HELM_ARCH}/helm
    mv /tmp/opencode/linux-${HELM_ARCH}/helm /tmp/opencode/helm
fi

if [ -x /tmp/opencode/helm ]; then
    /tmp/opencode/helm template kube-system "$OFFLINE_DIR/cilium/chart" -n kube-system \
        --set kubeProxyReplacement=false \
        --set operator.replicas=1 \
        --set hubble.enabled=false \
        | grep -o 'image: "[^"]*"' | sed -e 's/image: "//' -e 's/"$//' | sort -u \
        > "$OFFLINE_DIR/images/cilium-images.txt"
    # strip @digest for pull-by-tag; warn if any differ
    sed -E 's/@sha256:[0-9a-f]{64}//' "$OFFLINE_DIR/images/cilium-images.txt" \
        > "$OFFLINE_DIR/images/cilium-images.tags.txt"
    echo "[*] Cilium images required:"
    cat "$OFFLINE_DIR/images/cilium-images.tags.txt"
else
    echo "[!] helm unavailable, using pinned Cilium image list" >&2
fi

# ---------------------------------------------------------------------------
# 5. Image list: k8s control plane (from kubeadm constants, source v1.36.3)
# ---------------------------------------------------------------------------
K8S_CONTROL_PLANE_IMAGES=(
    "registry.k8s.io/kube-apiserver:v${K8S_VER}"
    "registry.k8s.io/kube-controller-manager:v${K8S_VER}"
    "registry.k8s.io/kube-scheduler:v${K8S_VER}"
    "registry.k8s.io/etcd:3.6.8-0"
)
K8S_COMMON_IMAGES=(
    "registry.k8s.io/kube-proxy:v${K8S_VER}"
    "registry.k8s.io/pause:3.10.2"
    "registry.k8s.io/coredns/coredns:v1.14.2"
)
CILIUM_IMAGES=(
    "quay.io/cilium/cilium:v${CILIUM_CHART_VER}"
    "quay.io/cilium/operator-generic:v${CILIUM_CHART_VER}"
    "quay.io/cilium/cilium-envoy:v1.37.5-1782911245-7cffc778c923f68a77954a53b1a98d6b5353f004"
)

printf '%s\n' "${K8S_CONTROL_PLANE_IMAGES[@]}" "${K8S_COMMON_IMAGES[@]}" "${CILIUM_IMAGES[@]}" \
    | sort -u > "$OFFLINE_DIR/images/images.txt"
printf 'v%s\n' "$K8S_VER" > "$OFFLINE_DIR/k8s-version.txt"

# Import plan: "<scope> <image ref>" — "master" images only go on the control-plane
# node; everything else lands on all nodes. Filename = scope-prefix + ref with
# '/' and ':' mapped to '-'.
{
    for img in "${K8S_CONTROL_PLANE_IMAGES[@]}"; do echo "master $img"; done
    for img in "${K8S_COMMON_IMAGES[@]}" "${CILIUM_IMAGES[@]}"; do echo "all $img"; done
} > "$OFFLINE_DIR/images/import-plan.txt"

# sanity: the pinned cilium list must match what the chart actually renders
if [ -s "$OFFLINE_DIR/images/cilium-images.tags.txt" ]; then
    mapfile -t expected < "$OFFLINE_DIR/images/cilium-images.tags.txt"
    for img in "${CILIUM_IMAGES[@]}"; do
        if ! printf '%s\n' "${expected[@]}" | grep -qx "$img"; then
            echo "[!] Cilium image '$img' not in rendered chart list — check CILIUM_IMAGES" >&2
        fi
    done
    for img in "${expected[@]}"; do
        if ! printf '%s\n' "${CILIUM_IMAGES[@]}" | grep -qx "$img"; then
            echo "[!] chart renders extra Cilium image '$img' — add it to CILIUM_IMAGES" >&2
        fi
    done
fi

# ---------------------------------------------------------------------------
# 6. Export container images via docker (host pulls, saves as docker-archive)
# ---------------------------------------------------------------------------
img_file() { # <ref> -> safe filename
    printf '%s' "$1" | tr '/:' '--'
}

echo "[*] Pulling + saving container images (this can take a while) ..."
for img in "${K8S_CONTROL_PLANE_IMAGES[@]}"; do
    out="$OFFLINE_DIR/images/master-$(img_file "$img").tar"
    if [ ! -s "$out" ]; then docker pull "$img" && docker save -o "$out" "$img"; else echo "  [skip] $out"; fi
done
for img in "${K8S_COMMON_IMAGES[@]}"; do
    out="$OFFLINE_DIR/images/$(img_file "$img").tar"
    if [ ! -s "$out" ]; then docker pull "$img" && docker save -o "$out" "$img"; else echo "  [skip] $out"; fi
done
for img in "${CILIUM_IMAGES[@]}"; do
    out="$OFFLINE_DIR/images/$(img_file "$img").tar"
    if [ ! -s "$out" ]; then docker pull "$img" && docker save -o "$out" "$img"; else echo "  [skip] $out"; fi
done

# ---------------------------------------------------------------------------
# 7. Manifest
# ---------------------------------------------------------------------------
(
    cd "$OFFLINE_DIR"
    find . -type f | sort
    echo "---"
    find . -type f -exec sha256sum {} \; | sort -k2
) > "$OFFLINE_DIR/MANIFEST.txt"

echo
echo "[*] Done. Bundle: $OFFLINE_DIR"
du -sh "$OFFLINE_DIR" 2>/dev/null || true
