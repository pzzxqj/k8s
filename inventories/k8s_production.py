# pyinfra inventory: k8s PRODUCTION cluster (192.168.90.x).

# Same layout as inventories/k8s_test.py: Host NAME = logical label, Host DATA =
# ssh_hostname / ssh_user. Repos are pointed at the intranet mirror
# http://192.168.90.201 (see group_data/k8s_production.py), which also carries
# the default `repos` subset.

# Servers that only need the AlmaLinux 10 source can live here too: override
# `repos` in their per-host data with ["alma"] and run `deploy/repos.py` against
# them.

control_plane = [
    (
        "k8s-master1",
        {
            "ssh_hostname": "192.168.90.220",
            "ssh_user": "zhch",
        },
    ),
]

workers = [
    (
        "k8s-worker1",
        {
            "ssh_hostname": "192.168.90.223",
            "ssh_user": "zhch",
        },
    ),
    (
        "k8s-worker2",
        {
            "ssh_hostname": "192.168.90.224",
            "ssh_user": "zhch",
        },
    ),
]

nodes = control_plane + workers
