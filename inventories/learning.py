# pyinfra inventory: learning (Incus lab) k8s cluster.

# Host NAME is the logical label (pyinfra.host.name); connection details are
# Host DATA via the SSH connector keys (ssh_hostname / ssh_user) plus our own
# data (repos: which dnf source files this host manages). The three repo tasks
# (tasks/alma_repos.py, tasks/kubernetes_repo.py, tasks/docker_ce_repo.py)
# default to all three sources; drop entries here to skip a source per host.

# In the learning environment the "intranet mirror" IS the upstream: repos are
# pointed straight at NJU / pkgs.k8s.io / download.docker.com (see
# group_data/learning.py). The learning env has no internal mirror.

control_plane = [
    (
        "k8s-master",
        {
            "ssh_hostname": "10.98.68.10",
            "ssh_user": "admin",
            "repos": ["alma", "kubernetes", "docker-ce"],
        },
    ),
]

workers = [
    (
        "k8s-worker-1",
        {
            "ssh_hostname": "10.98.68.11",
            "ssh_user": "admin",
            "repos": ["alma", "kubernetes", "docker-ce"],
        },
    ),
    (
        "k8s-worker-2",
        {
            "ssh_hostname": "10.98.68.12",
            "ssh_user": "admin",
            "repos": ["alma", "kubernetes", "docker-ce"],
        },
    ),
]

nodes = control_plane + workers