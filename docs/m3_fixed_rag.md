# M3 固定图文问答：工程交付与待验收项

2026-09-10：完整文字评分现已接入并完成隔离参考验证；真实 BGE 局部验证已通过。
最新命令与剩余基线门槛见 [M0–M3 收尾记录](m03_completion.md)。以下保留原交付历史，
其中“正式模式拒绝运行”指当时状态；当前正式入口需冻结清单和完整参考环境。

2026-09-09：B1/B2 工程主链、模型网关和 PM209 预测/区域诊断接口已实现。
真实模型效果与完整官方评分尚未验收；这些依赖继续按独立问题登记推进。

B1：检索允许的文字 → Writer → 独立语义核验 → 结构化结果。
B2：检索允许的资料 → 原图选择 → 图文 Writer → 独立图文核验 → 结构化结果。

Writer 只返回内容和引用 ID，不能指定用户、商品范围、source locator 或
verification。后端生成 claim、step 和 citation，核对登记记录、型号变体、
授权文档与 snapshot。摘要、前提和步骤均须有证据；每条 claim 和每个步骤/
配图对都必须获得独立核验结果。核验后再次检查权限和源文件，防止调用
期间撤销资料仍通过缓存输出。失败时移除待发布步骤和引用并返回拒答。

图片只能从本次可用候选中选择，选择/写作/核验接收的是实际图像字节。
缺图可以降级为有明确提示的文字结果；hash 损坏、越权、未知 ID 或跨型号
则拒绝回答。B1 不给模型发送图片。固定链路不执行工具、任意 URL 或命令，
也不执行 Agent 的动态补查/修复循环；后者属于 M4。

`ResponsesGateway` 使用用户配置中的模型，所有真实 LLM 角色现为
`deepseek-v4-flash-vision-exp`，详见 [最新接入验证](deepseek_validation.md)。传输与输出解析通过本地 mock 验证；本轮没有调用故障服务。
系统提示位于 `qa/prompts.py`，源资料/问题/草稿为 user-role 数据，输出采用
严格 JSON Schema。请求禁止自动重定向和重试，设置 store=false、25 秒超时
及每次 2,048 输出 token 上限。运行记录不保存密钥、端点、图像 base64 或私有
推理内容；提供提示与配置指纹、实际调用角色、图像 hash、耗时及已返回的
usage。未提供 usage 时总 token 为 null，不能写成实测零成本。

工程默认预算：最多 4 页、32 条证据、4 个候选图、2 个展示图、3 次网关调用、
累计 8 个图像输入。发送前还检查 48,000 字符上下文上限与 90 秒总时限；字符
限制不是精确 token 估算。配置清单见 `configs/shopguide/m3-engineering.json`。
这是已固定的工程配置，尚不是完成 val 调参与预算确认的正式实验配置。

运行方式（沿用 M2 的授权商品和 snapshot）：

```bash
.venv-shopguide/bin/shopguide ask \
  --root .shopguide/m2-pm209 --snapshot snapshot_pm209_verified \
  --principal user_research --product <manual_id> --variant <variant_id> \
  --question 'Explain the available source information.' \
  --baseline B2 --fake --out artifacts/shopguide/example-b2.json
```

`--baseline B1` 为文字对照。`--fake` 必须显式选择，使用源文字摘录和脚本化
核验，不代表模型理解或语义支持；公开结果的 semantic 保持 not_checked。
去掉 --fake 时必须同时提供 `--private-config` 和带 revision 的 embedding/
reranker 配置；没有静默 fallback。CLI 输出新文件，不覆盖已有 run。

评测接口分离：

```bash
# 仅评测侧读取 gold，导出问题白名单；此命令不调用模型
.venv-shopguide/bin/shopguide pm209-requests \
  --private data/pm209/prepared-v1/eval_private --split val \
  --profile pm209-retrieved-top1 --out <requests.jsonl>

# 预测器不接受 gold 路径；root 必须已导入请求涉及的资料并授予 principal 权限
.venv-shopguide/bin/shopguide pm209-predict \
  --requests <requests.jsonl> --root <runtime-root> --snapshot <snapshot_id> \
  --principal <principal_id> --baseline B2 --fake --out <predictions.jsonl>

# 工程区域诊断，明确不能当作完整官方评测
.venv-shopguide/bin/shopguide pm209-score \
  --requests <requests.jsonl> --predictions <predictions.jsonl> \
  --private data/pm209/prepared-v1/eval_private \
  --corpus data/pm209/prepared-v1/corpus --engineering --out <diagnostic.json>
```

支持的三种 profile：given-page 明确携带已知页面；retrieved-top1 在读图和
Writer 之前只保留检索第一页面；multipage 是当前固定多页扩展，不称为 Agent。
问题原文保留；任何 answer/gold/主体字段进入请求均被拒绝。预测文件只记录
实际引用的区域及配图，旁边 `.runs` 目录保留实际调用轨迹。缺失预测和失败
保留在分母；非法、重复、跨手册或未访问区域以及混合配置会拒绝评分。

区域包装与锁定的官方函数完成 8 组 1e-12 容差 parity：完美、空、错误、重复、
未知候选、错页部分 gold、多 gold、混合实例。严格区域指标另列，保留完整
正确区域分母；页面 recall 是诊断统计，不冒充官方逐手册聚合。来源为
[AIM3-RUC/MPMQA pinned evaluate.py](https://github.com/AIM3-RUC/MPMQA/blob/5226a9aa849fd3b8f35620e7b7d7d10c09d754a2/evaluate.py)。

```bash
.venv-shopguide/bin/python scripts/shopguide/m3_region_parity.py \
  --source external/MPMQA/evaluate.py --out artifacts/shopguide/m3-region-parity.json
UV_PROJECT_ENVIRONMENT=.venv-shopguide make test-qa
```

本轮验证：

| 内容 | 结果 |
|---|---|
| 新增 M3 用例 | 24 项通过 |
| 全套回归 | 111 passed；14 条原有弃用警告 |
| 官方区域函数 parity | 8 组通过；无文字指标替代或伪造 |
| 三个 profile 往返 | 自制资料/问题上预测、ID 校验和区域诊断通过 |
| gold 隔离测试 | 阻断 eval_private 文件访问时，预测仍成功 |
| 真实来源 CLI smoke | 已登记 PM209 页面上的 B1/B2 工程 fake 链路通过 |
| Ruff / Mypy | 通过，类型检查覆盖 38 个模块文件 |
| 打包 | sdist/wheel、独立安装后的 QA/提示/指标模块和 CLI 通过 |

本地样例：`artifacts/shopguide/m3-B1-final.json`、`m3-B2-final.json`，以及
`m3-preview/preview.md`。这些仅展示工程路径和登记原图，不是有模型效果
证明的商品使用指南，未公开第三方图片。

仍待完成的 M3 退出条件：当前 DeepSeek 模型与真实 BGE 组合验证、完整 NLGEval
文字评分/兼容性验证、50 个 val 真实冒烟与 B1/B2 基线、基于实测的正式
模型 revision/提示/预算及效果门槛冻结。当前 pm209-score 只提供显式工程
诊断，正式模式拒绝运行。这些待验收项见 [外部问题登记](issues/external_blockers.md)。
