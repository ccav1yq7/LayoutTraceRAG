# M3 验证报告

日期：2026-09-09。结论：现有工程链路通过复验；真实模型与完整官方评测门禁尚未通过。
本轮重跑验证，没有修改业务实现或将 fake 输出计作真实效果。

| 验证项 | 实际结果 |
|---|---|
| 全套 pytest | 111 passed，44.11 秒；14 条原有 LanceDB 弃用警告 |
| Ruff | 通过 |
| Mypy | 38 个模块文件通过 |
| 区域 scorer parity | 8 组与锁定官方函数一致 |
| uv.lock | 检查通过；解析 151 个包 |
| sdist / wheel | 构建、重装通过 |
| 独立安装 B1 CLI | python -I 调用安装包成功；2 次 fake 网关调用、0 个图像输入 |
| 独立安装 B2 CLI | python -I 调用安装包成功；3 次 fake 网关调用、8 个图像输入 |
| 原图复核 | 2 个展示资产重新授权读取，源链与字节 SHA256 校验通过 |
| 凭据检查 | wheel、sdist 和本轮验证产物不包含本地实际 API key |
| gpt-5.6-terra Planner 探测 | HTTP 503，service_temporarily_unavailable |
| gpt-5.6-terra Vision 探测 | HTTP 503，service_temporarily_unavailable |

两个成功的问答样例使用现有真实手册页面和明确的 fake 网关，semantic 均为
not_checked。这证明打包后的工程路径可运行，不证明真实模型问答、读图或
独立语义核验效果。

本轮真实探测最多 2 请求，每次 300 输出 token 上限、45 秒超时、无自动重试。
两次均未返回模型内容或 usage。可供管理员追踪的请求 ID：

- Planner：`45e1997b-d53b-4c4e-ab08-e5d0e4fab903`
- Vision：`681aab7f-9b70-46c7-ac46-3be21c5f6cc8`

环境预检：本次 `.venv-shopguide` 未安装 nlgeval 和 sentence_transformers；
在检查的项目及默认 Hugging Face 缓存目录未发现 BGE 缓存条目。这不是对
整台机器其他存储位置的穷尽搜索，也不能宣称真实 BGE 已部署并验证。
因此本轮没有运行 50 题真实 val、B1/B2 模型效果基线或完整 NLGEval 文字评分。
后续需准备真实检索/评分环境，并在模型服务可调用后完成这些门禁。

原始记录目录：`artifacts/shopguide/verification/`。
包括 tests.xml/tests.txt、region-parity.json、build/install/CLI 日志、B1.json、
B2.json、model-smoke.json、prerequisites.json 和 summary.json。

外部及依赖问题继续单独跟踪于 [问题登记](issues/external_blockers.md)。
