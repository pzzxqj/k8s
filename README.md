# k8s 内网实验集群

在 Incus 的 3 台 AlmaLinux 10 VM 上，用 **kubeadm + containerd + Cilium** 部署一个学习用的 Kubernetes v1.36 集群，另加 **1 台内网软件源镜像 VM**。
k8s/containerd 组件通过 **内网 dnf 镜像源**（k8s-repo）安装；容器镜像与 Cilium 走**离线包**（宿主机下载 → 上传到节点）；系统基础包同样由镜像源 k8s-repo 的 Alma 子 repo 提供（见下）。整体模拟真实内网受限环境。

## 拓扑

| 节点 | IP | 内存 | 角色 |
|---|---|---|---|
| k8s-repo | 10.98.68.13 | 2GiB | 内网 RPM 镜像源（pkgs.k8s.io + docker/containerd，nginx + reposync） |
| k8s-master | 10.98.68.10 | 6GiB | control-plane + Cilium |
| k8s-worker-1 | 10.98.68.11 | 3GiB | worker |
| k8s-worker-2 | 10.98.68.12 | 3GiB | worker |

单点修改处：`config.py`（拓扑、目录、VM 规格）；Cilium chart/CLI 版本固定在 `scripts/download_offline.py`；k8s 版本跟随**宿主机本地 kubeadm**（`kubeadm config images list`，须与镜像源内 RPM 同版本）。

## 组件来源

- **k8s RPM**（kubelet/kubeadm/kubectl/cri-tools/kubernetes-cni）→ 内网镜像 `http://10.98.68.13/k8s/core:/stable:/v1.36/rpm/`（`k8s-repo` 从 pkgs.k8s.io `dnf reposync` 同步，`repo-sync.timer` 每日更新）
- **containerd.io 2.3.3** → 内网镜像 `http://10.98.68.13/docker/linux/centos/10/x86_64/stable/`
- **容器镜像 + Cilium CLI/chart** → 离线包 `./offline`（images/ 预载到 containerd；`download_offline.py` 用 rsync 直接推到各节点 `/opt/k8s-offline`，不经 pyinfra）
- **系统基础包**（kernel-modules-extra、container-selinux 等）→ 镜像 `k8s-repo` 的 `almalinux/10/BaseOS|AppStream/` 规范布局子 repo（学习环境，仅镜像部署 k8s 所需包及其依赖闭包；镜像机/节点的 `almalinux-*.repo` 均为受管模板 `templates/alma-repo/`，见 `deploy/_alma_repos.py`：k8s-repo 还原原始全部启用（NJU 上游），节点仅启用被镜像的 BaseOS/AppStream；`scripts/snapshot_alma_repos.py` 可从新版 Alma VM 重新抓取）
- **kube-proxy** → kubeadm 默认 iptables 代理模式。注：曾尝试原生 nftables 代理模式（`kubeProxy.config.mode: nftables`，kubeadm v1beta4 中作独立文档），实测会破坏节点出站网络（kube-proxy 生成的 nft 表导致节点无法出站，flush 即恢复），故保留 iptables 模式；节点 `iptables-nft` 满足 kubelet 的 iptables 依赖，另装 `nftables` 包提供 `nft` CLI 以便排查规则

## 镜像数据持久化

镜像机 `k8s-repo` 的同步数据（`/var/www/repos`）放在 Incus 持久卷 `k8s-repo-repos`，由 `incus/incus_vms.py` 自动创建并挂载。`--destroy k8s-repo` 只删 VM、保留数据，重建后不去重复下载：

- k8s/containerd：`dnf reposync --newest-only` 天然增量；
- Alma Linux：repo-sync.sh 先用 `dnf download --url --resolve` 求依赖闭包的 URL 清单，只下载缺失、只删孤儿（陈旧内核等），每日定时与重建都不再全量重下闭包。

显式清空镜像数据用 `incus/incus_vms.py --destroy k8s-repo --purge-repos-data`。

## 目录结构

```
config.py                # 单一事实来源：拓扑/目录/VM 规格/镜像源常量
inventory.py             # pyinfra 分组（k8s_nodes / k8s_master / k8s_workers / k8s_repo）
Justfile                 # just 命令入口：vm-create / vm-destroy / offline / repo / all
deploy/                  # pyinfra 部署脚本
  repo.py                #   k8s-repo：nginx + reposync 镜像源（配置/每日 timer；首次同步手动触发）
  prepare.py             #   所有节点：内核/镜像源指向/containerd/k8s RPM/镜像预载
  init.py                #   master：kubeadm init + Cilium 离线安装
  join.py                #   workers：kubeadm join
incus/incus_vms.py       # 创建/销毁 VM（原生 Python + 线程池并行，不依赖 pyinfra）
templates/               # 远程配置文件 jinja2 模板
  kubernetes.repo.j2     #   节点端 k8s dnf 源（指向 k8s-repo）
  docker-ce.repo.j2      #   节点端 containerd dnf 源（指向 k8s-repo）
  mirror/                #   镜像 VM 端模板（nginx、repo-sync、timer、上游源）
scripts/
  download_offline.py    # 宿主机下载离线包（kubeadm config images list + Cilium）并 rsync 到节点
  import_images.sh       # 节点端镜像导入助手（随包上传到 /opt/k8s-offline/）
offline/                 # 生成的离线包（已 gitignore）
```

Justfile recipe 即一键编排链：`all: verify -> join -> init -> prepare -> offline -> ensure-repo`。

## 快速开始

```bash
# 1. 建 VM（可选，已有则跳过）
just vm-create   # 并行创建全部 VM（脚本内部线程池）；子集：just vm-create k8s-master,k8s-worker-1

# 2. 生成离线包并推送到所有节点 /opt/k8s-offline（可断点续传/幂等；
#    镜像清单来自本地 kubeadm 的 `kubeadm config images list` + 固定 Cilium 清单；
#    k8s/containerd RPM 由镜像源提供，不在 bundle 中）。
#    ensure-repo 会先探测镜像源是否已 serve k8s repodata，未 provision 才跑 deploy/repo.py。
just offline

# 3. 一键部署全流程：repo(按需) → offline → prepare → init(master) → join(workers) → verify
just all
```

手动执行单个阶段：

```bash
just repo        # 强制（重新）provision 镜像源配置（幂等；同步本身不在此步骤，见"镜像数据持久化"）
just offline     # 构建并上传离线包（依赖 ensure-repo，保证镜像源先就绪）
just prepare     # 所有节点准备（依赖 offline）
just init        # master 初始化（依赖 prepare）
just join        # 拉取 join 命令并加入 workers（依赖 init）
just verify      # 集群验证（依赖 join）
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
- **离线拉取镜像失败**：镜像 tag 与 kubeadm/Cilium chart 不一致。镜像清单来自宿主机本地 `kubeadm config images list`（若镜像源升级了 patch，需同步升级宿主机 kubeadm）与固定 Cilium 清单（见 `scripts/download_offline.py`），重新跑 `uv run python scripts/download_offline.py` 会重新生成 `offline/images/import-plan.txt`。
- **版本校验报错**（`宿主机 kubeadm 版本 (X) 与镜像源 (Y) 不一致`）：镜像源已同步到更新的 patch 而宿主机 kubeadm 未同步。先 `dnf repoquery --latest-limit 1`（或 `ssh admin@k8s-repo sudo dnf ... repoquery kubelet`）查镜像源当前版本，把宿主机 kubeadm 升级到同版本再跑；确需强行构建可用 `--skip-version-check`。
- **镜像源不可达 / 未 provision**：`download_offline.py` 报错退出（版本校验需要 ssh 到 k8s-repo）。`just offline` 的 ensure-repo 会在镜像源未 serve repodata 时自动先跑 `deploy/repo.py`；也可手动 `just repo`，或用 `--skip-version-check` 跳过。
- **幂等**：所有 recipe 可重复执行；`just all` 重跑全为 No-change/跳过，且不重启运行中的 kubelet。
