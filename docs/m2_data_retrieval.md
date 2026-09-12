# M2 数据与检索闭环

2026-09-10 收尾更新：完整评分参考环境与真实 BGE 权重/局部验证现已补齐；
当前运行条件、适配差异和剩余基线门槛见 [M0–M3 收尾记录](m03_completion.md)。
以下原始记录保留其当时语境，不代表当前评分/BGE 仍未接入。

2026-09-09：本轮完成数据准备、图片/PDF 入库、快照发布和带 scope 的真实
LanceDB 检索工程链路。没有调用故障 LLM 服务，也没有生成模型效果分数。

已实现的入口：

```bash
# 独立环境；新增 PDF 依赖及其锁定版本已进入 uv.lock
UV_PROJECT_ENVIRONMENT=.venv-shopguide uv sync --locked \
  --extra dev --extra storage --extra shopguide --extra shopguide-dev

# 一次性完整转换；已存在的目标目录不会被覆盖
.venv-shopguide/bin/shopguide prepare-pm209 \
  --archive data/pm209/raw/PM209.zip --out data/pm209/prepared-v1

# 示例为 3 页工程 smoke。新建 snapshot，不覆盖活动代次
.venv-shopguide/bin/shopguide ingest-corpus \
  --corpus data/pm209/prepared-v1/corpus --root .shopguide/m2-pm209 \
  --snapshot snapshot_pm209_example --principal user_research --max-pages 3 --fake

# 从 ingest 输出取得 product/variant。主体来自可信调用者，非模型参数
.venv-shopguide/bin/shopguide search \
  --root .shopguide/m2-pm209 --snapshot snapshot_pm209_example \
  --principal user_research --product <product_id> --variant <variant_id> \
  --query 'source document terms' --k 3 --fake

# 授权的本地 PDF：必须明确商品身份与适用依据
.venv-shopguide/bin/shopguide ingest-pdf --file <authorized-manual.pdf> \
  --root .shopguide/demo --snapshot snapshot_pdf_example --principal user_demo \
  --product product_example --variant variant_example --brand <brand> \
  --model <exact-model> --basis <reviewed-applicability-basis> --fake
```

`--fake` 明确选择测试 hash 向量和只保留 RRF 分数的排序器；它不是语义检索
性能基线。真实 BGE 适配器要求模型路径/ID 和固定 revision，默认只读本地
权重，不自动下载、不静默切换 fake。真实查询还必须配置固定 revision 的
BGE reranker；它依赖已有 `retrieval` extra。本轮没有加载 BGE 权重或测量
语义检索/重排效果。

实现边界与保证：

- PM209：白名单 `CorpusPage/CorpusRegion`，额外字段被拒绝；问题、答案、
  原始官方 ID 和顺序映射仅写入 `eval_private`。runtime corpus 含源页面、
  OCR、区域坐标和不透明 ID。保留各 split 内官方问题/页面/区域顺序；运行
  时证据的 source_reference 可连接到私有评分映射，不读取 gold 内容。
- 图片：原始字节、独立源 hash、派生 hash、父图、原始坐标、转换矩阵及
  正面积 bbox。裁剪和缩放核对像素；读取时验证授权、文档撤销、源文件和
  父图完整性。同字节可复用存储，但不同用户/商品的资产授权记录独立。
- PDF：pypdfium2 渲染，pdfplumber 提取可见 CropBox 内文字；保留 MediaBox、
  CropBox、旋转和源 PDF 到渲染像素的矩阵。测试覆盖 90° 旋转及非零偏移，
  CropBox 外隐藏文字不会进入证据。当前只做整页文字区域，无文字层时明确
  无提取文本；尚未集成 OCR 或复杂图表/图注自动检测。
- 关系：保存 belongs_to_page 和按版面推断的 nearby_text，后者明确标记
  inferred，不声称已理解或确认语义图注。
- 快照：STAGING → AUDITING → READY → ACTIVE；失败为 FAILED，活动指针不变。
  旧 ACTIVE 转为 READY，旧 run 仍可按固定 snapshot 读取。发布使用 SQLite
  原子比较并切换；READY 可经 `ScopedIndex.publish(expected_active=...)`
  恢复发布或回滚。失败/审计中断后同身份重新入库，确定性 ID 避免重复。
  CLI 入库使用文件锁限制单写入者；数据库和服务部署仍是单机边界。
- 检索：每 snapshot 独立 `evidence_v2` Lance 表；固化模型身份、revision、
  维数、预处理、schema 及 scope 列。dense 与原生 FTS 均显式 prefilter，
  再 top-k；融合后调用明确的 reranker，最后重新检查数据库和资产权限。
  无共享私有结果缓存；撤销资料不会因旧索引命中而泄露。
- 重新打开时核对完整行摘要、计数、向量有效性、模型身份和真实 FTS 索引。
  当前 dense 为精确扫描，health 也是全量校验；没有声称已完成大规模性能优化。

本轮实际运行结果：

| 验证 | 结果 |
|---|---|
| PM209 完整转换 | 10,018 页、211,609 区域；22,021 QA 全部留在私有映射 |
| 真实页面入库 | 3 页，60 个 evidence/asset 记录；完整审计后 ACTIVE |
| 真实页面重开查询 | 两路召回均运行，返回原图字节与来源引用；model_mode=fake |
| PDF 测试 | 带旋转/CropBox 的自制 PDF；源 hash、页面尺寸、可见文字通过 |
| M2 自动化 | 18 项测试，覆盖 I-01–06 及 scope/撤销/恢复/隔离/来源破坏 |
| 全套回归 | 87 passed；14 条原有 LanceDB 弃用警告 |
| Ruff / Mypy | 通过；Mypy 检查 25 个新模块文件 |
| 构建与独立安装 | sdist/wheel 成功；安装后 sg0002 迁移和 PDF 模块可用 |

完整 corpus 位于 `data/pm209/prepared-v1`，真实页面 smoke 的活动代次为
`snapshot_pm209_verified`。这些仅是本地研究/工程产物，未公开发布第三方图像。
原始日志和脱敏 smoke 摘要见 `artifacts/shopguide/m2-*`，均不提交。

M2 已建立文件与接口层面的 source/gold 分离；预测进程的容器挂载/操作系统
隔离仍需在后续部署阶段落实。本轮未实现浏览器、Writer、语义核验或正式
benchmark inference。外部服务、资源与许可问题统一见
[外部问题登记](issues/external_blockers.md)，不再作为 M2 工程开发的暂停理由。
