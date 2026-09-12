# 覆盖率响应失败专项排查

2026-09-10。本轮只进行历史结果审计、诊断代码补充及离线故障注入，无新增模型请求；累计真实请求仍为 799/870。旧实验源码归档、预测与评分保持原样，当前诊断源码已有变化，不能再宣称与旧 campaign 哈希一致。

## 进一步确认的事实

B2 32 条 error 中：12 条 RuntimeError、5 条 Writer ValidationError、5 条 TypeError、1 条核验 wire 字段错误、5 条未核验事项拒绝、3 条完整性拒绝、1 条未知证据。27 条正常拒答单独统计。

多页 B2 的 6 条 Selector 错误全部为 RuntimeError，耗时 25.049–25.071 秒且无 usage；它们与默认 25 秒超时高度一致。但旧日志丢失底层异常，无法追溯确认是连接等待、响应读取还是其他超时相关问题，也不能判断代理上游根因。另一条单页 Selector 错误仅耗时 2.895 秒，为 TypeError，不能与这六条合并诊断。

5 条 Writer schema 错误已经可定位：给定页 3 条 status/literal_error；多页 1 条额外字段；检索单页 1 条多个额外字段并缺 status、summary。这些是输出不符合业务 schema，不是已确认的网络错误。旧日志主动省略模型原值，因此无法知道不合法 status 的具体文本。

## 诊断缺口与补充

ResponsesGateway 原来将网络/协议/JSON 异常合并，FixedRAG 又只透传 HTTP 状态和少量核验错误，使响应截断、空输出、格式错误退化成通用错误。

现在分别保存受控原因码：MODEL_TIMEOUT、MODEL_CONNECTION_ERROR、MODEL_HTTP_PROTOCOL_ERROR、MODEL_RESPONSE_JSON_INVALID、MODEL_RESPONSE_TOO_LARGE、MODEL_OUTPUT_TRUNCATED、MODEL_OUTPUT_EMPTY、MODEL_OUTPUT_INVALID_JSON 等。Chat 的 length 与 stop+空内容分开；非正常完成继续拒绝。顶层问答 failure_detail 只透传白名单，任意服务端文本不会直接进入诊断。

timeout 仍为 25 秒，无自动重试，不放宽来源/字段/完整性校验，不改变检索与选图。本次不宣称覆盖率提高。模型报错时用量仍可能未知，未伪造为零，也未从旧日志猜测恢复。

离线测试验证直接和包装的 TimeoutError、连接错误、HTTP 503、无效响应 JSON、length 截断、空内容和正文无效 JSON；检查每例只调用一次、私有哨兵不泄露，并验证原因码贯穿问答链路。测试证据在 `artifacts/shopguide/coverage-diagnostics/tests.xml`；历史逐条审计在同目录 `historical-b2-errors.json`。

下一步应在新冻结的小样例配置下验证这些受控原因码，并单独检查上下文截断与候选图排序；旧超时现象不能靠事后补日志变成确诊。完整原因分析见 [覆盖率分析](m03_coverage_root_causes.md)。

## 定向真实验证完成

11 个预先固定的旧失败样例全部运行一次：9 answered、1 partial、1 error；使用 33 次请求，累计 **832/870**，剩余 38 次。6 个旧多页 Selector 失败均通过选图并交付（5 回答、1 部分回答）；5 个旧 Writer schema 失败均通过 Writer，其中 4 个回答、1 个在 Verifier 阶段报 `MODEL_OUTPUT_INVALID_JSON`。

新诊断在真实失败中已验证生效；本轮没有复现超时，也没有证明旧超时问题已解决。只有诊断代码变化，输出存在运行波动，10/11 交付不能视为修复后的全量覆盖率或正确率。未运行完整正式评分，不覆盖旧 300 条基线。无自动重试，冻结源码与连续账本核对通过。证据：`artifacts/shopguide/coverage-diagnostics/live-validation/`。
