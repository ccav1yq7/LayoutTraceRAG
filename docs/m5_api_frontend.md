# M5 API 与交互前端交付

2026-09-09：本地交互工程已实现。FastAPI 复用 M4 的 SQLite 会话、LangGraph
checkpoint 和受限工具；React/Vite 提供商品栏、步骤卡片、原图来源对话框、
步骤追问、私有补图、商品切换、取消、刷新恢复、反馈和人工确认模拟工单。
本交付不等于 PLAN 所有产品场景、正式模型效果和生产部署均已验收。

## 启动

在仓库根目录执行；私有配置由服务端读取，不复制进前端或命令行参数。

```bash
UV_PROJECT_ENVIRONMENT=.venv-shopguide uv sync --locked \
  --extra dev --extra storage --extra shopguide --extra shopguide-dev
npm --prefix web ci
npm --prefix web run build
.venv-shopguide/bin/shopguide serve --root .shopguide/web-live-demo \
  --web-dist web/dist --demo --test-retrieval --private-config LLM.config \
  --seed-demo --port 8484
```

打开 http://127.0.0.1:8484；远程 IDE 需转发该端口。当前模型使用
`deepseek-v4-flash-vision-exp`。`--test-retrieval` 明确使用 hash/RRF；完全离线
演示可将这两个模型相关参数换成 `--fake`，界面会显示测试模式。
示例商品 TEST-DS、TEST-DS2、TEST-LITE 和说明书面板均为本项目自制 fixtures，
不是实际品牌说明书或真实购买记录。上传图片会作为当前会话输入发送给模型。

CLI 只绑定 loopback，当前只开放本地 `--demo` 认证。应用工厂支持服务端签发
的 bearer token，但公网用户登录、部署和运行指标留在 M7；不能直接把本地
演示身份当作公网认证。Python wheel 提供 API；前端需从源码执行上述构建，
以 `--web-dist` 指定输出目录。sdist 包含前端源码与 lockfile，不含 node_modules。

## 接口与数据边界

接口契约见 [OpenAPI](shopguide-openapi.json) 和
[领域契约](shopguide-contracts.json)。新增迁移 sg0004 保存认证 token 的哈希、
上传元数据与反馈；启动会恢复既有排队任务，不能恢复的任务明确失败。

- Cookie 为 HttpOnly、SameSite=Strict；写请求校验 CSRF 和 Origin，所有会话、
  run、图片、来源、反馈和模拟操作按服务端身份授权。忽略客户端伪造用户头。
- SSE 只投影安全进度；事件 ID 支持重连回放，不启动第二次任务。不暴露内部
  prompt 或推理内容。消息幂等与 revision 冲突沿用 M4。
- PNG/JPEG/WebP 限制 20 MiB、2500 万像素，验证实际格式、解码再编码并去除
  EXIF。上传绑定用户/会话/任务，24 小时到期；不能进入说明书检索索引。
- 用户照片供视觉工具、Writer、Verifier 观察；明确列出官方配图白名单和
  必须核验的步骤/图片配对。上传 ID 被用作引用或展示资产时仍严格拒绝。
- 每次答案交付重新检查来源与上传权限、撤销和文件完整性。失效来源不继续
  展示旧步骤。图片由受控接口读取，模型输出不作为任意 URL 或 HTML 渲染。
- 模拟售后必须确认；只写入本地 SQLite，不连接任何真实售后或交易系统。
- readiness 不持续付费探测；模型调用失败后短暂返回 503，另有独立存活检查。

## 验证结果

| 检查 | 结果 |
|---|---|
| Python 全量回归 | 151 passed（含 M5 API 8 项、新增补图边界 3 项） |
| Ruff / Mypy | 通过，60 个模块文件 |
| 前端类型检查与生产构建 | 通过 |
| Vitest | 2 passed |
| Chromium / Playwright | 7 passed；桌面、手机、200% 字号、来源缩放、追问、上传、切换、确认、取消、刷新、反馈、文本转义 |
| 真实 DeepSeek 首轮 | 完成，语义 supported；7 次模型调用、3 次图片输入、14808 tokens |
| 真实 DeepSeek 补图修正后 | 完成，语义 supported；3 个步骤，7 次模型调用、7 次图片输入、21057 tokens |

真实调用的检索仍是测试替身，因此结果的整体 `model_mode` 保守标为 `fake`；
`model_id`、真实用量和 supported 核验记录不表示使用了假的 DeepSeek 回复，
也不代表正式 benchmark 成绩。样本只是自制面板，不能推断真实商品泛化能力。

失败样例没有删除：首次中文问题只得到无图 partial；首次补图中模型把上传
ID 放进官方配图字段，并在核验中增加多余图片配对，服务端正确拒绝。
通过明确输入类别和白名单修复提示，同时增加负向测试，没有放松校验。
浏览器“重试问题”现会保留原附件与步骤关联。

原始本地产物在 `artifacts/shopguide/m5/`：`all-tests.txt/xml`、`lint.txt`、
`live-browser-first.json`、`live-browser-photo.json`（失败）、
`live-browser-photo-v2.json`（修正后）、桌面/手机/来源截图。
脚本调试期间还修正了测试图片 CRC 和浏览器请求上下文的 URL/登录等待问题；
它们不是模型服务故障。CI 已加入独立浏览器任务，本轮仅本地执行，未声称远端 CI 通过。

## 验收范围与剩余门槛

E-02/03/05/09/11/12 的主要交互路径已覆盖；E-01 澄清与型号隔离已有工程测试。
E-06/07 的缺图/拒答、E-08 冲突、E-10 checkpoint 恢复分别有 M3/M4 后端回归，
本轮新增浏览器运行中刷新恢复。它们仍需完整 E-01 至 E-12 场景联验，尤其
E-04 不同版本实拍冲突和 E-10 浏览器连接时进程崩溃恢复，尚未作为独立
浏览器故障注入用例验收，不能把刷新测试写成进程崩溃测试。
真实 BGE、PM209 全量评分、ECom 原生环境、生产认证与性能目标仍待完成。
外部依赖保持在 [独立问题登记](issues/external_blockers.md)，下一工程模块为 M6。
