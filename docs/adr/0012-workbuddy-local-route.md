# ADR 0012：用户指定的本地 Workbuddy DeepSeek 路由

2026-09-10。用户提供本地配置、确认多模态能力，并批准本轮累计 870 次请求完成重跑。
本项目通过 `# workbuddy deepseek` 独立配置段选择路由，不混用旧官方段的模型/密钥。

## 已验证与适配

自制图片中的随机代码与两种彩色形状被正确识别，合法 JSON 返回；这是路由实际读图
证据，不是依据模型名字推测能力，也不证明代理底层一定使用某个不可变模型版本。

最初 Responses 项目预检返回空最终文本，输出额度用满。受控对照中，Responses 路径
仅返回 reasoning 类型输出；Chat Completions 原生 `thinking.type=disabled` 返回有效 Writer
结构且报告 reasoning_tokens=0。依据 [DeepSeek 官方思考模式说明](https://api-docs.deepseek.com/guides/thinking_mode/)
选择 Chat 协议。类似转发问题有 [CLIProxyAPI 问题记录](https://github.com/router-for-me/CLIProxyAPI/issues/4933)，
但本轮未审计本地代理源码/内部转发，不能把外部报告当成本机根因的证明。

- 仅明确的 loopback HTTP 地址允许此路由；原 ResponsesGateway 默认继续拒绝 HTTP。
  本地凭据不发送到其他主机，禁用环境代理与自动重定向，失败不回退到官方服务。
- 请求使用 Chat Completions、原生 thinking disabled、temperature=0、max_tokens=2048。
  不发送互相冲突的 reasoning/effort 字段。系统指令携带实际输出 schema，要求纯 JSON。
- 使用 json_object 传输和现有本地严格 schema 校验；图片转换为 Chat 的 image_url 输入，
  不改变字节、来源或授权。不删除 Markdown 围栏来掩盖非法模型输出。
- 只有单个 choice、finish_reason=stop 且存在最终文本才可解析；仅推理或 length 截断
  不能伪装成 completed。忽略内部推理内容，不保存或展示它。
- prompt/completion token 用量映射为现有 input/output 字段；缺失/非法用量保持 unknown，
  不伪造零费用。代理 credit 字段不擅自解释为货币金额。
- route/provider/model 与 transport_profile 全部进入配置指纹：workbuddy-deepseek /
  deepseek-v4-flash / loopback-chat-schema-instructions-v1。

## 验证和预算

真实 B1 与 B2 项目预检均完成 Writer/Verifier，B2 同时完成选图。之后才冻结新全量清单。
此前中止批次与定位 120 次、新路由能力/协议/项目预检 13 次，共 133 次，全部计入 870。
旧的独立“连通性测试”任务不属于本轮问答重跑账本。新批次另有局部 750 上限，并在
每次 HTTP 调用前检查累计 870 上限；达到累计上限会中断，不伪造剩余预测。
代理内部是否额外重试/转发不可独立观测，预算按本项目发起的模型 HTTP 请求计数。

问题、数据、索引和每题预算保持不变，但新旧实验同时改变应用修复、提示和模型路由，
结果不能用于隔离证明某一项修复的因果收益。服务入口、模型名和不可变版本未知均须披露。
