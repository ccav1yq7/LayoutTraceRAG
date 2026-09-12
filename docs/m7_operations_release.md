# M7 加固与发布审阅

2026-09-09：已实现离线备份/恢复 CLI、数据目录锁、单 API 实例租约、容器和
Compose 演示配置、依赖清单及发布证据检查。实际完成进程强杀恢复、数据恢复、
本地候选容器浏览器验证。**发布结论：不发布到公网；M7 全部退出条件未满足。**
本轮没有新增付费模型请求，未推送镜像、提交代码或部署公网服务。

2026-09-10 更新：基础镜像内容核对、完整 OS 扫描和第一轮修复已完成，
最新配置与剩余漏洞见 [供应链复验](supply_chain_validation.md)。下面的
扫描/镜像数字保留为 9 月 9 日历史记录，运行命令已对齐当前候选名称。

## 验证与修复

- 全量 Python 回归 **184 passed**（M7 新增 11 项），Ruff/Mypy 通过，74 个模块。
- Vitest **2 passed**；候选容器上的 Playwright **9 passed**，覆盖桌面、窄屏、
  原图来源、追问、补图、取消、刷新和模拟业务确认。
- 修复手机端切换型号后立即 Enter 发送的会话 revision 竞态：发送等待切换
  成功后读取新版本；切换失败不向旧型号发问。新增两个延迟响应回归用例，
  原失败报告保留在 `browser-race-before.json`。
- 发现 Vitest 开发依赖 GHSA-82fw-gwwq-j7x9，升级到修复版 **4.1.11**；依据
  [官方安全公告](https://github.com/vitest-dev/vitest/security/advisories/GHSA-82fw-gwwq-j7x9)。
  保留升级前审计，升级后 npm audit 为 0；Python 开发环境与候选镜像的 Python
  runtime 包审计也未发现已知漏洞。**不包含 OS 包或 wheel 内嵌原生库漏洞审计**。
- 生成 CycloneDX 1.6 格式的 Python runtime/前端依赖清单，共 69 个组件。
  原先三个缺少 License 字段的包已按镜像内许可证文件补上 Apache-2.0/MIT；
  这不代表所有原生依赖、OS 或数据集的分发许可已完成法律审查。
- SQLite 根目录共享锁覆盖 Repository 生命周期；备份取互斥锁。API lifespan
  另持服务租约，防止同一数据目录被两个 API runtime 同时使用。

## 恢复与限流演练

可复现脚本（只使用自制资料与延迟 fake 模型）：

```bash
.venv-shopguide/bin/python scripts/shopguide/m7_drill.py \
  --out artifacts/shopguide/m7/new-drill --port 8487
```

脚本创建并清理自己的服务进程，不改正在使用的演示根目录。此次在 run 为
RUNNING 时发送 SIGKILL，再启动服务；原消息重发返回同一个 run，最终完成，
会话中没有重复消息。之后停服备份，恢复到新目录并再次启动 API，读取到了
相同答案和授权原图。checkpoint 与主数据库均包含在备份中。

负载为 **同一用户 5 个客户端**：2 个请求被接受并完成，3 个按现有每用户
2 个活跃 run 上限返回 429。接受请求的样本 p95 入队 **134 ms**、完成 **2.36 s**。
仅 2 个成功样本、测试模型、同一用户限流场景，不能写成“五个推理任务并发
达标”或真实模型性能结论。正式 P-01 至 P-08 和真实 5 并发目标仍待验收。

早期演练脚本先后遇到本机代理设置、重复打开 httpx client，以及把预期的 429
误判成失败，原产物保留。已改为本地请求不读取代理、正确管理 client 生命周期，
并单独统计限流；未通过放宽限流来让测量变绿。

## 离线备份、恢复与数据回退

所有写入者必须使用当前 RootLock 版本并停止。锁是进程间协作锁，不能保护
不遵守协议的外部程序或仍运行旧代码的服务。它不是在线备份协议。

```bash
# 先停止使用该根目录的服务、导入器和 CLI。
.venv-shopguide/bin/shopguide ops backup \
  --root .shopguide/your-stopped-data --out .shopguide/backups/v1
# 保存输出中的 manifest_sha256，放在备份目录之外。
.venv-shopguide/bin/shopguide ops verify-backup \
  --backup .shopguide/backups/v1 --manifest-sha256 <保存的SHA256>
.venv-shopguide/bin/shopguide ops restore \
  --backup .shopguide/backups/v1 --manifest-sha256 <保存的SHA256> \
  --target .shopguide/restored-v1
```

备份包括 metadata.db、存在时的 checkpoints.db、assets、indexes、uploads 和
schema/snapshot/prompt 清单；SQLite 通过 backup API 创建一致副本。不会复制
LLM.config、运行锁或任意额外配置文件。备份目录权限为 0700，内含用户业务
数据，仍应按私有数据保管；未实现加密或远端备份。

恢复前校验清单 SHA、每个文件的大小/哈希、路径和 schema；拒绝软链接、越界
路径、未登记文件、损坏内容及已存在的目标目录。恢复后验证 SQLite integrity，
清空旧 HTTP 凭据，业务会话/checkpoint 保留。重新启动时须重新挂载私有模型
配置；备份不保存密钥，也不声称锁定了供应商不可变模型版本。

数据回退方式：停止原服务 → 保留故障根目录 → 从已验证备份恢复到新目录 →
启动新目录并检查 readiness/答案/原图 → 明确切换使用的根目录。此次验证的是
**同版本数据恢复**，跨镜像版本/破坏性迁移的回滚没有验收，不执行 schema 降级。

## 本地容器候选

采用单个容器：FastAPI 服务构建好的 React 静态文件，内嵌有限线程 worker，
共享 SQLite/LanceDB 数据卷。相对 PLAN 拟议的三个服务，这是保守的单机决策，
未引入多个 SQLite 写 worker 或宣称水平扩展已验证。参见
[Docker 构建实践](https://docs.docker.com/build/building/best-practices/)。

```bash
npm --prefix web ci
npm --prefix web run build
# 默认基础镜像已固定为官方 Python 3.12 平台摘要，见 base-image.lock.json。
docker build -f deploy/shopguide/Dockerfile -t shopguide:supply-chain-candidate .
# 确保本机 8484 未被其他实例占用；不会自动停止你的现有预览。
docker compose -f deploy/shopguide/compose.yaml up -d --no-build
```

容器默认 **fake 模型、本地演示身份**，只监听 127.0.0.1，采用 Linux host 网络；
这是本机演示配置，不适用于 Docker Desktop 的全部网络模式，也不是公网部署。
不要把 demo bootstrap 放到公网反向代理后；正式身份系统/HTTPS 入口尚未接入。
运行用户为 10001，根文件系统只读，丢弃全部 capabilities，启用
no-new-privileges，仅 data/backups 卷和临时目录可写。默认不挂载模型凭据。

本机 Docker Hub 拉取超时，实际验证使用已有的镜像缓存，以这个 digest 覆盖
`PYTHON_IMAGE` 构建参数：

```text
swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/python:3.12-slim-bookworm@sha256:52331dac368d5bcf1c00d30a782753d28bb7286a12b7bb629f388cec4cfd6fb2
```

因此本次实际运行的是 **Python 3.12 镜像候选**；默认 Python 3.11 Docker 构建
未在本机完成，不能混写。已扫描所有候选镜像层，未含当前配置密钥。镜像 ID
与安全参数保存在 `container-audit.json`，未验证镜像供应链来源、签名或 OS 漏洞，
不得把本地候选当作已批准的分发镜像。GitHub CI 新增容器构建/健康检查任务，
本轮未在远端运行。

Compose 卷内冷备份可在停服后执行：

```bash
docker compose -f deploy/shopguide/compose.yaml stop app
docker compose -f deploy/shopguide/compose.yaml run --rm --no-deps app \
  ops backup --root /data --out /backups/v1
# 将上一步输出的 SHA 作为下列参数；恢复目录必须不存在。
docker compose -f deploy/shopguide/compose.yaml run --rm --no-deps app \
  ops restore --backup /backups/v1 --manifest-sha256 <SHA256> \
  --target /backups/restored-v1
```

恢复后先使用 `serve --root /backups/restored-v1` 在临时实例验证，再更新启动根目录；
不要覆盖原 /data。ops 只提供当前 sg0004 恢复；升级 schema 后须重新审阅恢复兼容性。

## 发布门禁与剩余工作

```bash
.venv-shopguide/bin/shopguide ops release-check \
  --checklist configs/shopguide/release-candidate.json \
  --out artifacts/shopguide/m7/release-decision.json
```

当前应返回退出码 2、`do_not_release`。检查器验证每项人工登记证据的存在性和
SHA；它不是自动判断测试或法律结论的智能评分器。即使所有证据齐全，也仅
输出 eligible_for_manual_review，不会自动部署。

正式 PM209、完整公平 ECom 对照、真实模型性能、剩余产品场景、生产认证、
供应链/OS 审计、全部许可和跨版本回滚仍未完成。第 24 节据此保留未通过项；
不能为了宣布 M7 完成而使用 fake 指标或单题结果关闭这些门槛。
