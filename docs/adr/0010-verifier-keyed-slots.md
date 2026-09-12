# ADR 0010：核验固定键与完整性原因

日期：2026-09-10。范围：核验输出完整性、重复 ID；不调整检索、Writer 内容、图片来源绑定或核验放行门槛。

## 验收条件

1. 不把重复数组记录删掉后冒充一次合格核验；不覆盖 complete=false。
2. 每个后端要求的 claim 和 step/image 对都有且仅有一个判断；未知、缺失、冲突和重复 JSON 键拒绝。
3. supported=false、整体 incomplete、未覆盖要求仍不能交付。
4. 使用同一份既有答案/证据/图片复验核验器，排除 Writer 重生成造成的变化；保留负向对照。
5. 新协议与提示必须登记，旧基线不重写、不混分。

## 决策

- 旧协议要求模型重复输出长 claim ID 和 step/asset ID，模型会重复某个条目。
  改用 `VerificationWire`：后端为原有唯一核验项分配 c0/c1…、i0/i1…，把映射写入
  `verification_slots`，动态构建所需键的闭合 JSON 对象 schema。每个键对应 supported/explanation。
- 只有完整且无额外键的响应才映射回原 canonical SemanticVerdict。支持布尔值严格检查；
  不猜键、不补漏项、不改判决、不从 explanation 推断真假，不做重复条目合并。
- 原始 API envelope 和模型 JSON 解析都拒绝重复键，禁止 JSON 默认“最后一个值覆盖前值”
  隐藏重复。legacy/custom gateway 返回的原数组仍受 FixedRAG 原有唯一性/覆盖检查约束。
- `complete` 仍必须为 true 才能交付；每条 supported 也必须为 true。明确区分逐项事实
  支持与整体回答是否足够响应问题，避免既把不完整答案强行放行，也把无关手册主题当作必答项。
- 新响应必填 `incomplete_reason`：none、review_incomplete、answer_incomplete、evidence_uncertain。
  complete=true 只允许 none；complete=false 必须为其他类别。内部 canonical 契约允许
  legacy gateway 缺少原因（None），但给出原因时也必须与 complete 一致。
- 生产诊断仅记录受控原因和覆盖计数，不附上模型自由解释/原值。诊断脚本在忽略的本地
  artifacts 中保存结构化核验响应，不包含隐藏推理、凭据或图片字节。
- 提示版本为 fixed-rag-v5；真实 Responses/DeepSeek 网关登记 verification_wire=keyed-slots-v1，
  scripted/legacy gateway 登记 canonical-arrays-v1。配置指纹区分协议，不把旧运行当新协议结果。
- 不增加重试、请求上限、输出 token 上限或图片/证据预算；未新增依赖。

## 验证与边界

在旧固定答案上：重复 ID 样例两次都产生唯一完整判断并通过；原 incomplete 样例两次
仍为 answer_incomplete，正确保留拒绝。四个正常对照均通过。无依据事实、答非所问
两个真实负向对照均拒绝。端到端两例一例完成带图回答，一例在核验响应阶段失败；未追加重试。
本轮固定总预算 16 请求，已用 16；这不是完整 50 题覆盖率复测，不能声称全量质量提升。
