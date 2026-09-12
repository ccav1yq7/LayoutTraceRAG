# M4 Agent、会话与恢复

2026-09-09：M4 后端工程链路已实现，并完成离线测试及真实 DeepSeek 小样例。
没有接入真实订单、退款、支付或外部售后系统；浏览器/API 属于 M5。

Agent 使用 LangGraph 的 plan/tool/answer/terminal 节点和 SQLite checkpointer。
业务数据库保存最新 run 状态，恢复时以此为准，再用工具账本核对实际效果。
模型调用前先保存预算消耗，工具结果保存后再前进。进程中断可能重发尚未
完成的模型请求，但不会重置预算；模型请求不宣称 exactly-once。

Planner 通过 `PlanDecision` 选择工具、澄清、草稿、转人工建议或拒答。
`arguments` 是字段明确的对象，随后还要经过对应工具的严格 schema 校验。
模型不能选择主体、修改 SearchScope、确认商品、批准工单或跳过答案核验。
目前注册 7 个工具：list_purchased_items、resolve_product、search_manuals、
read_page、inspect_asset、prepare_service_request、commit_service_request。
型号解析只返回授权候选，用户经后端明确选择；动态裁剪、上传和视频工具
仍属于后续范围，不因工具目录中的规划名称而宣称已实现。

search/read 返回的证据 ID 和真实 inspect 结果进入工作区；最终 Writer 只
使用这些证据及实际看过的图。M3 的结构、型号/版本及独立语义核验完整保留。
核验失败回到 Planner，并提供 repair 状态；没有剩余预算就拒答。历史回答
只作指代理解，不能代替来源；源撤销后旧观察不会再次作为当前证据发送。

会话与执行语义：

- session_id 不代表授权；每次读取均验证后端 principal。
- client_message_id 同内容重发返回同一 run，不同内容冲突。revision 检查
  防止旧页面操作覆盖新状态；一个会话只能有一个活跃 run。
- 切换商品创建新 task，清空工作证据/图片，历史 run 保留。初次商品澄清后
  选择商品保留原问题上下文。新消息可复用同任务、仍有效的来源引用。
- 每主体最多两个 QUEUED/RUNNING run；每 run 另有非阻塞文件锁防止两个
  worker 同时执行。当前范围是单机，不宣称分布式协调。
- 默认 8 次控制决策、12 次工具尝试、12 次模型调用，其中保留 2 次用于
  Writer/Verifier；最多 4 页、8 次累计图片输入、90 秒执行时限。
- 工具参数规范化后连续重复两次且无新证据会触发停止/安全收尾。
  模型请求失败、格式修正及嵌套 inspect 都计入预算；缺 usage 时不报零成本。
- provider 返回的累计 token 超过 48,000 后停止新请求；这是响应后检查，
  不是预付费金额上限。请求前仍有字符、图片数和剩余 timeout 限制。
- cancel 在节点边界和模型返回后检查。已经产生的模拟工单不会被取消动作
  假装回滚；取消结果会保留已完成的业务效果。

模拟工单使用 prepare → 人工确认 → commit。确认绑定 principal/session/task/
revision/参数 hash/有效期。模型可看到提案 ID，但不能设置 approved。
用户等待确认期间不运行模型；确认恢复后重新计算执行 deadline，已用模型/
工具预算保留。变更参数、商品或 revision 会使旧确认失效。

本地 ticket 对 confirmation 设唯一约束。写入后响应丢失，恢复先查实际 ticket，
即使确认已过期也能确认已完成效果。不能确认的副作用标记 OUTCOME_UNKNOWN，
不会自动重做。该保证仅覆盖当前模拟 SQLite 实现，不能推广到任意外部系统。

开发 CLI（--principal 为本地可信调用者参数，M5 API 必须从认证身份注入）：

```bash
.venv-shopguide/bin/shopguide session-create \
  --root .shopguide/deepseek-vision-smoke --principal user_demo \
  --snapshot snapshot_ds_vision

.venv-shopguide/bin/shopguide session-select \
  --root .shopguide/deepseek-vision-smoke --principal user_demo \
  --session <session_id> --expected-revision 0 \
  --product product_ds_vision --variant variant_ds_vision

.venv-shopguide/bin/shopguide agent-message \
  --root .shopguide/deepseek-vision-smoke --principal user_demo \
  --session <session_id> --expected-revision 1 --message-id message_first \
  --text 'How do I start this training panel? Show the relevant image.' \
  --test-retrieval --private-config LLM.config
```

下一条消息使用 session-show 返回的最新 revision 和新的 message-id；同一
消息的网络重发仍用原 message-id。离线执行改为 --fake，不附真实模型配置。
--submit-only 仅入队，之后用 agent-resume --run 执行。agent-status 查看结果
及事件，agent-cancel 请求取消。agent-confirm 要求真实用户提供提案中的
confirmation、arguments-hash 和 expected-revision，批准后恢复同一个 run。
未完成 run 恢复时若模型、工具 schema、预算或控制器配置变化，会拒绝混跑；
取消仍可完成，不要求重新调用模型。

工程对照通过 `scripts/shopguide/compare_agent.py`。`B2_shared_tools` 是固定
search → read → inspect → finalization 控制器，与 B3 使用同一工具执行器、
源 snapshot、模型和外层预算。它不是历史 M3 B2 的改名，不混入旧分数。

```bash
.venv-shopguide/bin/python scripts/shopguide/compare_agent.py \
  --root .shopguide/deepseek-vision-smoke --principal user_demo \
  --product product_ds_vision --variant variant_ds_vision --snapshot snapshot_ds_vision \
  --question 'How do I start this synthetic TEST-DS training panel?' \
  --test-retrieval --private-config LLM.config --out <new-comparison.json>
```

真实 DeepSeek 验证使用自制面板和测试 hash/RRF，未使用正式 benchmark gold：

| v2 运行 | 模型调用 | 图像输入 | 已报告 token | 结果 |
|---|---:|---:|---:|---|
| B2_shared_tools | 3 | 3 | 4,608 | 完成，独立核验 supported |
| B3 首轮 | 7 | 3 | 13,433 | 完成，包含 1 次格式修正 |
| B3 追问 | 3 | 2 | 9,864 | 沿用上下文完成，独立核验 supported |

这组样例没有证明 Agent 更省成本或更准确；第一轮调用开销更高。测试检索与
真实模型混合模式仍明确标记为工程用途，不作为正式效果分数。

首轮负结果也保留：DeepSeek 将要求字符串的 arguments_json 返回成对象，
B3 两次契约校验失败；固定对照还发生重复读图输出解析失败。之后改为严格
参数对象并明确 JSON 指令，补上解析异常的账本终态处理及未知 usage 标记。
旧失败运行的 token 汇总没有包含所有失败请求，不能用作总费用对比。

| 验证 | 结果 |
|---|---|
| M4 新增测试 | 25 项通过 |
| 全套测试 | 140 passed；14 条原有弃用警告 |
| Ruff / Mypy | 通过，52 个模块文件 |
| 独立安装 | sg0003 迁移、Agent 契约与 CLI 通过 |
| CLI 多轮/重发 | 初次回答、追问完成，重发返回同一 run |
| 中断与副作用 | 读后恢复、写入响应丢失、过期恢复、取消后效果、未知效果通过 |

产物在 `artifacts/shopguide/m4/`：首次负结果 live-comparison.json，修正后
live-comparison-v2.json、live-followup-v2.json，以及测试、构建和 CLI 记录。
正式 B2/B3 多任务效果比较、真实 BGE、完整评分及浏览器验收仍未完成；问题
继续登记在 [独立问题表](issues/external_blockers.md)。
