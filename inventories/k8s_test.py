# pyinfra inventory: k8s TEST cluster (Incus lab, 10.98.68.x).

# Host NAME is the logical label (pyinfra.host.name); connection details are
# Host DATA via the SSH connector keys (ssh_hostname / ssh_user) plus our own
# data (repos: which dnf source files this host manages). The three repo tasks
# (tasks/alma_repos.py, tasks/kubernetes_repo.py, tasks/docker_ce_repo.py)
# default to all three sources; drop entries here to skip a source per host.

# In the k8s test environment the "intranet mirror" IS the upstream: repos are
# pointed straight at NJU / pkgs.k8s.io / download.docker.com (see
# group_data/k8s_test.py). The test env has no internal mirror.

control_plane = [
    (
        "k8s-master",
        {
            "ssh_hostname": "10.98.68.10",
            "ssh_user": "admin",
            "repos": ["alma", "kubernetes", "docker-ce"],
        },
    ),
    (
        "k8s-master-2",
        {
            "ssh_hostname": "10.98.68.14",
            "ssh_user": "admin",
            "repos": ["alma", "kubernetes", "docker-ce"],
        },
    ),
    (
        "k8s-master-3",
        {
            "ssh_hostname": "10.98.68.15",
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
    (
        "k8s-worker-3",
        {
            "ssh_hostname": "10.98.68.16",
            "ssh_user": "admin",
            "repos": ["alma", "kubernetes", "docker-ce"],
        },
    ),
]

nodes = control_plane + workers