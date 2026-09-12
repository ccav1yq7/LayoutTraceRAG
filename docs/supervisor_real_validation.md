# 主从客服真实模型验证

使用用户指定 Workbuddy DeepSeek 本地路由，模型标识 deepseek-v4-flash。共两条独立会话：主从混合任务上限 12 请求，纯工单查询上限 4 请求。全部推理为真实调用；资料/订单为自编训练面板 demo，检索为 HashEmbedder/RRFReranker，未使用真实 BGE，不属于正式客服效果评测。

## 结果

| 场景 | 实际请求 | 结果 |
|---|---:|---|
| 查询当前商品工单，并明确请求操作指南专家查启动方式 | 11 | 两次指南子 Agent 委派已执行；查询结果保留；最终回答核验未通过，run=FAILED |
| 查询当前商品工单，不创建新工单 | 2 | COMPLETED，返回当前商品下无可查看模拟工单 |

混合场景实际调用角色：intent×1、plan×4、specialist_guide×4、write×1、verify×1。第一次委派两步均留下工具参数/范围错误，没有收集到证据；受控日志未保留细分原因，不能直接认定为模型能力不足或权限配置问题。第二次委派获取两条授权说明书证据并正常返回。

工单查询确实已返回 empty，而不是“查询工具不可用”。当前 finalizer 接收原始混合问题和说明书证据，没有接收 collected_service_query；Writer 因此把工单部分当作资料缺失，在 summary 中声称无法提供该信息。该信息传递缺口由代码与 draft 对照确认。Verifier 的两条 claim 均为 supported，但 complete=false、incomplete_reason=review_incomplete；这就是最终被 VERIFIER_COVERAGE_INVALID/VERIFICATION_FAILED 拦截的直接原因。日志不足以证明 complete=false 只由缺少业务上下文引起，不做单一因果夸大。

工单查询结果仍通过最终 service_query 展示，没有被失败覆盖；未创建任何工单或确认记录。纯查询场景只测试了“空结果”，本轮不宣称真实模型已通过有记录/多记录所有路径。显式要求专家的样例也不能证明模型会在自然问题中自动选择最合适的角色。

## 账本与复现

冻结工件：artifacts/shopguide/supervisor-real-v2/，含 protocol.json、两份 run/events、summary.json、audit.json。源码 hash、历史账本前缀与连续编号核对通过。

本轮 13 次请求，无自动重试，累计 **867/870**，剩余 3 次；已知输入 39,411、输出 2,777 tokens，未知用量 0，金额未估算。测试未覆盖实际模型版本不可变性、真实 BGE、生产资料、真人客服或正式性能。

结论：真实模型已运行意图识别、主控、子 Agent 和核验；纯查询可用，混合任务存在实际集成问题，不能宣称完整联验通过。下一修复点是按来源类型把已查询的业务事实传给最终回答/核验，避免用说明书推断工单事实；同时细化子 Agent 工具失败诊断。保留 mandatory verifier，不以关闭 complete 校验获得通过。

后续修复：业务查询结果已传入最终回答/核验，定向两请求真实复验通过，累计 869/870。见 [修复记录](business_context_repair.md)；上文原失败作为历史证据保留。
