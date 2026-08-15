set shell := ["bash", "-uc"]

# VM names in config.py order (single source of truth)
vm_names := `uv run python -c "import config; print(' '.join(config.VMS))"`

# Topology values from config.py (single source of truth)
ssh_user := `uv run python -c "import config; print(config.SSH_USER)"`
master_ip := `uv run python -c "import config; print(config.MASTER_IP)"`
mirror_ip := `uv run python -c "import config; print(config.REPO_MIRROR_IP)"`
k8s_repo_path := `uv run python -c "import config; print(config.K8S_REPO_SERVED_PATH)"`

# SSH key used for pyinfra / scp / ssh / rsync (SSH_KEY overrides)
key := `echo "${SSH_KEY:-$HOME/.ssh/id_ed25519}"`
offline_dir := env_var_or_default("OFFLINE_DIR", "offline")

default:
    @just --list

# Live Incus instance status
status:
    incus list

# VM names known to config.py
vm-list:
    uv run python -c "import config; print('\n'.join(config.VMS))"

# Create Incus VMs in parallel (thread pool inside incus_vms.py).
# Optional comma-separated subset: just vm-create k8s-master,k8s-worker-1
vm-create vms="":
    @names="{{ vms }}"; [ -z "$names" ] && names="{{ vm_names }}"; names="${names//,/ }"; uv run incus/incus_vms.py $names

# Destroy Incus VMs (default all, or comma-separated subset)
vm-destroy vms="":
    @names="{{ vms }}"; [ -z "$names" ] && names="{{ vm_names }}"; names="${names//,/ }"; uv run incus/incus_vms.py --destroy $names

# Destroy VMs AND wipe k8s-repo's persistent mirror-data volume (--purge-repos-data)
vm-destroy-data vms="":
    @names="{{ vms }}"; [ -z "$names" ] && names="{{ vm_names }}"; names="${names//,/ }"; uv run incus/incus_vms.py --destroy --purge-repos-data $names

# Force-(re)provision the repo mirror config (nginx + reposync script/services).
# Idempotent; does NOT run the sync itself (first sync runs manually, then the
# daily timer). ensure-repo only provisions when the mirrored repo isn't served.
repo:
    uv run pyinfra -y inventory.py deploy/repo.py --limit k8s_repo --user {{ ssh_user }} --key {{ key }}

# Ensure the mirror serves the k8s repodata; provision it only when missing.
ensure-repo:
    @if curl -fsS -o /dev/null "http://{{ mirror_ip }}/{{ k8s_repo_path }}/repodata/repomd.xml"; then \
        echo "[skip] k8s-repo already provisioned"; \
    else \
        uv run pyinfra -y inventory.py deploy/repo.py --limit k8s_repo --user {{ ssh_user }} --key {{ key }}; \
    fi

# Build the offline bundle (images/cilium) and rsync it to all nodes. The mirror
# must be up first (version check inside download_offline.py); ensure-repo handles
# that. Extra flags pass through, e.g. `just offline --no-upload`.
offline args="": ensure-repo
    uv run python scripts/download_offline.py {{ args }}

# Prepare all nodes: kernel/swap/selinux, containerd, k8s RPMs via the mirror,
# preload the offline images into containerd.
prepare: offline
    uv run pyinfra -y inventory.py deploy/prepare.py --limit k8s_nodes --user {{ ssh_user }} --key {{ key }}

# Bootstrap the control plane on the master + install Cilium (offline chart).
init: prepare
    uv run pyinfra -y inventory.py deploy/init.py --limit k8s_master --user {{ ssh_user }} --key {{ key }}

# Fetch the join command from the master, then join the workers.
join: init
    scp -i {{ key }} -o StrictHostKeyChecking=accept-new \
        {{ ssh_user }}@{{ master_ip }}:/etc/kubernetes/join-command.txt \
        {{ offline_dir }}/join-command.txt
    uv run pyinfra -y inventory.py deploy/join.py --limit k8s_workers --user {{ ssh_user }} --key {{ key }}

# Verify the cluster: nodes Ready + core kube-system pods.
verify: join
    ssh -i {{ key }} -o StrictHostKeyChecking=accept-new {{ ssh_user }}@{{ master_ip }} \
        'kubectl get nodes -o wide; echo; kubectl -n kube-system get pods -o wide'

# Full cluster orchestration (repo -> offline -> prepare -> init -> join -> verify)
all: verify
    @echo "== cluster ready =="
