# 本轮覆盖率损失分析

2026-09-10。只分析既有 Workbuddy v6 的 300 条预测、运行事件和当前冻结源码，没有新增模型请求。覆盖率为 answered+partial，不是正确率。

## 直接损失

| B2 协议 | 已覆盖 | Writer 拒答 | 生成/选择/核验响应错误 | 组装契约拒绝 | 核验声明不完整 |
|---|---:|---:|---:|---:|---:|
| given-page | 36 | 6 | 6 | 1 | 1 |
| retrieved-top1 | 27 | 12 | 6 | 4 | 1 |
| multipage | 30 | 7 | 11 | 1 | 1 |

每行 50 条。150 条 B2 中缺失 59 条：27 条拒答、23 条模型调用/响应/结构验证错误、6 条组装契约拒绝、3 条核验声明不完整。23 条响应错误进一步为 Selector 7、Writer 10、Verifier 6；它们是阶段归类，不是已确认的底层根因。部分错误只记录 RuntimeError/VALIDATION_OR_DEPENDENCY_FAILED，不能直接判成网络故障或模型能力不足。

## 证据支持的原因

1. **多模态链路可靠性不足。** B2 有 32/150 条 error，B1 有 11/150 条。尤其多页中，B1 已覆盖而 B2 未覆盖的 13 题里，12 条是 error、1 条拒答。B2 增加 Selector 并传递图像，失败点更多；同题结果支持优先修执行链路，但不构成多模态本身更差的因果证明。B2 有 12 次接近 25 秒且无 usage 的事件，代码默认 timeout=25 秒，提示超时风险；现有日志无法确认每次失败的具体原因。不得直接靠无限重试掩盖。
2. **生成结果与输出契约仍不一致。** 6 条组装失败中，5 条因非空 unresolved_items 被 UNVERIFIED_UNRESOLVED_TEXT 拒绝，1 条引用 UNKNOWN_EVIDENCE。_assemble 当前对任意非空 unresolved_items 拒绝整份答案，因此可能把有有效主体但附加未解决事项的答案整体丢弃；是否可安全保留主体须逐例查看，不能直接去掉校验。另有 3 条核验声明不完整，以及 1 条显式核验 wire 字段错误（包含在 23 条响应错误中；另 1 条槽位错误属于 B1）。当前 Chat json_object 只要求 JSON，对业务 schema 的一致性仍靠提示和本地验证，不能视为服务端强 schema 保证。
3. **检索和上下文构造均有损失。** retrieved-top1 和 multipage 实际都只有 33/50 题访问 gold 页；B2 未访问 gold 的 17 题中，单页 8 条拒答、3 条错误，多页 6 条拒答、3 条错误。多页初选前 4 页中有 37 题命中 gold，但拼接后直接 evidence[:32]，4 题 gold 候选被截掉；这解释为何增加页面预算没有提高实际 gold 页覆盖。访问 gold 页仅表示页存在，不证明关键区域被保留；未访问 gold 页也不证明其他页无可用答案。
4. **拒答不全由缺页解释。** given-page B2 全部有 gold 页仍拒答 6 题；检索单页访问 gold 的 33 题中仍有 4 条拒答、8 条错误，多页有 1 条拒答、10 条错误。Writer 可能缺关键区域/图片、未能理解或过于保守，但生产 run 只保留 WRITER_ABSTAINED 和通用回复，没有原始拒答理由，不能把 27 条拒答全部归因为“资料不足”或“误拒答”。
5. **图片候选选择存在偏差风险，尚未量化。** 当前先按区域类别排序，再取前 4 张作为 Selector 候选，最终最多 2 张交给 Writer；不是按问题相关性给全部图片排序。相关图片可能未入围，读图能力存在不代表任务所需图像实际被送入。需增加候选/选择/关键图命中审计后才能计数归因。

本轮没有再出现 DISPLAY_ASSET_EVIDENCE_MISMATCH；旧的图片来源绑定错误已不是当前观测到的主要损失。真实 BGE 已工作，当前没有证据支持将问题归因为“未接入真实 BGE”或“模型不支持多模态”。

## 修复优先级

1. 先将响应失败诊断细分为连接/读取超时、HTTP、空输出、截断、JSON 解析及 schema 校验，并记录受控字段路径、finish_reason 和用量是否存在；不记录凭据或隐藏推理。优先复现多页 Selector 失败。
2. 在相同 32 条预算内按问题相关性和页面配额构造上下文，避免第一页占满；候选图片也做相关性排序。冻结旧结果，先离线比较上下文命中，不使用 gold 干预线上选择。
3. 修复 unresolved_items 的表达与核验协议：把已验证答案与需澄清事项明确分开，未知引用仍拒绝；不删除来源或完整性门禁。
4. 为拒答增加受控原因码，审查有 gold 页仍拒答的案例，再决定是否调整提示或补查证据。用独立样例复验，同时检查正确性和区域指标，不能只追求覆盖率。

23 条 B2 响应错误即使全部恢复，也只是理论上最多增加 23/150=15.3 个百分点；后续仍可能拒答或核验不通过，因此不是预计收益。拒答中的必要部分应保留。

证据：`artifacts/shopguide/val50-workbuddy-v6/failure-audit.json`、`per-question.json`、`coverage-loss-cross-tabs.json`、逐题 runs 与 events；历史截断审计位于 `artifacts/shopguide/m03-completion/val50-proposal/multipage-budget-audit.json`。源码：`shopguide/qa/fixed.py` 的 `_retrieve`、候选图片构造及 `_assemble`，`models/responses.py` 默认超时与 `models/providers.py` Chat 适配。完整评分见 [复测报告](m03_workbuddy_retest.md)。
