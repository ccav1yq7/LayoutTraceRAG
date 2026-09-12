# RAG 前置意图候选层：第一阶段实现

按 PLAN §8.7–8.11 执行。本轮交付意图层的规则/槽位/样例向量索引、融合、重排接口及 Judge 接入；不是整个 RAG 改造完成。原说明书 RAG 与主从分工继续保留，人工暂停。

## 已实现

- `intent/candidate_rules.json`：版本化六类正向/负向规则及槽位关联。规则匹配留原文位置；否定信号不直接删除已有目标。规则版本与 taxonomy 一致性检查，配置 hash 纳入 Agent 配置并在使用前检查。
- `intent/candidates.py`：独立 intent_examples 命名空间，稳定 example_id、目录/规则/模型/样例 hash、向量维度和有限数校验、精确余弦检索及可导出索引快照。当前小目录每轮构建内存索引，可导出 JSON；尚未加入跨请求索引复用/快照加载和线上迁移。意图样例不进入手册引用。
- 保留 raw_query，NFKC/空白归一化仅用于检索；工单号、订单项、型号提取标记 format_only_unverified，不作为对象授权。未实现独立 NER 或复杂纠错。
- Embedding 样例 Top10，按意图聚合 Max/Top3 均值，记录 margin；规则与槽位各留分数和原文依据。采用 RRF 秩融合而非直接相加不同尺度分数，输出融合 Top5。
- 可复用现有 reranker 对 query–意图定义/样例做精排，记录模式、模型和 revision。真实配置禁止悄悄使用 fake reranker；关闭分支显式标记。现有 BGE reranker 是通用零样本适配，未训练意图专用模型。
- Judge 同一次 intent 调用增加 candidate_evidence，状态仍由原 Delta/reducer 管理；不会额外增加第二次 Judge。近期历史和完整状态保持原接口。候选审计存入 AgentState，进入持久 run。
- 当前六类目录保留完整目录给 Judge，记录活动/暂停/待定的保护项，避免 Top3 剪掉另一个诉求；这是明确的 full_catalog_recall_guard 策略，不宣称已经完成大目录缩减。超过 32 类拒绝并要求另行配置/验证。
- BERT 明确 enabled=false / no_trained_classifier，未产生伪分数。训练、标注数据与模型发布待后续完成。

## 验证

第一组意图候选/状态/主从 25 项通过；新增完整 Agent 接入检查后，候选与 Agent/API 回归 38 项通过。验证无效向量/精排结果及规则版本明确失败、原文依据不变、暂停目标不被候选剪掉、索引导出 hash、一轮只有一次 Judge、fake/real 模式可审计。Ruff/Mypy 通过。

真实本地 BAAI/bge-m3 与 BAAI/bge-reranker-v2-m3 使用已有锁定 revision 和本地权重，处理六个自编开发样例，导出独立样例索引与逐路 trace：六例的全部预期意图均在融合 Top5、精排 Top3。规则咨询和工单查询等样例的重排 Top1 有错误，不能以 Top1 自动判定，也不能以此六例宣传模型准确率。

证据目录：`artifacts/shopguide/intent-candidates-bge/`。真实 Judge 在预先构造的“暂停申请＋两项咨询”状态下仅尝试一次，返回 RuntimeError；本次脚本仅保留 MODEL_OR_SCHEMA_FAILED，无法追溯具体传输原因，未声称 Judge 通过。无自动重试，记录在 `artifacts/shopguide/intent-candidates-judge/`。

本轮 BGE 本地推理不消耗 API 请求；Judge 尝试 1 次计数，累计 **870/870**。本轮已停止真实请求。阶段代码与指标不覆盖历史实验工件。

## 尚未完成

- BERT 分类器训练与权重、意图专用 Cross-Encoder 训练、独立人工标注测试集。
- 大目录按多诉求分组召回、预算内候选缩减、最近客服提问意图显式保留、分支故障的完整发布降级策略。
- 开放槽位 NER、校准置信度、数据审核/私有样例删除同步、持久向量索引加载与缓存。
- 资料 RAG 的跨页证据预算和图片相关性排序改造，以及完整真实主从场景联验。

因此 PLAN 新增大任务保持未关闭；本轮完成的是可运行前置候选管线及集成，不是“完整四路已实现/全部 RAG 已验收”。

后续 BERT 开发版已实际微调并接入可选分支，详见 [微调记录](bert_intent_finetuning.md)。上文未训练状态是候选层第一阶段记录；模型仍未默认上线，真实数据验收未完成。
