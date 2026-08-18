set shell := ["bash", "-uc"]

# VM names in config.py order (single source of truth)
vm_names := `uv run python -c "import config; print(' '.join(config.VMS))"`

# Topology values from config.py (single source of truth)
ssh_user := `uv run python -c "import config; print(config.SSH_USER)"`
master_ip := `uv run python -c "import config; print(config.MASTER_IP)"`

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

# Build the offline bundle (images/cilium) and rsync it to all nodes. k8s RPMs
# come from the repos (pkgs.k8s.io etc.), so nothing to provision first;
# k8s_download_offline.py verifies the host kubeadm matches the upstream k8s version.
# Extra flags pass through, e.g. `just offline --no-upload` or
# `just offline --inventory inventories/k8s_production.py` for the production cluster.
offline args="":
    uv run python scripts/k8s_download_offline.py --inventory inventories/k8s_test.py {{ args }}

# Point the servers' dnf sources where their Host Data / group_data say
# (k8s_test = upstream, k8s_production = intranet mirror); per-host `repos`
# subset respected. REPO_INVENTORY defaults to the k8s test cluster.
repos inventory="inventories/k8s_test.py":
    uv run pyinfra -y {{ inventory }} deploy/repos.py

# Prepare all nodes: kernel/swap/selinux, containerd, k8s RPMs (repos from
# group_data/k8s_test.py = upstream), preload the offline images into containerd.
# SSH user comes from Host Data; auth via the local ssh-agent / default keys.
prepare: offline
    uv run pyinfra -y inventories/k8s_test.py deploy/k8s_prepare.py --limit nodes

# Bootstrap the control plane on the master + install Cilium (offline chart).
init: prepare
    uv run pyinfra -y inventories/k8s_test.py deploy/k8s_init.py --limit control_plane

# Fetch the join command from the master, then join the workers.
join: init
    scp -i {{ key }} -o StrictHostKeyChecking=accept-new \
        {{ ssh_user }}@{{ master_ip }}:/etc/kubernetes/join-command.txt \
        {{ offline_dir }}/join-command.txt
    uv run pyinfra -y inventories/k8s_test.py deploy/k8s_join.py --limit workers

# Verify the cluster: fetch the admin kubeconfig (the /etc/kubernetes/admin.conf
# copy is 0600 root-only; ~admin/.kube/config is the identical admin-readable
# copy installed by init.py), then check nodes Ready + Cilium (no kube-proxy) +
# coredns + kube-system pods via the kubernetes client.
verify: join
    scp -i {{ key }} -o StrictHostKeyChecking=accept-new \
        {{ ssh_user }}@{{ master_ip }}:/home/{{ ssh_user }}/.kube/config \
        {{ offline_dir }}/admin.conf
    uv run python scripts/k8s_verify_cluster.py --kubeconfig {{ offline_dir }}/admin.conf

# Full cluster orchestration (offline -> prepare -> init -> join -> verify)
all: verify
    @echo "== cluster ready =="