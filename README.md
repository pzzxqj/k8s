# k8s 集群与内网 RPM 镜像站

本文档覆盖两套环境，镜像站与节点网络互相独立。两套环境跑**同一套 pyinfra 部署流程**（`deploy/*.py` 编排 + `tasks/*.py` 原子任务），差异只体现在 `inventories/` 的 Host Data 与 `group_data/` 的镜像源数据：

1. **生产环境（192.168.90.x）**：当前在用的内网集群（k8s-master1/worker1/worker2）与镜像站 `192.168.90.201`，后者全量镜像 AlmaLinux 10 + docker-ce + kubernetes，走三套 systemd 定时同步。镜像站的部署脚本/配置独立在 `mirror/`（与 k8s 分离）。节点 dnf 源全部指向镜像站。
2. **测试环境（Incus 实验集群，10.98.68.x）**：在 Incus 的 3 台 AlmaLinux 10 VM 上用 kubeadm + containerd + Cilium 部署测试用 Kubernetes v1.36，无内网镜像——「内网镜像」就是上游（pkgs.k8s.io / download.docker.com / NJU）；容器镜像与 Cilium 走离线包。

## 版本

脚本版本单一来源 `pyproject.toml`（`project.version`），用 git tag（`v0.x.y`）发布。0.x 阶段的递增策略：

- **`0.minor`**：破坏性变更或新能力——`inventories/group_data` 键变化、recipe/命令面变化（如 rename、加 join-cp）、新拓扑模式（如 HA）。判定标准：**重新部署者需要知道这次改动**。
- **`patch`**：纯修复或行为不变的重构（如幂等修复）。

离线包构建时（`scripts/k8s_download_offline.py`）把生成时的版本与 commit 写入 `offline/deploy-version.txt`，随包 rsync 到每节点 `/opt/k8s-offline/`；`cat /opt/k8s-offline/deploy-version.txt` 即知该集群部署所用脚本版本。

---

## 生产环境（192.168.90.x）

### 拓扑

| 节点 | IP | ssh 别名 | 角色 |
|---|---|---|---|
| mirror | 192.168.90.201 | mirror | 内网 RPM 镜像站（nginx :80，root `/srv/repos`） |
| k8s-master1 | 192.168.90.220 | k8s-master1 | control-plane + Cilium |
| worker1 | 192.168.90.221 | worker1 | worker |
| worker2 | 192.168.90.222 | worker2 | worker |

### 镜像站

nginx 将 `/srv/repos` 以纯 HTTP 提供在 `http://192.168.90.201/`（firewalld 放行 http；SELinux 上下文 `httpd_sys_content_t`）。

同步全部为字节级，三套独立脚本 + systemd 定时（错峰避免 egress 设备并发打满）：

| 内容 | 上游 | 方式 | 落盘路径 | systemd timer |
|---|---|---|---|---|
| AlmaLinux 10（9 仓库 × x86_64 / x86_64_v2，全量 123 GB） | mirrors.nju.edu.cn | lftp 字节级（`--only-newer --delete`，单流 + 8 文件并行；rsync 873 被 egress 黑洞故走 HTTPS） | `/srv/repos/almalinux/10/` | `alma-repo-sync.timer` 每日 00:00 |
| docker-ce（el10 x86_64 stable + gpg 密钥） | mirrors.aliyun.com | lftp 字节级（`--exclude` 过滤 Aliyun 导航伪目录） | `/srv/repos/docker-ce/linux/centos/10/x86_64/stable/` | `docker-ce-repo-sync.timer` 每日 00:20 |
| kubernetes（v1.36，x86_64） | pkgs.k8s.io | `dnf reposync --download-metadata`（上游无目录列表，reposync 走 repomd/primary） | `/srv/repos/kubernetes/core:/stable:/v1.36/rpm/` | `kubernetes-repo-sync.timer` 每日 00:40 |

- 脚本位于仓库 `mirror/scripts/{alma,docker-ce,kubernetes}-repo-sync.sh`（provisioner 见 `mirror/repo.py`），部署到镜像机 `/usr/local/bin/`；均为 `set -euo pipefail`，任何失败使 oneshot unit failed（journald 可查）。
- 首同步手动触发过；此后 timer 每日增量：alma/docker-ce 用 `--only-newer`（+`--delete` 清理上游移除的文件），k8s 由 reposync 增量并按 `primary.xml` 比对自动清理陈旧 rpm。
- **k8s 升级/增版本**：编辑 `mirror/scripts/kubernetes-repo-sync.sh` 的 `K8S_VERSIONS=(v1.36 v1.37)` 数组后 `systemctl start kubernetes-repo-sync.service`；旧版本目录保留，客户端按需指到对应版本 URL。
- 逐字节校验已核：各仓库 repomd.xml 与随机 rpm 与上游 md5 一致（docker-ce 276 rpm、k8s 15 rpm、alma 18 个仓库 repomd 齐全）。
- 镜像机配置由 `mirror/repo.py` provision（幂等，收敛即零变更）：

```bash
uv run pyinfra -y 192.168.90.201 mirror/repo.py --user zhch
```

服务端自检：

```bash
curl -fsSL http://192.168.90.201/almalinux/10/BaseOS/x86_64/os/repodata/repomd.xml -o /dev/null -w '%{http_code}\n'
curl -fsSL http://192.168.90.201/docker-ce/linux/centos/10/x86_64/stable/repodata/repomd.xml -o /dev/null -w '%{http_code}\n'
curl -fsSL http://192.168.90.201/kubernetes/core:/stable:/v1.36/rpm/repodata/repomd.xml -o /dev/null -w '%{http_code}\n'
```

### 客户端配置（生产节点）

生产节点走与测试环境完全相同的 pyinfra 流程，只是 inventory 与 group data 不同：节点信息（IP/用户/`repos` 子集）在 `inventories/k8s_production.py` 的 Host Data，dnf 源 URL 在 `group_data/k8s_production.py`（派生自 `mirror/config.py`，即镜像站实际同步的内容）。

**仅换源**（不跑 prepare）：`deploy/repos.py` 把节点上三类 dnf 源收敛到 Host Data 指定的来源——Alma 基础源就地编辑（取消 `mirrorlist`、baseurl 重指 `http://192.168.90.201/almalinux`，保留原始 URL 尾路径）；`kubernetes.repo`/`docker-ce.repo` 为受管文件（模板在 `templates/`，gpgkey 取镜像站 `repodata/repomd.xml.key` 与 `docker-ce/linux/centos/gpg`）。非 k8s 服务器只想要 Alma 源时，可在 inventory 里写 `"repos": ["alma"]` 后同样跑 `deploy/repos.py`。

```bash
uv run pyinfra -y inventories/k8s_production.py deploy/repos.py
uv run pyinfra -y inventories/k8s_production.py deploy/repos.py --limit control_plane   # 仅 k8s-master1
ssh k8s-master1 'sudo dnf repolist'   # 应出现 kubernetes / docker-ce / almalinux 系列，均从镜像源解析
```

**完整部署**（与测试环境同一套任务）：先 `just offline --inventory inventories/k8s_production.py` 推送离线包，再逐阶段执行：

```bash
uv run pyinfra -y inventories/k8s_production.py deploy/k8s_prepare.py
uv run pyinfra -y inventories/k8s_production.py deploy/k8s_init.py --limit control_plane
uv run pyinfra -y inventories/k8s_production.py deploy/k8s_init_cp.py --limit control_plane   # HA（多 master）时
uv run pyinfra -y inventories/k8s_production.py deploy/k8s_join.py --limit workers
```

（ssh 用户来自 Host Data；认证走本地 ssh-agent / 默认密钥，与 `mirror/repo.py` 一致。）

---

## 测试环境（Incus 实验集群，10.98.68.x）

用 **kubeadm + containerd + Cilium** 在 Incus 的 AlmaLinux 10 VM 上部署测试用 Kubernetes v1.36 集群。集群形状（多少控制面/多少 worker）**完全由 inventory 决定**，脚本自动推导：控制面 >1 时自动启用 **HA 控制面**（Keepalived VIP + HAProxy 负载均衡，跑在 master 上），单控制面则退回经典单 master 布局。RPM 全部**直连上游**下载（无内网镜像），容器镜像与 Cilium 走**离线包**（宿主机下载 → 上传到节点）。

### 拓扑

| 节点 | IP | 内存 | 角色 |
|---|---|---|---|
| k8s-master | 10.98.68.10 | 6GiB | control-plane（bootstrap）+ Keepalived/HAProxy + Cilium |
| k8s-master-2 | 10.98.68.14 | 6GiB | control-plane + Keepalived/HAProxy |
| k8s-master-3 | 10.98.68.15 | 6GiB | control-plane + Keepalived/HAProxy |
| k8s-worker-1 | 10.98.68.11 | 3GiB | worker |
| k8s-worker-2 | 10.98.68.12 | 3GiB | worker |
| k8s-worker-3 | 10.98.68.16 | 3GiB | worker |

- **HA 控制面端点**：Keepalived VIP `10.98.68.20:6443`（`group_data/k8s_test.py` 的 `control_plane_endpoint`），HAProxy 在每个 master 上把 VIP 转发到全部 apiserver；kubeadm `controlPlaneEndpoint` 与 Cilium `k8sServiceHost` 都用 VIP。apiserver 以 `bind-address` 绑本机 IP，与 HAProxy 互不冲突。
- **自动决策**：`deploy/_topology.py` 自定位 inventory（`host.groups` 的 `k8s_test` 自动组），`control_plane` 列表第一条 = bootstrap master、长度 >1 = HA、keepalived 优先级/HAProxy 后端由列表推导。清单加行即扩容，无需任何标志位。
- 单点修改处：`config.py`（VM 规格，仅建机用）与 `inventories/k8s_test.py`（部署拓扑，唯一决策来源，IP 需与 config 一致）；Cilium chart/CLI 版本固定在 `scripts/k8s_download_offline.py`；k8s 版本跟随**宿主机本地 kubeadm**（`kubeadm config images list`，须与 pkgs.k8s.io 上游 RPM 同版本）。

### 组件来源

- **k8s RPM**（kubelet/kubeadm/kubectl/cri-tools/kubernetes-cni）→ 直连上游 `https://pkgs.k8s.io/core:/stable:/v1.36/rpm/`
- **containerd.io 2.3.3** → 直连上游 `https://download.docker.com/linux/centos/10/x86_64/stable/`
- **容器镜像 + Cilium CLI/chart** → 离线包 `./offline`（images/ 预载到 containerd；`k8s_download_offline.py` 用 rsync 直接推到各节点 `/opt/k8s-offline`，不经 pyinfra）
- **系统基础包** → 直连上游 NJU `almalinux/10/<RepoDir>/x86_64/os/` 规范布局（见 `config.ALMA_UPSTREAM_BASE`）：**全部 9 个仓库**（BaseOS/AppStream/CRB/extras/HighAvailability/NFV/RT/SAP/SAPHANA）。节点的 `almalinux-*.repo` **就地编辑**（`tasks/alma_repos.py`：注释 `mirrorlist`、baseurl 重指 NJU 且保留 URL 尾路径，注释过的 baseurl 一并启用），不再用 VM 模板整套下发；cloud-init（`incus/incus_vms.py`）建机时同样用 sed 先指向 NJU，保证首装 `dnf` 不出内网。`tasks/kubernetes_repo.py`/`tasks/docker_ce_repo.py` 是受管文件（`templates/`），baseurl 指向 pkgs.k8s.io / download.docker.com（gpgkey 分别取 `repodata/repomd.xml.key` 与 `linux/centos/gpg`）
- **kube-proxy** → 不使用，由 **Cilium eBPF 完全替代**（kube-proxy free）。`kubeadm init` 加 `--skip-phases=addon/kube-proxy`，Cilium 以 `kubeProxyReplacement=true` + `k8sServiceHost/-Port` 安装（见 `deploy/k8s_init.py`），ClusterIP/NodePort/HostPort/masquerade 全走 eBPF，节点上不落任何 service 级 netfilter 规则。历史教训：曾先后尝试 kube-proxy 原生 nftables 模式（kubeadm v1beta4 `kubeProxy.config.mode: nftables`）与改 kube-proxy ConfigMap `mode: nftables`，实测均因 kube-proxy 的 nft 表与 Cilium netfilter 规则冲突导致 3 节点出站黑洞（`sudo nft delete table ip kube-proxy` 即恢复），故彻底去掉 kube-proxy 而非在其上纠缠模式。节点仍装 `iptables-nft` + `nftables`（满足 kubelet 内核依赖、提供 `nft` CLI 排查）
- **HA 控制面 LB** → 仅在 control_plane 组多于 1 台时安装：`haproxy` + `keepalived`（`tasks/k8s_lb.py`，Alma 上游源）。Keepalived VRRP（unicast，优先级按清单顺序 200/150/100）持有 VIP，`notify_master/backup` 脚本只在 VIP 持有者上启停 HAProxy，避免与同机 apiserver 抢 `:6443`。apiserver 侧由 kubeadm 配置 `bind-address` 绑本机 IP（`templates/kubeadm.yaml.j2`）

### 目录结构

```
config.py                # 单一事实来源：测试环境拓扑/目录/VM 规格（上游源常量取自 mirror/config.py）
inventories/             # pyinfra inventory：Host Name + Host Data（ssh_hostname/ssh_user/repos 子集）
  k8s_test.py            #   测试集群（control_plane / workers / nodes）
  k8s_production.py      #   生产集群（k8s-master1/k8s-worker1/k8s-worker2；可加仅需 alma 源的主机）
group_data/              # pyinfra 组数据：镜像源唯一差异点
  all.py                 #   默认值（repos 子集 / apiserver_port / service_subnet / node_offline_dir）
  k8s_test.py            #   NJU / pkgs.k8s.io / download.docker.com（测试环境「内网镜像=上游」）
  k8s_production.py      #   派生自 mirror/config.py 的镜像站 URL（192.168.90.201）
Justfile                 # just 命令入口：vm-create / vm-destroy / offline / repos / prepare / init / join / verify / all
mirror/                  # 内网 RPM 镜像站（生产环境，独立于 k8s）
  config.py              #   镜像机/上游源常量（MIRROR_ROOT、pkgs.k8s.io、NJU 等）
  repo.py                #   镜像机 provisioner（nginx + lftp/reposync + timers；直接主机运行）
  scripts/               #   三套同步脚本（alma/docker-ce/kubernetes-repo-sync.sh）
  templates/             #   镜像机 unit×6 + repos.conf.j2
deploy/                  # pyinfra 编排脚本（环境无关，靠 inventory/group data 区分）
  _common.py             #   共享 helper（is_control_plane / ssh_user / safe_file_exists / 远程路径常量）
  _topology.py           #   从 inventory 自动推导拓扑（bootstrap/HA/endpoint/keepalived 优先级/haproxy 后端）
  repos.py               #   仅换 dnf 源（alma 就地编辑 + kubernetes/docker-ce 受管文件）
  k8s_prepare.py         #   所有节点：repo→内核/swap/selinux→containerd/k8s RPM→镜像预载→(HA) LB
  k8s_init.py            #   bootstrap control_plane：kubeadm init + Cilium 离线安装 + join 命令
  k8s_init_cp.py         #   (HA) 附加 control_plane：kubeadm join --control-plane
  k8s_join.py            #   workers：kubeadm join
tasks/                   # 原子任务（被 deploy/*.py local.include）
  alma_repos.py          #   almalinux-*.repo 就地编辑（注释 mirrorlist，baseurl 重指 alma_base）
  kubernetes_repo.py     #   受管 kubernetes.repo（按 repos 子集 push/remove）
  docker_ce_repo.py      #   受管 docker-ce.repo（按 repos 子集 push/remove）
  kernel_modules_extra.py / kernel_modules.py / sysctl.py / swap.py / selinux.py
  k8s_containerd.py / k8s_rpms.py / k8s_images.py / kubelet_service.py
  kubeadm_init.py / kubeconfig.py / cilium.py / k8s_join_command.py
  k8s_worker_join.py / k8s_control_plane_join.py / k8s_lb.py
incus/
  incus_vms.py           # 创建/销毁 VM（原生 Python + 线程池并行，不依赖 pyinfra）
  _incus.py              #   共享 incus CLI 封装（run / instance_exists / instance_running）
templates/               # 远程配置文件 jinja2 模板
  kubernetes.repo.j2     #   节点端 k8s dnf 源（pkgs.k8s.io 上游 / 镜像站）
  docker-ce.repo.j2      #   节点端 containerd dnf 源（download.docker.com 上游 / 镜像站）
  kubeadm.yaml.j2        #   kubeadm init 配置（advertiseAddress=本机，controlPlaneEndpoint=VIP/单点，HA 时 bind-address）
  haproxy.cfg.j2         #   HAProxy：VIP:apiserver_port → 全部 master apiserver（HA 时渲染）
  keepalived.conf.j2     #   Keepalived VRRP：VIP/unicast/优先级/notify（HA 时渲染）
  containerd-config.toml.j2
scripts/
  k8s_download_offline.py  # 宿主机下载离线包（kubeadm config images list + Cilium）并 rsync 到节点
                           #   --inventory / --group 决定上传目标（默认 k8s_test）
  k8s_import_images.sh   # 节点端镜像导入助手（随包上传到 /opt/k8s-offline/）
  k8s_lb_master.sh       # keepalived notify：start/stop haproxy（VIP 持有者切换时）
  k8s_verify_cluster.py  # 集群验证（官方 kubernetes client，见「验证」）
offline/                 # 生成的离线包（已 gitignore）
```

Justfile recipe 即一键编排链：`all: verify -> join -> join-cp -> init -> prepare -> offline`（单 master 时 `join-cp` 自动跳过）。

### 快速开始

```bash
# 1. 建 VM（可选，已有则跳过）
just vm-create   # 并行创建全部 VM（脚本内部线程池）；子集：just vm-create k8s-master,k8s-worker-1

# 2. 生成离线包并推送到所有节点 /opt/k8s-offline（可断点续传/幂等；
#    镜像清单来自本地 kubeadm 的 `kubeadm config images list` + 固定 Cilium 清单；
#    k8s/containerd RPM 由上游源提供，不在 bundle 中）。
just offline

# 3. 一键部署全流程：offline → prepare → init(bootstrap master) → join-cp(HA 附加
#    master，单 master 自动跳过) → join(workers) → verify
just all
```

手动执行单个阶段：

```bash
just offline     # 构建并上传离线包（版本校验直查 pkgs.k8s.io，无 dnf 时降级跳过）
just repos       # 仅换 dnf 源（默认 k8s_test；REPO_INVENTORY 参数，如 just repos inventories/k8s_production.py）
just prepare     # 所有节点准备（依赖 offline；HA 时顺带装 keepalived/haproxy 并拉起 VIP）
just init        # bootstrap master 初始化 + Cilium（依赖 prepare；附加 master 自跳过）
just join-cp     # (HA) 附加 master 加入控制面（依赖 init；单 master 自跳过）
just join        # 拉取 join 命令并加入 workers（依赖 join-cp）
just verify      # 集群验证（依赖 join）
```

**扩容即加行**：新增 worker → 在 `config.py` 的 `VMS` 与 `inventories/k8s_test.py` 的 `workers` 组各加一行（IP 一致），重跑 `just vm-create <名字>` + `just prepare` + `just join`。新增 master → 同样加进 `VMS` 与 `control_plane` 组，`just vm-create` + `just prepare` + `just join-cp`。bootstrap/HA/endpoint/优先级全部由 `deploy/_topology.py` 从清单自动推导，无需改脚本。

生产环境复用同一套 recipe，仅 inventory 不同：`just offline --inventory inventories/k8s_production.py`、`uv run pyinfra -y inventories/k8s_production.py deploy/{repos,k8s_prepare,k8s_init,k8s_init_cp,k8s_join}.py`（见「生产环境」一节）。生产若启用 HA，同样先在 `group_data/k8s_production.py` 的 `control_plane_endpoint` 填一个空闲 VIP，再把附加 master 加进 `control_plane` 组。

### 验证

`just verify` 自动完成核心检查（`scripts/k8s_verify_cluster.py` 用官方 kubernetes client 读 master 的 admin.conf）：3 节点 Ready（含 kubelet 版本）、cilium DaemonSet 全调度就绪、无 kube-proxy DaemonSet（kube-proxy free）、kube-system 全部 Pod Running/Succeeded、coredns Deployment 全可用；任一失败即非零退出。

```bash
just verify      # 自动核心检查；也可手动 ssh 复查：
ssh admin@10.98.68.10
kubectl get nodes -o wide          # 3 个节点 Ready
cilium status                      # Cilium / Operator / Envoy OK，且 KubeProxyReplacement: True
kubectl -n kube-system get ds      # 无 kube-proxy DaemonSet（kube-proxy free）
curl -sk https://10.96.0.1:443/version   # ClusterIP Service 路径（由 Cilium eBPF 处理）

# 跨节点 Pod→Pod 数据面实测（离线环境无 dig/nslookup，复用 coredns 镜像做链式转发：
# test pod 自起 :5353 并 forward 到另一节点 coredns Pod IP，日志出现 NOERROR 即链路通。
# 注意：coredns 只认多行 Corefile（单行分号式 `{ forward . IP; log }` 会报
# "Unexpected '}'"——caddy 语法不支持），转发目标须取实际 coredns Pod IP；
# 若取到的 coredns 恰在 nodeName 节点，换 .items[1] 或改 nodeName 保证跨节点。）
CK_IP=$(kubectl -n kube-system get pods -l k8s-app=kube-dns \
  -o jsonpath='{.items[0].status.podIP}')
cat > /tmp/testdns-cf.yaml <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: testdns-cfg
  namespace: kube-system
data:
  Corefile: |
    .:5353 {
        forward . $CK_IP
        log
        errors
    }
EOF
kubectl create -f /tmp/testdns-cf.yaml
kubectl -n kube-system run testdns --image=registry.k8s.io/coredns/coredns:v1.14.2 \
  --restart=Never --overrides='{"spec":{"nodeName":"k8s-worker-2",\
  "containers":[{"name":"dns","image":"registry.k8s.io/coredns/coredns:v1.14.2",\
  "command":["/coredns","-conf","/cfg/Corefile"],\
  "volumeMounts":[{"name":"cfg","mountPath":"/cfg"}]}],\
  "volumes":[{"name":"cfg","configMap":{"name":"testdns-cfg"}}]}}'
IP=$(kubectl -n kube-system get pod testdns -o jsonpath='{.status.podIP}')
# host 上以 bash /dev/udp 手搓 DNS 查询包发给 testdns:5353，再查其日志：
printf '\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x07example\x03com\x00\x00\x01\x00\x01' \
  > /dev/udp/$IP/5353
kubectl -n kube-system logs pod/testdns | grep example.com   # 期待 NOERROR 行
kubectl -n kube-system delete pod/testdns cm/testdns-cfg
```

### 常见问题

- **`cilium-health status` 输出 `Cluster health: 0/3 reachable` / `cilium-health-ep` 控制器超时**（kube-proxy free 场景下的已知误报）：健康模块的自查（endpoint 探测 + `cilium-health-ep` 控制器对本地健康端点 `:<healthIP>:4240/hello` 的 GET）会持续失败，但**并非数据面问题**——本环境直接实测全部通过：host→各节点健康端点 ICMP/HTTP 全通（`curl :4240/hello` 200、`ping` 0 丢包）、首尾经过真实 Pod 的跨节点 Pod→Pod 链式转发返回 NOERROR、Hubble 观察期间 0 条 drop/deny、3 节点 Ready、coredns Ready、各服务可达。与上流 #31567/#33697/#30504 等健康上报回归问题族表现一致（健康模块自身的探测/宣称逻辑易误报，1.16 起多次变更、cluster 汇总计数单独曾有单独 bug）。结论：以实际业务连通性为准，健康上报不一致可忽略；若需确认数据面，用上文「验证」一节的离线 DNS 链式转发法直测 Pod→Pod。
- **离线导入镜像后 pod `ErrImageNeverPull`（特定镜像内容在某节点孵化）**：复用 VM 若带旧集群残留的同一镜像（按 content digest 判定，如 `ctr images ls -d` 或 `ctr content ls`），其元数据/内容可能被污染，导致 containerd CRI 对该 content digest 的 ImageStatus 查不到（`ctr run` 却能跑、`crictl inspecti` 也能看到，busybox 等其它镜像正常，kubelet 重启/containerd 重启均无效）。换用不同 content digest 的镜像（如 `nginx:latest` 换 `nginx:1.27`）即恢复；彻底清除需在节点 `ctr -n k8s.io images rm` 掉旧 ref 及 `docker.io/library/xxx@sha256:<旧 digest>` 记录后重导。
- **coredns 探针超时 / NotReady**：kubelet 被重启（如 RPM 重装）后偶发 veth/endpoint 状态残留。删掉 coredns Pod 让其重建即可：`kubectl -n kube-system delete pods -l k8s-app=kube-dns`。`tasks/k8s_rpms.py` 用 `dnf.packages` 只装缺失、绝不升级，重跑 prepare 不会重装 kubelet。
- **kube-proxy nftables 模式导致节点出站中断**：只要让 kube-proxy 以 nftables 模式运行（无论 kubeadm 配置还是改 ConfigMap），其 nft 表会与 Cilium netfilter 规则冲突，3 节点全部无法出站（已建立连接仍可通），且规则集无显式 drop「计数」。修复与回退：`sudo nft delete table ip kube-proxy; sudo nft delete table ip6 kube-proxy` 即恢复出站（kube-proxy 同步周期 ~30s 内会重建表，注意窗口），再改回 iptables/移除 kube-proxy。本项目最终的解决方案是**完全移除 kube-proxy**（见上方「组件来源」）。
- **离线拉取镜像失败**：镜像 tag 与 kubeadm/Cilium chart 不一致。镜像清单来自宿主机本地 `kubeadm config images list`（若 pkgs.k8s.io 上游升级了 patch，需同步升级宿主机 kubeadm）与固定 Cilium 清单（见 `scripts/k8s_download_offline.py`），重新跑 `uv run python scripts/k8s_download_offline.py` 会重新生成 `offline/images/import-plan.txt`。
- **版本校验报错**（`宿主机 kubeadm 版本 (X) 与 pkgs.k8s.io 当前 kubelet (Y) 不一致`）：上游已更新到新的 patch 而宿主机 kubeadm 未同步。先 `dnf --disablerepo='*' --repofrompath=pkgs,https://pkgs.k8s.io/core:/stable:/v1.36/rpm -q repoquery --latest-limit 1 --qf '%{VERSION}' kubelet` 查上游当前版本，把宿主机 kubeadm 升级到同版本再跑；确需强行构建可用 `--skip-version-check`。
- **版本校验跳过**：`k8s_download_offline.py` 依赖宿主机 `dnf` 解析 pkgs.k8s.io；宿主机无 dnf（或无法访问 pkgs.k8s.io）时自动降级为仅提示，不报错退出——加 `--skip-version-check` 可显式忽略。
- **把已部署的单 master 集群扩成 HA 3 master**：kubeadm 官方支持对已有集群追加 control-plane。步骤：1) 清单 `control_plane` 组追加 master-2/3、`config.py` `VMS` 建对应 VM；2) `just vm-create` + `just prepare`（会在各 master 装 keepalived/haproxy 并拉起 VIP，也覆盖现 master）；3) `just init` 因 admin.conf 已存在会跳过 init、仅收敛其它任务；4) `just join-cp` 拉取 `kubeadm init phase upload-certs --upload-certs` 生成的证书 key 后把 master-2/3 以 `--control-plane` 加入；5) `just join` 加 worker。注意：现有 kubeconfig/客户端仍指向旧 master IP，需自行切到 VIP（`kubectl config set-cluster ...` 或重新下发 admin.conf）。
- **幂等**：所有 recipe 可重复执行；`just all` 重跑全为 No-change/跳过，且不重启运行中的 kubelet。`tasks/alma_repos.py` 跳过与 `alma_base` 相同的已知上游（测试环境即 NJU），避免 baseurl 自匹配反复改写、连带每次触发 `dnf clean all`。
