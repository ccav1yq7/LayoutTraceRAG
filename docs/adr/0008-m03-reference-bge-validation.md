# ADR 0008：M0–M3 评分参考环境、真实检索与验证预算

日期：2026-09-10。范围仅为原 M0–M3 收尾，不增加新产品模块。

## 验收条件

1. `pm209-score` 提供实际的完整文字评分入口，固定来源和资源，任何参考失败不得输出 COMPLETE。
2. 三 profile 分开，fake 不进正式评分，缺失/失败保留分母，结果与实际 run 和冻结配置关联。
3. 真实 BGE 权重与 revision 固定；真实向量建库、重开、scope 和原图检查通过。
4. 用预先冻结的 val 问题集验证检索；真实 B1/B2 调用先准备具体预算，得到追加授权后运行。

## 选择

- 独立 Python 3.11 参考环境，`configs/pm209/requirements.lock` 固定依赖。
  NLGEval 固定到 `2ab4528fad5548315cf61e40c2249fec8c8ad233`，以 --no-deps
  安装源码。禁用的 SkipThought/GloVe 不会导入旧 gensim/Theano，因此不安装它们。
  与官方一样设 `no_skipthoughts=True, no_glove=True`，不省略 overlap 指标。
- 使用原 MPMQA `evaluate.py` 的标点处理、区域函数与 `scripts/compute_metrics.py`
  的文字函数及区域分组函数，执行前校验完整文件 SHA；不导入训练 loader/模型。
  全文字指标为 Bleu_1–4、METEOR、ROUGE_L、CIDEr、SPICE。
- Stanford CoreNLP 3.6.0 资源与 Temurin Java 8 仅供离线参考评分，并按字节锁定。
  Java 21 缺少旧 SPICE 使用的 Nashorn；不更改系统 Java，不删 SPICE。
- gold 使用 pinned `google-t5/t5-base` Fast legacy tokenizer 和官方语义 special tokens，
  对齐 loader 的 `<pad>+answer` 编码、去 EOS、skip_special_tokens 解码。
  原发布 pretrained.zip 未复现，HF tokenizer 与新 Transformers 属于已登记兼容适配；
  不把该实现宣称为原 URA 模型/旧训练环境的逐字节复现。
- 空类别记 `question_count=0, text=null`；无候选类型的区域指标也为 null，避免上游
  空输入崩溃/NaN 被包装为数值。整体空预测仍逐题评分，不能删除失败样本。
- QA→page 聚合保留 `utils.merge_recall` 的 page_nums 权重；问题微平均与手册宏平均
  仅为诊断。当前不提供反向 page→QA，相应完整双向 r_mean 不伪造。
- BGE-M3 revision `5617a9f61b028005a4858fdac845db406aefb181`；reranker revision
  `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`。沿用 SentenceTransformer/CrossEncoder。
  两者最大长度固定为 1024、batch=4、float32；embedding 仍为无指令前缀的归一化 dense。
  原 embedding 未限制为 1024，因此 preprocessing 身份升级为
  `normalize-no-prefix-max1024-v2`，必须重建独立索引，不能复用旧 identity。
- 真实设备为 RTX 3090 24GB；新检索环境由现有 uv.lock + retrieval/shopguide extras
  创建，不更换原 `.venv-shopguide`。授权检查的 N+1 文档查询改为一次联表查询；
  每次读取仍重新检查当前授权及撤销，不引入跨请求权限缓存。
- 50 题选择规则先冻结为官方 val 顺序前 5 本手册各前 10 题，所有相关手册页面都入库。
  这是开发验证子集，不代表全部 21 本 val 手册或 test。不给模型提供答案/相关区域。
- pilot 使用上述清单第 0、10 个问题（两本手册）×3 profile×B1/B2，最多 30 请求；
  每请求最多 2048 输出 tokens，无自动重试。全 50 题对应上限 750 请求。
  请求发出前持久化预留，失败也计数，旧目录不能重置额度；配置/源码/请求清单变动
  使冻结运行失效。既有 20 次额度不复用。本轮用户已批准 pilot，且明确后续 50 次以下小验证无需重复询问；
  大实验不拆批规避。pilot 25 次加定位 5 次，共 30 请求，完整 750 请求方案未批准。

## 结果边界

`official_metric_implementation=true` 表示执行了锁定官方指标代码；
`report_kind=real_baseline_validation` 与 `official_benchmark=false` 表示本次是已登记
验证协议，不是完整 test 成绩或最终发布验收。DeepSeek 的不可变服务版本和本实验模型
费率尚不明确，保留 null 与版本限制，不填虚构版本、零费用或提升百分比。
