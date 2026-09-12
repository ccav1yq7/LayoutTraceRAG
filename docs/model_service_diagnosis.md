# 模型服务 502/503 诊断

日期：2026-09-09。模型固定为用户指定的 `gpt-5.6-terra`。

结论：当前证据定位到所配置模型转发服务的上游访问失败，不支持将故障
归因于 ShopGuide 请求体、路径或本地代理。服务明确返回：

```json
{"error":{"message":"Upstream access forbidden, please contact administrator","type":"upstream_error"}}
```

这一错误以 HTTP 502 返回；随后请求得到 HTTP 503，错误为
`Service temporarily unavailable` / `api_error`。二者可能是同一上游故障
及后续不可用状态，但是否存在熔断、渠道禁用或限流，客户端无法确认。

| 对照 | 结果 |
|---|---|
| 环境代理，GET /v1/models | 200；模型列表包含 gpt-5.6-terra |
| 禁用 Python 代理，GET /v1/models | 200；同样包含目标模型 |
| 环境代理，POST /responses，最小纯文本 | 502；upstream_access_forbidden |
| 禁用 Python 代理，同一最小请求 | 502；相同上游拒绝信息 |
| 禁用 Python 代理，POST /v1/responses | 503；service temporarily unavailable |
| 禁用 Python 代理，POST /responses，stream=true | 503；service temporarily unavailable |
| 同一路径，不发送 Authorization | 401；API_KEY_REQUIRED |

最小请求不含图片、JSON Schema、工具或任何业务数据；第二组还去除了
max_output_tokens。失败并不依赖这些参数。模型枚举成功只能证明网关列出
该模型，不能证明实际推理渠道可用。不带认证的控制请求与带认证的错误
阶段不同，结合 upstream_error 强烈指向网关转发之后的失败，但不应据此
宣称已独立验证上游账号权限、余额或所有租户路由配置。

供服务管理员查日志的请求 ID（无需传递 API key）：

- 环境代理、上游拒绝：`9d6a8863-ab7a-4666-99f9-d52d9c7d7616`
- 直连、上游拒绝：`db3c1a7f-e4f3-430e-96aa-00677113896c`
- /v1/responses 503：`70cd5da8-d085-4cf2-8cdc-d2b7cf32f7fc`
- /responses 流式 503：`d410f356-1f56-413d-9b80-85dfd5544167`

管理员应核对这些请求命中的渠道及上游实际响应，检查该渠道的认证凭据、
账号/模型访问权限和路由状态。现有证据不能进一步区分凭据过期、账号限制、
模型渠道配置错误或其他上游访问策略；未访问或更改服务器配置。

本次共 7 次 HTTP 诊断请求，其中 4 次带认证的最小推理请求，无成功模型
输出。原始脱敏证据保存在 `artifacts/shopguide/diagnostics/transport.json`
和 `request-variants.json`。报告不保存凭据或端点地址。

客户端确有一处诊断不足：此前 smoke 只记录 502/503，丢弃服务端错误语义。
已修正 `scripts/shopguide/model_smoke.py`，只保留白名单错误分类、类型和合法
UUID 请求 ID，不保存任意错误正文。相关 6 项离线测试通过。这改善故障
定位，不代表上游服务已恢复，也不改变模型选择。
