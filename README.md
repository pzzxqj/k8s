# k8s 内网实验集群

在 Incus 的 3 台 AlmaLinux 10 VM 上，用 **kubeadm + containerd + Cilium** 部署一个学习用的 Kubernetes v1.36 集群，另加 **1 台内网软件源镜像 VM**。
k8s/containerd 组件通过 **内网 dnf 镜像源**（k8s-repo）安装；容器镜像与 Cilium 走**离线包**（宿主机下载 → 上传到节点）；系统基础包走节点的在线 Alma dnf。整体模拟真实内网受限环境。

## 拓扑

| 节点 | IP | 内存 | 角色 |
|---|---|---|---|
| k8s-repo | 10.98.68.13 | 2GiB | 内网 RPM 镜像源（pkgs.k8s.io + docker/containerd，nginx + reposync） |
| k8s-master | 10.98.68.10 | 6GiB | control-plane + Cilium |
| k8s-worker-1 | 10.98.68.11 | 3GiB | worker |
| k8s-worker-2 | 10.98.68.12 | 3GiB | worker |

单点修改处：`config.py`（拓扑、目录、VM 规格）；artifact 版本固定在 `scripts/download_offline.py`。

## 组件来源

- **k8s RPM**（kubelet/kubeadm/kubectl/cri-tools/kubernetes-cni）→ 内网镜像 `http://10.98.68.13/k8s/core:/stable:/v1.36/rpm/`（`k8s-repo` 从 pkgs.k8s.io `dnf reposync` 同步，`repo-sync.timer` 每日更新）
- **containerd.io 2.3.3** → 内网镜像 `http://10.98.68.13/docker/linux/centos/10/x86_64/stable/`
- **容器镜像 + Cilium CLI/chart** → 离线包 `./offline`（images/ 预载到 containerd）
- **系统基础包**（container-selinux 等）→ 节点在线 Alma 仓库（VM 创建时 cloud-init 已把仓库指向 NJU 镜像 mirrors.nju.edu.cn，与 deploy/repo.py 一致）

## 目录结构

```
config.py                # 单一事实来源：拓扑/目录/VM 规格/镜像源常量
inventory.py             # pyinfra 分组（k8s_nodes / k8s_master / k8s_workers / k8s_repo）
Justfile                 # just 命令入口：vm-create 并行建 VM / vm-destroy / offline / all
deploy/                  # pyinfra 部署脚本
  repo.py                #   k8s-repo：nginx + reposync 镜像源（首次同步 + 每日 timer）
  prepare.py             #   所有节点：内核/镜像源指向/containerd/k8s RPM/镜像预载
  init.py                #   master：kubeadm init + Cilium 离线安装
  join.py                #   workers：kubeadm join
incus/incus_vms.py       # 创建/销毁 VM
templates/               # 远程配置文件 jinja2 模板
  kubernetes.repo.j2     #   节点端 k8s dnf 源（指向 k8s-repo）
  docker-ce.repo.j2      #   节点端 containerd dnf 源（指向 k8s-repo）
  mirror/                #   镜像 VM 端模板（nginx、repo-sync、timer、上游源）
scripts/
  download_offline.py    # 宿主机下载离线包到 ./offline
  import_images.sh       # 节点端镜像导入助手（随包上传到 /opt/k8s-offline/）
  cluster.sh             # 一键编排全流程
offline/                 # 生成的离线包（已 gitignore）
```

## 快速开始

```bash
# 1. 建 VM（可选，已有则跳过）
just vm-create   # 并行创建全部 VM（每个 VM 一个 pyinfra 进程）；子集：just vm-create k8s-master,k8s-worker-1

# 2. 宿主机下载离线包-仅镜像/工具与容器镜像（可断点续传/幂等；k8s/containerd RPM 由镜像源提供，不在 bundle 中）
uv run python scripts/download_offline.py

# 3. 一键部署：镜像源 → prepare → init(master) → join(workers) → verify
./scripts/cluster.sh
```

手动执行单个阶段：

```bash
# 先建好镜像源 VM
uv run pyinfra -y inventory.py deploy/repo.py --limit k8s_repo --user admin --key ~/.ssh/id_ed25519

# 再部署集群
uv run pyinfra -y inventory.py deploy/prepare.py --limit k8s_nodes --user admin --key ~/.ssh/id_ed25519
uv run pyinfra -y inventory.py deploy/init.py   --limit k8s_master  --user admin --key ~/.ssh/id_ed25519
uv run pyinfra -y inventory.py deploy/join.py   --limit k8s_workers --user admin --key ~/.ssh/id_ed25519
```

## 验证

```bash
ssh admin@10.98.68.13 curl -s http://localhost/k8s/core:/stable:/v1.36/rpm/  # 镜像源 repodata
ssh admin@10.98.68.10
kubectl get nodes -o wide          # 3 个节点 Ready
cilium status                      # Cilium / Operator / Envoy OK
curl -sk https://10.96.0.1:443/version   # ClusterIP Service 路径
```

## 常见问题

- **coredns 探针超时 / NotReady**：kubelet 被重启（如 RPM 重装）后偶发 veth/endpoint 状态残留。删掉 coredns Pod 让其重建即可：`kubectl -n kube-system delete pods -l k8s-app=kube-dns`。deploy/prepare.py 已用 `rpm -q` 守卫避免重跑时重装 kubelet。
- **离线拉取镜像失败**：镜像 tag 与 kubeadm/Cilium chart 不一致。镜像清单来自 `kubeadm` 常量（deploy 版本见 `scripts/download_offline.py`）与 `helm template` 渲染结果，重新跑 `uv run python scripts/download_offline.py` 会重新生成 `offline/images/import-plan.txt`。
- **幂等**：所有脚本可重复执行；`cluster.sh` 重跑全为 No-change/跳过，且不重启运行中的 kubelet。
