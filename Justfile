set shell := ["bash", "-uc"]

# VM names in config.py order (single source of truth)
vm_names := `uv run python -c "import config; print(' '.join(config.VMS))"`

# default concurrency for parallel VM creation
parallel := "4"

default:
    @just --list

# Live Incus instance status
status:
    incus list

# VM names known to config.py
vm-list:
    uv run python -c "import config; print('\n'.join(config.VMS))"

# Create Incus VMs in parallel (one pyinfra process per VM).
# Optional comma-separated subset: just vm-create k8s-master,k8s-worker-1
vm-create vms="":
    @names="{{vms}}"; [ -z "$names" ] && names="{{vm_names}}"; printf '%s\n' "$names" | tr ' ' '\n' | xargs -P {{parallel}} -I{} env INCUS_VMS={} uv run pyinfra -y @local incus/incus_vms.py

# Destroy Incus VMs (default all, or comma-separated subset)
vm-destroy vms="":
    @names="{{vms}}"; [ -z "$names" ] && names="{{vm_names}}"; for vm in ${names//,/ }; do incus delete --force "$vm" 2>/dev/null || true; done

# Download offline bundle (images/tools)
offline:
    uv run python scripts/download_offline.py

# Full cluster orchestration (repo -> prepare -> init -> join -> verify)
all:
    ./scripts/cluster.sh
