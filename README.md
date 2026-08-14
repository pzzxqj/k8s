# k8s 离线实验集群

在 Incus 的 3 台 AlmaLinux 10 VM 上，用 **kubeadm + containerd + Cilium** 部署一个学习用的 Kubernetes v1.36 集群。k8s/containerd/Cilium 相关组件全部**离线安装**（宿主机下载 → 上传到节点），仅系统基础包走节点的在线 dnf —— 用于模拟内网受限环境。

## 拓扑

| 节点 | IP | 内存 | 角色 |
|---|---|---|---|
| k8s-master | 10.98.68.10 | 6GiB | control-plane + Cilium |
| k8s-worker-1 | 10.98.68.11 | 3GiB | worker |
| k8s-worker-2 | 10.98.68.12 | 3GiB | worker |

单点修改处：`config.py`（拓扑、目录、VM 规格）；artifact 版本固定在 `scripts/download_offline.py`。

## 组件版本

- Kubernetes **v1.36.3**（pkgs.k8s.io，含 kubelet/kubeadm/kubectl/cri-tools/kubernetes-cni）
- containerd **containerd.io 2.3.3**（download.docker.com el10 RPM）
- Cilium **1.20.0**（最小化：cilium + operator-generic + cilium-envoy，无 Hubble，保留 kube-proxy）
- cgroup 驱动：**systemd**（containerd `SystemdCgroup=true` + kubelet `cgroupDriver: systemd`）

## 目录结构

```
config.py                # 单一事实来源：拓扑/目录/VM 规格
inventory.py             # pyinfra 分组（k8s_nodes / k8s_master / k8s_workers）
deploy/                  # pyinfra 部署脚本
  prepare.py             #   所有节点：内核/containerd/k8s RPM/镜像预载
  init.py                #   master：kubeadm init + Cilium 离线安装
  join.py                #   workers：kubeadm join
incus/incus_vms.py       # 创建/销毁 VM
templates/               # 远程配置文件 jinja2 模板（containerd.toml、kubeadm.yaml）
scripts/
  download_offline.py    # 宿主机下载离线包到 ./offline
  import_images.sh       # 节点端镜像导入助手（随包上传到 /opt/k8s-offline/）
  cluster.sh             # 一键编排全流程
offline/                 # 生成的离线包（已 gitignore）
```

## 快速开始

```bash
# 1. 建 VM（可选，已有则跳过）
uv run pyinfra @local incus/incus_vms.py

# 2. 宿主机下载离线包（可断点续传/幂等）
uv run python scripts/download_offline.py

# 3. 一键部署：prepare → init(master) → join(workers) → verify
./scripts/cluster.sh
```

手动执行单个阶段：

```bash
uv run pyinfra -y inventory.py deploy/prepare.py --user tux --key ~/.ssh/id_ed25519
uv run pyinfra -y inventory.py deploy/init.py   --limit k8s_master  --user tux --key ~/.ssh/id_ed25519
uv run pyinfra -y inventory.py deploy/join.py   --limit k8s_workers --user tux --key ~/.ssh/id_ed25519
```

## 验证

```bash
ssh tux@10.98.68.10
kubectl get nodes -o wide          # 3 个节点 Ready
cilium status                      # Cilium / Operator / Envoy OK
curl -sk https://10.96.0.1:443/version   # ClusterIP Service 路径
```

## 常见问题

- **coredns 探针超时 / NotReady**：kubelet 被重启（如 RPM 重装）后偶发 veth/endpoint 状态残留。删掉 coredns Pod 让其重建即可：`kubectl -n kube-system delete pods -l k8s-app=kube-dns`。deploy/prepare.py 已用 `rpm -q` 守卫避免重跑时重装 kubelet。
- **离线拉取镜像失败**：镜像 tag 与 kubeadm/Cilium chart 不一致。镜像清单来自 `kubeadm` 常量（deploy 版本见 `scripts/download_offline.py`）与 `helm template` 渲染结果，重新跑 `uv run python scripts/download_offline.py` 会重新生成 `offline/images/import-plan.txt`。
- **幂等**：所有脚本可重复执行；`cluster.sh` 重跑全为 No-change/跳过，且不重启运行中的 kubelet。
