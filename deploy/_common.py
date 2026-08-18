"""Shared constants for the pyinfra deploy scripts and tasks.

Pure, env-agnostic remote path constants used across tasks/. Environment-specific
or per-host tunables (repos, apiserver_port, control_plane_endpoint, ...) live in
group_data/ and reach the tasks via host.data — this module only holds values that
are identical for every host and every environment (fixed toolchain paths).
"""

# ---------- node preparation targets ----------
FSTAB = "/etc/fstab"
SELINUX_CONFIG = "/etc/selinux/config"
CONTAINERD_CONFIG = "/etc/containerd/config.toml"
MODULES_CONF = "/etc/modules-load.d/k8s.conf"
SYSCTL_CONF = "/etc/sysctl.d/k8s.conf"

# ---------- kubeadm control-plane artifacts ----------
ADMIN_CONF = "/etc/kubernetes/admin.conf"
KUBELET_CONF = "/etc/kubernetes/kubelet.conf"
KUBEADM_YAML = "/etc/kubernetes/kubeadm.yaml"
