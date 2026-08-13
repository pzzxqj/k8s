#!/usr/bin/env bash
# Import the offline image tarballs into the k8s.io containerd namespace.
# Usage: import_images.sh <all|master>
#   all    - import only "all"-scope images (workers)
#   master - import every image, including control-plane-only tars (master)
#
# The image->scope mapping comes from /opt/k8s-offline/images/import-plan.txt
# produced by download_offline.sh. Re-running is safe: already-present images
# are skipped.
set -euo pipefail

IMAGES_DIR="${IMAGES_DIR:-/opt/k8s-offline/images}"
NS="k8s.io"
SCOPE="${1:?usage: import_images.sh <all|master>}"

command -v ctr >/dev/null 2>&1 || { echo "ctr not found" >&2; exit 1; }

import_one() { # <scope> <ref>
    local scope="$1" ref="$2" fname tar
    if [ "$scope" = master ] && [ "$SCOPE" != master ]; then
        return
    fi
    fname="$(printf '%s' "$ref" | tr '/:' '--')"
    [ "$scope" = master ] && fname="master-${fname}"
    tar="$IMAGES_DIR/${fname}.tar"
    if [ ! -f "$tar" ]; then
        echo "[warn] missing tar for $ref ($tar)" >&2
        return
    fi
    if ctr -n "$NS" images ls -q 2>/dev/null | grep -Fxq -- "$ref"; then
        echo "[skip] $ref"
        return
    fi
    echo "[import] $ref"
    ctr -n "$NS" images import "$tar"
}

while read -r scope ref; do
    [ -z "$ref" ] && continue
    import_one "$scope" "$ref"
done < "$IMAGES_DIR/import-plan.txt"
