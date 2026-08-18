#!/usr/bin/env bash
# Mirror Kubernetes rpm repos (pkgs.k8s.io / core:/stable:/<ver>/rpm) verbatim.
#
# pkgs.k8s.io serves no directory listing (403), so lftp mirror cannot be used;
# dnf reposync drives off repodata/ + primary.xml instead. --download-metadata
# copies repodata/ (repomd.xml + primary/filelists/other) byte-for-byte and
# --forcearch=<arch> limits the pull to the native arch. reposync nests the
# whole repo under destdir/<repoid>/; we hoist that back to the upstream layout
# (.</K8S_ARCH//*.rpm + repodata/) so client baseurls just work. repo_gpgcheck
# keys (repomd.xml.key/.asc) are not pulled by reposync, so they are fetched
# separately. Anything no longer referenced by primary.xml is pruned, keeping
# daily runs free of stale rpms.
#
# Adding a newer k8s series: append to K8S_VERSIONS, e.g.
#   K8S_VERSIONS=(v1.36 v1.37) && systemctl start kubernetes-repo-sync.service
set -euo pipefail

K8S_ROOT="${K8S_ROOT:-https://pkgs.k8s.io}"
K8S_DEST="${K8S_DEST:-/srv/repos/kubernetes}"
K8S_ARCH="${K8S_ARCH:-x86_64}"
K8S_VERSIONS=(v1.36)

for v in "${K8S_VERSIONS[@]}"; do
    id="k8s_${v//:/-}"
    url="$K8S_ROOT/core:/stable:/$v/rpm"
    dest="$K8S_DEST/core:/stable:/$v/rpm"
    echo "==[k8s $v]=="
    mkdir -p "$dest"

    dnf reposync --repofrompath="$id,$url" --repoid="$id" \
        --setopt="$id.gpgcheck=0" --setopt="$id.repo_gpgcheck=0" \
        --refresh --download-metadata --arch="$K8S_ARCH" --destdir="$dest"

    srcdir="$dest/$id"
    if [ -d "$srcdir" ]; then
        mkdir -p "$dest/$K8S_ARCH"
        mv -f "$srcdir/$K8S_ARCH"/*.rpm "$dest/$K8S_ARCH/"
        rm -rf "$dest/repodata"
        mv "$srcdir/repodata" "$dest/repodata"
        rm -rf "$srcdir"
    fi

    test -s "$dest/repodata/repomd.xml" || {
        echo "[error] k8s $v: no repodata after sync" >&2
        exit 1
    }

    # repo_gpgcheck keys live in repodata/ but reposync does not fetch them
    curl -fsSL --max-time 30 "$url/repodata/repomd.xml.key" -o "$dest/repodata/repomd.xml.key"
    curl -fsSL --max-time 30 "$url/repodata/repomd.xml.asc" -o "$dest/repodata/repomd.xml.asc"

    python3 - "$dest" "$K8S_ARCH" <<'PY'
import gzip, os, re, sys
dest, arch = sys.argv[1], sys.argv[2]
rdir = os.path.join(dest, "repodata")
prim = next((os.path.join(rdir, f) for f in os.listdir(rdir) if f.endswith("-primary.xml.gz")), None)
if prim is None:
    print("[check] primary.xml.gz not found, skipping verification", file=sys.stderr)
    sys.exit(0)
data = gzip.open(prim, "rt").read()
references = set(re.findall(r'<location href="%s/([^"]+\.rpm)"' % re.escape(arch), data))
adir = os.path.join(dest, arch)
on_disk = {f for f in os.listdir(adir) if f.endswith(".rpm")}
for stale in on_disk - references:
    os.remove(os.path.join(adir, stale))
    print("[prune] removed stale %s" % stale)
missing = references - on_disk
print("[check] %s: primary lists %d rpms, %d on disk, %d missing"
      % (arch, len(references), len(references - missing), len(missing)))
sys.exit(0 if not missing else 1)
PY
done

echo "== done =="