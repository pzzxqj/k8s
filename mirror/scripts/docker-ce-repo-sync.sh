#!/usr/bin/env bash
# Mirror the Docker CE rpm repo (el10 x86_64 stable) from Aliyun over HTTPS.
#
# Aliyun serves a custom HTML listing; lftp parses it, but the page also
# carries absolute-pathed nav links (docker-ce/centos/ubuntu/pypi) that lftp
# sees as pseudo-directories. We exclude exactly those names with
# --exclude-rx, so the mirrored tree matches upstream byte-for-byte. The
# client-side GPG key (linux/centos/gpg) is copied separately so nodes in the
# intranet can gpgcheck without touching the internet. Any failure exits
# non-zero (set -e) so the oneshot service is reported as failed.
set -euo pipefail

DOCKER_UP="${DOCKER_UPSTREAM:-https://mirrors.aliyun.com/docker-ce}"
DOCKER_DEST="${DOCKER_DEST:-/srv/repos/docker-ce}"
REL="linux/centos/10/x86_64/stable"
NAV_JUNK="^(docker-ce|centos|ubuntu|pypi)$"

mkdir -p "$DOCKER_DEST/$REL" "$DOCKER_DEST/linux/centos"

echo "==[docker-ce stable]=="
lftp -e "set net:timeout 30; set net:max-retries 8; set net:reconnect-interval-base 5; \
set mirror:use-pget-n 1; open '$DOCKER_UP'; \
mirror --only-newer --delete --exclude='$NAV_JUNK' --parallel=8 '$REL' '$DOCKER_DEST/$REL'; quit"

test -s "$DOCKER_DEST/$REL/repodata/repomd.xml" || {
    echo "[error] docker-ce: no repodata after sync" >&2
    exit 1
}

echo "==[docker-ce centos gpg key]=="
curl -fsSL --max-time 60 "$DOCKER_UP/linux/centos/gpg" -o "$DOCKER_DEST/linux/centos/gpg"

echo "== done =="