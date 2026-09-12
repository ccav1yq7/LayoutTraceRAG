# ShopGuide M0 benchmark protocol

2026-09-10：50 题真实三协议 B1/B2 已完成（300 预测、六组完整评分、595 请求）；
[结果与限制](m03_val50_baseline.md)。本轮不是完整 test 或 URA 复现，B2 可靠性未验收。

2026-09-10 收尾更新：完整评分参考环境与真实 BGE 权重/局部验证现已补齐；
当前运行条件、适配差异和剩余基线门槛见 [M0–M3 收尾记录](m03_completion.md)。
以下原始记录保留其当时语境，不代表当前评分/BGE 仍未接入。

Date: 2026-09-09. Full official benchmark support is not implemented.

PM209 is pinned at `5226a9aa849fd3b8f35620e7b7d7d10c09d754a2`. The parity
script verifies HEAD and exact `evaluate.py` bytes, extracts the unchanged
`compute_visual_answer_metics` function AST and executes it with scikit-learn
metrics. It avoids importing DeepSpeed, Detectron2 and NLGEval into the application.
An independently written set-counting reference agrees on all six region metrics
for five synthetic cases: perfect, empty, wrong-page, duplicate predictions and
mixed instances. This validates that function only; it is **not** a full scorer,
text-score reproduction, URA baseline or benchmark result.

```bash
uv run --extra shopguide-dev python scripts/shopguide/scorer_parity.py \
  --source external/MPMQA/evaluate.py --out artifacts/shopguide/m0/scorer-parity.json
```

The function's candidate-universe membership semantics are retained: predictions
outside that universe are ignored. Undefined precision/recall default to the
upstream scikit-learn behavior. Warnings are suppressed only inside synthetic
parity invocation. No upstream source was patched.

Full NLGEval text scoring and official environment baseline smoke remain BLOCKED
until the isolated reference dependencies/resources and model services are working.
Given-page, retrieved-top1 and multipage profiles must be separate; no fake result
is permitted in effect tables. Test questions/answers/gold mappings stay outside
runtime corpus and the agent process.

ECom is pinned at `bc5018daaf45eea12330941bce68cebd293cfa85`. Its observed
reward is action × search × output; time is reported separately. Official tool
traces are read from actual message tool_calls. Each trial needs its own mutable
business data directory and MCP process. A Responses capability test cannot stand
in for an ECom trial. All 53 tasks, including unavailable-image cases, remain in the
inventory. No task-specific policy or answer was authored from task contents.

For official multi-trial reporting, pass^k is C(c,k)/C(n,k), with complete trial
coverage; do not substitute pass@k. M6 will implement and test the adapter and report.

M3 更新：预测、评测侧问题导出与区域诊断已拆分实现；生产区域包装与官方
函数通过 8 组 parity。三种固定 profile 的合成往返测试通过，真实 val 与
NLGEval 完整文字评分仍未运行。参见 [M3 说明](m3_fixed_rag.md)。
