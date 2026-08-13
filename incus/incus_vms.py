"""pyinfra: batch create/destroy Incus VMs for a k8s lab.

VM specs come from config.py (single source of truth).

Run:
    uv run pyinfra @local incus/incus_vms.py             # create all
    INCUS_VMS=k8s-master uv run pyinfra @local incus/incus_vms.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from pyinfra.context import host  # noqa: E402
from pyinfra.facts.server import Command  # noqa: E402
from pyinfra.operations import server  # noqa: E402

IMAGE = os.environ.get("INCUS_IMAGE", "images:almalinux/10/cloud")
NETWORK = os.environ.get("INCUS_NETWORK", "incusbr0")
USER = os.environ.get("INCUS_USER", config.SSH_USER)
SSH_PUB_KEY = os.environ.get(
    "INCUS_SSH_KEY", os.path.expanduser("~/.ssh/id_ed25519.pub")
)
VM_SELECT = [x.strip() for x in os.environ.get("INCUS_VMS", "").split(",") if x.strip()]
LAB_PASSWORD_HASH = os.environ.get(
    "INCUS_PASSWORD_HASH",
    "$6$cmjfM7yK3xEZRLk0$GGVqHsGbqo5KM1GcK7LsHea67v772D/FxDYA9vQQdOJpiM0zJK51VBhzDQaRV6mdLczz8Ls1ic1/zh74PtnUr/",
)

VMS = config.VMS


def vm_exists(name: str) -> bool:
    out = host.get_fact(Command, command=f"incus list --format=compact {name}")
    return bool(out) and name in out


def user_data(name: str) -> str:
    hosts = "\n".join(f"      {s['ip']} {n}" for n, s in VMS.items())
    with open(SSH_PUB_KEY) as f:
        key = f.read().strip()
    hosts_file = f"      127.0.0.1   localhost\n{hosts}"
    return f"""#cloud-config
hostname: {name}
users:
  - name: {USER}
    groups: [wheel]
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: false
    passwd: {LAB_PASSWORD_HASH}
    ssh_authorized_keys:
      - {key}
ssh_pwauth: false
write_files:
  - path: /etc/hosts
    content: |
{hosts_file}
runcmd:
  - dnf -y install openssh-server
  - systemctl enable --now sshd
  - sh -c 'grep -q "^Subsystem sftp" /etc/ssh/sshd_config || echo "Subsystem sftp /usr/libexec/openssh/sftp-server" >> /etc/ssh/sshd_config'
  - systemctl restart sshd
"""


def create_vms():
    for name, spec in VMS.items():
        if VM_SELECT and name not in VM_SELECT:
            continue
        if vm_exists(name):
            print(f"[skip] {name} already exists")
            continue
        server.shell(
            commands=[
                f"incus init {IMAGE} {name} --vm -d root,size={spec['disk']}",
                f"incus config set {name} limits.cpu={spec['vcpu']}",
                f"incus config set {name} limits.memory={spec['memory']}",
                f"incus config set {name} cloud-init.user-data - <<'CLOUDINIT'\n{user_data(name)}\nCLOUDINIT",
                f"incus config device add {name} agent disk source=agent:config",
                f"incus config device override {name} eth0 network={NETWORK} ipv4.address={spec['ip']}",
                f"incus start {name}",
            ]
        )
        server.shell(commands=[f"incus wait {name} agent"], _ignore_errors=True)


create_vms()
