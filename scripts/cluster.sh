#!/usr/bin/env bash
# End-to-end driver for the k8s lab:
#   setup repo mirror -> prepare all nodes -> init master -> fetch join cmd
#   -> join workers -> verify
#
# Topology (master/worker/mirror IPs) is read from config.py; inventory.py
# derives the pyinfra groups used with --limit.
#
# Prereqs: k8s-repo VM created; ./scripts/download_offline.py run once; VMs
# sized (see config.VMS).
#
#     ./scripts/cluster.sh
#
set -euo pipefail
cd "$(dirname "$0")/.."

SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
OFFLINE_DIR="${OFFLINE_DIR:-offline}"
SSH_USER="${SSH_USER:-$(uv run python -c "import config; print(config.SSH_USER)" 2>/dev/null || echo admin)}"

# single source of truth for the topology (fall back to hardcoded defaults if
# config.py can't be read)
MASTER_IP="${MASTER_IP:-$(uv run python -c "import config; print(config.MASTER_IP)" 2>/dev/null || echo 10.98.68.10)}"
WORKERS="${WORKERS:-$(uv run python -c "import config; print(','.join(config.WORKER_IPS))" 2>/dev/null || echo 10.98.68.11,10.98.68.12)}"
export WORKERS

run() {
    uv run pyinfra "$@"
}

echo "== [0/6] deploy/repo.py: provision internal repo mirror (nginx + reposync) =="
run -y inventory.py deploy/repo.py --limit k8s_repo --user "$SSH_USER" --key "$SSH_KEY"

echo "== [1/6] deploy/prepare.py: all nodes (kernel, containerd, k8s RPMs via mirror, images) =="
run -y inventory.py deploy/prepare.py --limit k8s_nodes --user "$SSH_USER" --key "$SSH_KEY"

echo "== [2/6] deploy/init.py: bootstrap control plane on master =="
run -y inventory.py deploy/init.py --limit k8s_master --user "$SSH_USER" --key "$SSH_KEY"

echo "== [3/6] fetch worker join command from master =="
scp -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new \
    "$SSH_USER@$MASTER_IP:/etc/kubernetes/join-command.txt" \
    "$OFFLINE_DIR/join-command.txt"

echo "== [4/6] deploy/join.py: join workers =="
run -y inventory.py deploy/join.py --limit k8s_workers --user "$SSH_USER" --key "$SSH_KEY"

echo "== [5/6] verify cluster =="
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$SSH_USER@$MASTER_IP" \
    'kubectl get nodes -o wide; echo; kubectl -n kube-system get pods -o wide'
