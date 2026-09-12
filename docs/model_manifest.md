# 当前模型：deepseek-v4-flash-vision-exp

> 2026-09-10 最新状态：本地 Workbuddy `deepseek-v4-flash` 完整复测与六组评分已完成，300 条预测，新批次 666 次、累计 799/870 次请求。见 [最终复测记录](m03_workbuddy_retest.md)。下文此前未重跑/待预算的描述为历史阶段状态；M3 稳定性、效果和发布验收仍未通过。

2026-09-10 收尾更新：完整评分参考环境与真实 BGE 权重/局部验证现已补齐；
当前运行条件、适配差异和剩余基线门槛见 [M0–M3 收尾记录](m03_completion.md)。
以下原始记录保留其当时语境，不代表当前评分/BGE 仍未接入。

2026-09-09 最新指定覆盖此前 GPT 配置。当前入口为 LLM.config，DeepSeek 视觉
探测和 B2 三阶段真实调用已通过。详见 [DeepSeek 验证](deepseek_validation.md)
与 [ADR 0005](adr/0005-deepseek-provider.md)。以下保留此前验证历史，不代表当前配置。

# ShopGuide model manifest

Date: 2026-09-09. The user explicitly selected **gpt-5.6-terra for all LLM roles**.
Planner, vision and verifier use that selection; no Qwen fallback was used.
`LLM.comfig` model and review_model fields were updated and the file is ignored by
Git. The credential and endpoint are not reproduced in this document.

The configured wire API is Responses. Capability probe payloads contain only
synthetic text or a self-authored image with two shapes. Each invocation permits
at most two requests, 300 output tokens/request, a 45-second timeout, no automatic
retries and store=false. Each report states the requested model and actual status.

Three two-request probes were attempted while resolving the user's model selection
and base-URL path handling. The first two used an appended /v1 path; the final
script honors the exact configured base URL. The recorded requested model is
`gpt-5.6-terra` in all three reports. Initial statuses were 502/503, then 503/503;
final exact-base-URL probe returned **503 for planner and 503 for vision**.
No valid model answer, usage or returned model identity was received. These are
transport/service failures, not failed model reasoning scores.

Current status: **BLOCKED** for real structured-action output and real visual
observation. Immutable model revision, serving configuration and provider tariff
are unknown. No monetary cost is invented; the service did not return token usage.
The bounded attempts do not authorize a full paid benchmark run.

Reproduce after service availability is restored:

```bash
uv run --extra shopguide python scripts/shopguide/model_smoke.py \
  --private-config LLM.comfig --out artifacts/shopguide/m0/model-smoke.json
```

`ScriptedPolicy` and `FakeVisionObserver` have `model_mode=fake`; their tests prove
contracts only. Formal Settings reject fake mode, absent model revisions, absent
data manifest and unapproved/pending manifest values. Config validation does not
claim endpoint capability or verify a provider's model identity.

BGE-M3 embedding and BGE reranking remain planned retrieval components; no model
revision is pinned and no weights or real vectors were produced in this round.
Hardware observations are in [baseline audit](baseline_audit.md).

2026-09-09 后续诊断：代理/直连、最小请求及流式对照指向转发服务上游访问被拒绝。
见 [502/503 诊断报告](model_service_diagnosis.md)。已改善 smoke 错误分类，6 项相关离线测试通过；服务仍未恢复。
