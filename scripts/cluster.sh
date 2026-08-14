#!/usr/bin/env bash
# End-to-end driver for the offline k8s lab:
#   prepare all nodes -> init master -> fetch join cmd -> join workers -> verify
#
# Topology (master/worker IPs) is read from config.py; inventory.py derives the
# pyinfra groups used with --limit.
#
# Prereqs: ./scripts/download_offline.py run once; VMs sized (see config.VMS).
#
#     ./scripts/cluster.sh
#
set -euo pipefail
cd "$(dirname "$0")/.."

SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
OFFLINE_DIR="${OFFLINE_DIR:-offline}"

# single source of truth for the topology (fall back to hardcoded defaults if
# config.py can't be read)
MASTER_IP="${MASTER_IP:-$(uv run python -c "import config; print(config.MASTER_IP)" 2>/dev/null || echo 10.98.68.10)}"
WORKERS="${WORKERS:-$(uv run python -c "import config; print(','.join(config.WORKER_IPS))" 2>/dev/null || echo 10.98.68.11,10.98.68.12)}"
export WORKERS

run() {
    uv run pyinfra "$@"
}

echo "== [1/5] deploy/prepare.py: all nodes (kernel, containerd, k8s RPMs, images) =="
run -y inventory.py deploy/prepare.py --user tux --key "$SSH_KEY"

echo "== [2/5] deploy/init.py: bootstrap control plane on master =="
run -y inventory.py deploy/init.py --limit k8s_master --user tux --key "$SSH_KEY"

echo "== [3/5] fetch worker join command from master =="
scp -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new \
    "tux@$MASTER_IP:/etc/kubernetes/join-command.txt" \
    "$OFFLINE_DIR/join-command.txt"

echo "== [4/5] deploy/join.py: join workers =="
run -y inventory.py deploy/join.py --limit k8s_workers --user tux --key "$SSH_KEY"

echo "== [5/5] verify cluster =="
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "tux@$MASTER_IP" \
    'kubectl get nodes -o wide; echo; kubectl -n kube-system get pods -o wide'
