---
name: pyinfra
description: pyinfra 部署脚本参考技能。本项目用 pyinfra 管理 Incus 上的 AlmaLinux 10 内网集群，当任务涉及编写/修改 deploy/*.py、inventory.py、自定义 operation/fact，或运行/调试 `uv run pyinfra` 命令时使用。附带本地捆绑的 pyinfra 3.x 官方完整文档 pyinfra-llms-full.txt，可精确 grep 各 operation/fact 签名与参数，勿依赖记忆猜 API。
---

# pyinfra 技能

## 何时使用

- 编写、修改或新增 pyinfra 部署脚本（`deploy/*.py`）
- 编辑 `inventory.py` 的 host/组定义与 data
- 运行、调试或解释 `uv run pyinfra ...` 命令
- 需要某个 operation / fact / connector / global argument 的**精确签名或参数名**

## 官方文档（本地参考，锁定 3.x）

文件：`pyinfra-llms-full.txt`（与本文件同目录，~296 KB，3.x 完整全文，与 `pyinfra>=3.10.0` 匹配）。

**不要整篇通读**，按锚点定位后局部 Read / Grep：

- 页面边界：行 `Source: .../<page>.html`，`page` 形如 `operations/files`、`operations/systemd`、`facts/server`、`connectors/ssh`、`arguments`、`cli`、`inventory-data`、`using-operations`、`deploy-process`（文件内的 URL 字符串硬编码为 `en/latest`，内容实为 3.x，与本项目版本匹配）。可用 `rg -n "Source: .*operations/files" pyinfra-llms-full.txt` 定位。
- operation 的签名/参数/示例在对应 `operations/<name>` 段；fact 的返回类型/缺省值在 `facts/<name>` 段。
- 检索精确关键词：`rg -n "files\.put|apt\.packages|_if|_retries|_serial" pyinfra-llms-full.txt`。
- 全局参数（`_sudo`、`_env`、`_if`、`_retries` 等）集中在 `arguments` 段。

## 本项目约定（k8s 内网集群）

- 入口统一为 `uv run pyinfra -y inventory.py deploy/<X>.py --limit <group> --user admin`（可加 `--key ~/.ssh/id_ed25519`），见各 `deploy/*.py` 头部注释。所有脚本幂等，重跑应整体 No-change。
- 分组：`k8s_master` / `k8s_workers` / `k8s_nodes`，取自 `inventory.py`。镜像站不在此列——真实镜像站由 `mirror/repo.py` 直接主机连接运行（`uv run pyinfra -y 192.168.90.201 mirror/repo.py --user zhch`；实名认证走 ssh-agent/ssh_config，不传 `--key`）。
- import 风格：
  - 上下文：`from pyinfra.context import host`
  - 事实：`from pyinfra.facts.<mod> import <Fact>`（如 `FileContents`、`FindFiles`、`FindInFile`、`Command`、`Selinux`）
  - 操作：`from pyinfra.operations import files, server, systemd, selinux`
- 复杂/重复逻辑放 `deploy/_common.py`；模板走 `files.template(src="templates/... .j2", dest=..., **变量)`（变量在 `.j2` 里用 `{{ var }}`）。
- 幂等优先：能用事实 diff 就不隔空跑无条件命令；需要判断"装没装/在不在"时先用事实，必要时参考现有 `rpm -q` 守卫思路避免重装。

## 关键概念速查（细节见文档）

- **两阶段执行**：prepare 阶段读事实、确定操作顺序与是否变更，execute 才发命令。deploy 代码在 prepare 期间以**变更前**状态执行；依赖前序操作结果的条件须用 `_if=<meta>.did_change` / `did_not_change` / `did_succeed` / `did_error`（可传 callable 列表，或 `any_changed` / `all_changed`）。
- **声明式 vs 命令式**：大多数 operation 是声明式（diff 状态，无漂移则 no-op）；`server.shell`、`python.call` 等是命令式（无条件执行）。
- **Global arguments**：除 `name` 外均以下划线开头：`_sudo`、`_sudo_user`、`_use_sudo_login`、`_preserve_sudo_env`、`_env`、`_shell_executable`、`_serial`、`_parallel`、`_if`、`_retries`、`_retry_delay`、`_retry_until`、`_ignore_errors` 等。默认 shell 是 `sh`，bash 专属语法需 `_shell_executable="bash -l"`。
- **事实原则**：deploy 代码里只依赖**不可变**事实（OS 发行版/架构/内核版本）；"是否安装/文件是否存在/服务是否运行"是可变的，分支必须挂到 `_if` 上，否则读到的是 prepare 前的旧状态。`host.get_fact(Fact)` 失败会致使 host 失败，需容忍时传 `_ignore_errors=True`。
- **数据与上下文**：`host.name` / `host.groups` / `host.data`、`inventory.get_host("name")`、`config.X`（如 `config.SUDO`、`config.REQUIRE_PYINFRA_VERSION`、`config.INHERIT_ENV`）。
- **CLI 调试**：`--dry`（只报告不执行）、`-vv`（回显每条 shell）、`--debug-operations` / `--debug-facts`、`exec -- <cmd>`、`fact <Fact> path=...`、`debug-inventory`、`--limit`/`--exclude`。

## 版本注意

- 技能文档固定为 **pyinfra 3.x**（与本项目 `pyinfra>=3.10.0` 一致）。不要臆造 pyinfra 4.x 才有的语法；遇到没把握的 API 先 `rg` 参考文件确认其是否存在于 3.x。