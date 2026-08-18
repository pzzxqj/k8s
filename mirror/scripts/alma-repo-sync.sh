#!/usr/bin/env bash
# Mirror AlmaLinux 10 rpm repos (x86_64 + x86_64_v2) from NJU over HTTPS.
#
# rsync (TCP 873) is blackholed at the payload stage by the egress device on
# this host, so we mirror with lftp over HTTPS instead — same verbatim result:
# upstream Packages/ + repodata/ (incl. AppStream modulemd) kept byte-for-byte,
# --delete prunes files removed upstream, --only-newer keeps daily runs cheap.
# Any lftp/network failure exits non-zero (set -e) so the oneshot service is
# reported as failed and nothing half-mirrored is assumed healthy.
set -euo pipefail

UP="${ALMA_UPSTREAM:-https://mirrors.nju.edu.cn/almalinux/10}"
DST="${ALMA_DEST:-/srv/repos/almalinux/10}"
REPOS=(BaseOS AppStream CRB extras HighAvailability NFV RT SAP SAPHANA)
ARCHS=(x86_64 x86_64_v2)

mkdir -p "$DST"

for repo in "${REPOS[@]}"; do
    for arch in "${ARCHS[@]}"; do
        src="$repo/$arch/os"
        dest="$DST/$repo/$arch/os"
        echo "==[$repo/$arch]=="
        mkdir -p "$dest"
        # use-pget-n 1: single stream per file. Parallel Range-chunked pget was
        # failing on large files during bursts (egress device resets). File-level
        # parallelism (--parallel=8) keeps throughput high; each file retries more.
        lftp -e "set net:timeout 30; set net:max-retries 8; set net:reconnect-interval-base 5; \
set mirror:use-pget-n 1; open '$UP'; mirror --delete --only-newer --parallel=8 '$src' '$dest'; quit"
        test -s "$dest/repodata/repomd.xml" || {
            echo "[error] $repo/$arch: no repodata after sync" >&2
            exit 1
        }
    done
done

echo "== done =="