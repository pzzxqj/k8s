# pyinfra group data: defaults shared by every host of every file-based
# inventory run from the repo root (lowest-priority data source).
#
# Values here are defaults; group data (k8s_test.py / k8s_production.py) and
# per-host data in the inventories override them (see data hierarchy in the
# pyinfra docs).

# dnf sources this host manages (atomic repo tasks honour the subset):
#   alma          -> tasks/alma_repos.py        (in-place baseurl/mirrorlist edit)
#   kubernetes    -> tasks/kubernetes_repo.py   (managed kubernetes.repo)
#   docker-ce     -> tasks/docker_ce_repo.py    (managed docker-ce.repo)
repos = ["alma", "kubernetes", "docker-ce"]

# kubeadm / cluster defaults (test and production share them today).
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