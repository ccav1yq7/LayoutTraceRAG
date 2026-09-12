# ShopGuide

**面向已购商品的图文使用指导与售后问答 Agent —— 以证据可追溯的 RAG 为核心。**

用户选定自己购买的商品后，助手能查到**适用的说明书**，按步骤回答，并展示来自
**真实资料的原始图块**；继续追问时能沿用正确上下文、补查证据或澄清，而不是编造
图像和操作方法。

> LangGraph · LanceDB（向量 + 全文）· BGE-M3 / bge-reranker-v2-m3 · PDF/原图证据管线 · FastAPI + React

## 问题

售后场景里的回答不能只是"听起来合理"：

- **型号必须对**。相似的机型说明书混用会直接导致错误操作。
- **步骤必须能追溯**。每一句操作说明都要能指回具体手册的**页码与原始图块**。
- **不够时要承认**。资料不足、缺图、异常服务要区分清楚并明确告知，而不是补一张生成图。
- **多轮不能丢上下文**。换商品、改诉求、暂停、撤回都要正确跟踪。

## 架构

一个 LangGraph Agent，围绕**证据检索**与**强制核验**组织决策：

```
意图理解 → 证据检索（RAG）→ 工具决策 → 图文答案生成 → 独立核验
                ↑                                          │
                └────────────── 澄清 / 补查 ⟲ ──────────────┘
```

- **意图理解** — 独立的多诉求识别与多轮变化跟踪（continue / update / 新增 / 撤回）。
- **证据检索（RAG）** — 说明书页面与原始图块作为带定位信息的证据单元；范围限定在
  用户已选型号，混合检索 + 重排后进入上下文。
- **工具决策** — 有界的多轮工具调用：查手册、取原图、创建／查询模拟工单、请求补拍。
- **生成** — 严格依据检索到的证据组织回答，按问题分点，配图与内容状态分离。
- **核验** — 独立核验回答与引用证据是否一致；不通过则标出不把未验证内容当作结论。

## 代码结构

```
shopguide/
  agent/        有界 Agent、工具契约、账本、诊断、专精分工
  qa/           B1/B2 固定问答、上下文组装、核验线协议
  api/          FastAPI 应用、受保护原图上传、SSE、会话恢复
  retrieval/    证据检索与作用域化存储
  ingest/       说明书 / PDF / 原图确定性入库管线
  intent/       意图库、候选检索、BERT 分类器
  benchmarks/   PM209 / ECom 评测适配、评分与请求记账
  ecom/         原生 ECom MCP 与隔离 trial 运行器
  models/       模型提供方与响应契约
  storage/      快照、仓储、加锁、Alembic 迁移
  sessions/     持久会话
  ops/          备份、发布门禁
  schemas/      证据与商品数据契约
web/            React + Vite 工作区前端
tests/shopguide/  30 个测试文件
configs/        shopguide / ecom / pm209 / intent 配置
docs/           设计与验收记录、ADR
```

## 当前状态

**本地演示链路可用，工程实现已推进较多；完整计划和最终验收尚未完成。**

发布证据复核仍为 **do_not_release**。

| 范围 | 已有 | 未关闭 |
|---|---|---|
| M0–M3 | 真实 BGE 522 页建库、50 题三协议检索与评分、固定 B1/B2 与独立核验 | B2 稳定性与效果、完整 PM209 验收 |
| M4–M5 | 有界 Agent、持久会话、FastAPI + React 工作区、SSE、取消与恢复 | 全部产品场景（E-01 至 E-12）联验 |
| M6 | 原生 ECom MCP、隔离 trial、官方评分桥接 | 完整任务重复实验与公平对照 |
| M7 | 本地容器、备份恢复、供应链扫描 | 生产认证、真实性能、许可与最终发布 |

第三轮真实验收在累计 1345/1345 时停止，执行了 16/24 条消息，**不标全量通过**。
逐项代码证据与关闭条件见 [`docs/plan_acceptance_audit.md`](docs/plan_acceptance_audit.md)，
完整开发计划见 [`PLAN.md`](PLAN.md)，实施状态见 [`docs/shopguide_status.md`](docs/shopguide_status.md)。

## 安装

```bash
uv sync --locked --extra dev --extra storage --extra shopguide --extra shopguide-dev
```

真实 BGE 向量检索需要额外的嵌入栈：

```bash
uv sync --locked --extra retrieval
```

## 使用

```bash
# 配置与数据库检查
uv run --no-sync shopguide doctor --config configs/shopguide/offline.json

# 生成契约
uv run --no-sync shopguide schema --out docs/shopguide-contracts.json
```

## 测试

```bash
uv run pytest              # 290 项，无需模型或密钥
make lint                  # Ruff + Mypy
make test-unit             # 单元
make test-qa test-agent test-api test-ecom test-ops
```

测试通过数只证明被测内容，不替代验收条件。离线 hash 向量结果显式标注为 fake，
不作为语义检索成绩。

## 数据、模型与许可

本仓库**不含**视频、数据集、模型权重、索引或 API 凭据，也未连接任何真实业务账户。
PM209、ECom-Bench 及 BGE/CLIP 等模型须自行下载并遵守各自条款。

本仓库仅含**合成演示**依据与公开许可范围内所需的引用。`artifacts/`、`.shopguide/`、
`data/`、`.cache/` 与本地 `LLM.config` 始终不入库。

配置了模型凭据时，问题与检索到的证据文本会发送给该服务方。
见 [第三方组件说明](THIRD_PARTY_NOTICES.md)。

## 历史沿革

本项目最初探索的是**长视频证据可追溯问答**（原 LayoutTraceRAG）：把讲座、会议与教程的
音轨 ASR、画面文字和关键帧作为带时间戳的证据节点，用 agentic RAG 回答问题并回引播放时间码。

该方向**已舍弃**，不再包含在本仓库中。保留下来的核心思路——证据必须可溯源、答案必须经核验
——成为 ShopGuide 的基础：证据单元由「时间码片段」换成了「说明书的页码与原始图块」。

旧方向的代码仍保留在开发工作区 `LayoutTraceRAG/legacy/layouttrace-core/`，不参与本仓库的
构建与测试，也不随本仓库发布。

## License

MIT — 见 [LICENSE](LICENSE)。第三方库、模型与数据集保留各自许可。
