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
- **kube-proxy** → 不使用，由 **Cilium eBPF 完全替代**（kube-proxy free）。`kubeadm init` 加 `--skip-phases=addon/kube-proxy`，Cilium 以 `kubeProxyReplacement=true` + `k8sServiceHost/-Port` 安装（见 `deploy/init.py`），ClusterIP/NodePort/HostPort/masquerade 全走 eBPF，节点上不落任何 service 级 netfilter 规则。历史教训：曾先后尝试 kube-proxy 原生 nftables 模式（kubeadm v1beta4 `kubeProxy.config.mode: nftables`）与改 kube-proxy ConfigMap `mode: nftables`，实测均因 kube-proxy 的 nft 表与 Cilium netfilter 规则冲突导致 3 节点出站黑洞（`sudo nft delete table ip kube-proxy` 即恢复），故彻底去掉 kube-proxy 而非在其上纠缠模式。节点仍装 `iptables-nft` + `nftables`（满足 kubelet 内核依赖、提供 `nft` CLI 排查）

## 镜像数据持久化

镜像机 `k8s-repo` 的同步数据（`/var/www/repos`）放在 Incus 持久卷 `k8s-repo-repos`，由 `incus/incus_vms.py` 自动创建并挂载。`--destroy k8s-repo` 只删 VM、保留数据，重建后不去重复下载：

- k8s/containerd：`dnf reposync --newest-only` 天然增量；
- Alma Linux：repo-sync.sh 先用 `dnf download --url --resolve` 求依赖闭包的 URL 清单，只下载缺失、只删孤儿（陈旧内核等），每日定时与重建都不再全量重下闭包。

显式清空镜像数据用 `incus/incus_vms.py --destroy k8s-repo --purge-repos-data`。

## 目录结构

```
config.py                # 单一事实来源：拓扑/目录/VM 规格/镜像源常量
inventory.py             # pyinfra 分组（k8s_nodes / k8s_master / k8s_workers / k8s_repo）
Justfile                 # just 命令入口：vm-create / vm-destroy / repo / offline / prepare / init / join / verify / all
deploy/                  # pyinfra 部署脚本
  _common.py             #   共享 helper（is_master / safe_file_exists / 远程路径常量）
  _alma_repos.py         #   Alma 镜像源模板渲染/一致性检查（见 templates/alma-repo/）
  repo.py                #   k8s-repo：nginx + reposync 镜像源（配置/每日 timer；首次同步手动触发）
  prepare.py             #   所有节点：内核/镜像源指向/containerd/k8s RPM/镜像预载
  init.py                #   master：kubeadm init + Cilium 离线安装
  join.py                #   workers：kubeadm join
incus/
  incus_vms.py           # 创建/销毁 VM（原生 Python + 线程池并行，不依赖 pyinfra）
  _incus.py              #   共享 incus CLI 封装（run / instance_exists / instance_running）
templates/               # 远程配置文件 jinja2 模板
  kubernetes.repo.j2     #   节点端 k8s dnf 源（指向 k8s-repo）
  docker-ce.repo.j2      #   节点端 containerd dnf 源（指向 k8s-repo）
  alma-repo/             #   受管 Alma almalinux-*.repo.j2 模板（由 _alma_repos.py 渲染）
  mirror/                #   镜像 VM 端模板（nginx、repo-sync、timer、上游源）
scripts/
  download_offline.py    # 宿主机下载离线包（kubeadm config images list + Cilium）并 rsync 到节点
  import_images.sh       # 节点端镜像导入助手（随包上传到 /opt/k8s-offline/）
  verify_cluster.py      # 集群验证（官方 kubernetes client，见「验证」）
  snapshot_alma_repos.py # 从新版 Alma VM 重新抓取 almalinux-*.repo 生成受管模板
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

`just verify` 自动完成核心检查（`scripts/verify_cluster.py` 用官方 kubernetes client 读 master 的 admin.conf）：3 节点 Ready（含 kubelet 版本）、cilium DaemonSet 全调度就绪、无 kube-proxy DaemonSet（kube-proxy free）、kube-system 全部 Pod Running/Succeeded、coredns Deployment 全可用；任一失败即非零退出。

```bash
just verify      # 自动核心检查；也可手动 ssh 复查：
ssh admin@10.98.68.13 curl -s http://localhost/k8s/core:/stable:/v1.36/rpm/  # 镜像源 repodata
ssh admin@10.98.68.10
kubectl get nodes -o wide          # 3 个节点 Ready
cilium status                      # Cilium / Operator / Envoy OK，且 KubeProxyReplacement: True
kubectl -n kube-system get ds      # 无 kube-proxy DaemonSet（kube-proxy free）
curl -sk https://10.96.0.1:443/version   # ClusterIP Service 路径（由 Cilium eBPF 处理）

# 跨节点 Pod→Pod 数据面实测（离线环境无 dig/nslookup，复用 coredns 镜像做链式转发：
# test pod(host 上 coredns 镜像自起 :5353) forward 到另一节点 coredns Pod IP，
# test pod 日志出现 NOERROR + ra 即链路通。）
kubectl -n kube-system create cm testdns-cfg \
  --from-literal=Corefile='.:5353 { forward . 10.0.2.6; log; errors }'
kubectl -n kube-system run testdns --image=registry.k8s.io/coredns/coredns:v1.14.2 \
  --restart=Never --overrides='{"spec":{"nodeName":"k8s-master",\
  "containers":[{"name":"dns","image":"registry.k8s.io/coredns/coredns:v1.14.2",\
  "command":["/coredns","-conf","/cfg/Corefile"],\
  "volumeMounts":[{"name":"cfg","mountPath":"/cfg"}]}],\
  "volumes":[{"name":"cfg","configMap":{"name":"testdns-cfg"}}]}}'
IP=$(kubectl -n kube-system get pod testdns -o jsonpath='{.status.podIP}')
# host 上以 bash /dev/udp 手搓 DNS 查询包发给 testdns:5353，再查其日志：
kubectl -n kube-system logs pod/testdns | grep example.com   # 期待 NOERROR 行
kubectl -n kube-system delete pod/testdns cm/testdns-cfg
```

## 常见问题

- **`cilium-health status` 输出 `Cluster health: 0/3 reachable` / `cilium-health-ep` 控制器超时**（kube-proxy free 场景下的已知误报）：健康模块的自查（endpoint 探测 + `cilium-health-ep` 控制器对本地健康端点 `:<healthIP>:4240/hello` 的 GET）会持续失败，但**并非数据面问题**——本环境直接实测全部通过：host→各节点健康端点 ICMP/HTTP 全通（`curl :4240/hello` 200、`ping` 0 丢包）、首尾经过真实 Pod 的跨节点 Pod→Pod 链式转发返回 NOERROR、Hubble 观察期间 0 条 drop/deny、3 节点 Ready、coredns Ready、各服务可达。与上流 #31567/#33697/#30504 等健康上报回归问题族表现一致（健康模块自身的探测/宣称逻辑易误报，1.16 起多次变更、cluster 汇总计数单独曾有单独 bug）。结论：以实际业务连通性为准，健康上报不一致可忽略；若需确认数据面，用上文「验证」一节的离线 DNS 链式转发法直测 Pod→Pod。
- **离线导入镜像后 pod `ErrImageNeverPull`（特定镜像内容在某节点孵化）**：复用 VM 若带旧集群残留的同一镜像（按 content digest 判定，如 `ctr images ls -d` 或 `ctr content ls`），其元数据/内容可能被污染，导致 containerd CRI 对该 content digest 的 ImageStatus 查不到（`ctr run` 却能跑、`crictl inspecti` 也能看到，busybox 等其它镜像正常，kubelet 重启/containerd 重启均无效）。换用不同 content digest 的镜像（如 `nginx:latest` 换 `nginx:1.27`）即恢复；彻底清除需在节点 `ctr -n k8s.io images rm` 掉旧 ref 及 `docker.io/library/xxx@sha256:<旧 digest>` 记录后重导。
- **coredns 探针超时 / NotReady**：kubelet 被重启（如 RPM 重装）后偶发 veth/endpoint 状态残留。删掉 coredns Pod 让其重建即可：`kubectl -n kube-system delete pods -l k8s-app=kube-dns`。deploy/prepare.py 已用 `rpm -q` 守卫避免重跑时重装 kubelet。
- **kube-proxy nftables 模式导致节点出站中断**：只要让 kube-proxy 以 nftables 模式运行（无论 kubeadm 配置还是改 ConfigMap），其 nft 表会与 Cilium netfilter 规则冲突，3 节点全部无法出站（已建立连接仍可通），且规则集无显式 drop「计数」。修复与回退：`sudo nft delete table ip kube-proxy; sudo nft delete table ip6 kube-proxy` 即恢复出站（kube-proxy 同步周期 ~30s 内会重建表，注意窗口），再改回 iptables/移除 kube-proxy。本项目最终的解决方案是**完全移除 kube-proxy**（见第 23 行）。
- **离线拉取镜像失败**：镜像 tag 与 kubeadm/Cilium chart 不一致。镜像清单来自宿主机本地 `kubeadm config images list`（若镜像源升级了 patch，需同步升级宿主机 kubeadm）与固定 Cilium 清单（见 `scripts/download_offline.py`），重新跑 `uv run python scripts/download_offline.py` 会重新生成 `offline/images/import-plan.txt`。
- **版本校验报错**（`宿主机 kubeadm 版本 (X) 与镜像源 (Y) 不一致`）：镜像源已同步到更新的 patch 而宿主机 kubeadm 未同步。先 `dnf repoquery --latest-limit 1`（或 `ssh admin@k8s-repo sudo dnf ... repoquery kubelet`）查镜像源当前版本，把宿主机 kubeadm 升级到同版本再跑；确需强行构建可用 `--skip-version-check`。
- **镜像源不可达 / 未 provision**：`download_offline.py` 报错退出（版本校验需要 ssh 到 k8s-repo）。`just offline` 的 ensure-repo 会在镜像源未 serve repodata 时自动先跑 `deploy/repo.py`；也可手动 `just repo`，或用 `--skip-version-check` 跳过。
- **幂等**：所有 recipe 可重复执行；`just all` 重跑全为 No-change/跳过，且不重启运行中的 kubelet。
