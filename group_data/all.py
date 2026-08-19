# pyinfra group data: defaults shared by every host of every file-based
# inventory run from the repo root (lowest-priority data source).
#
# Values here are defaults; group data (k8s_test.py / k8s_production.py) and
# per-host data in the inventories override them (see data hierarchy in the
# pyinfra docs).

# kubeadm / cluster defaults (test and production share them today). The `repos`
# default (which dnf sources a host manages) lives in the environment group data
# (k8s_test.py / k8s_production.py); per-host data in the inventories only
# overrides it with a subset (e.g. ["alma"]) for non-k8s hosts.
apiserver_port = "6443"
service_subnet = "10.96.0.0/12"
node_offline_dir = "/opt/k8s-offline"

# HA control plane endpoint: set the Keepalived VIP per environment (e.g.
# group_data/k8s_test.py). Only used when the control plane is HA (>1 master);
# None keeps the classic single-master layout (endpoint = the bootstrap master's
# ssh_hostname, no LB installed).
control_plane_endpoint = None
# Keepalived VRRP defaults (auth_pass is limited to 8 chars in PASS mode).
vrrp_pass = "k8svrrp1"
vrrp_cidr = "24"