# M0–M3 收尾记录

后续更新：获批的完整 50 题三协议 B1/B2 运行已完成，300 条预测/六组评分，使用 595 次请求；
最新结果见 [50 题基线记录](m03_val50_baseline.md)。B2 可靠性未验收；下文保留前一轮完成记录。

2026-09-10。此轮补正式评分接入、真实 BGE 与基线验证，不扩展 M4–M7 功能。
验收条件与兼容适配见 [ADR 0008](adr/0008-m03-reference-bge-validation.md)。

## 已实现与局部实测

- `pm209-score` 已能在独立进程调用完整 NLGEval 文字评分和官方区域函数。
  校验源码、资源、Java、tokenizer 和依赖版本；600 秒超时清理整个进程组。
  fake/配置变化/修改预测文字与引用/缺失 run 证据会拒绝正式入口；参考执行失败不返回部分 COMPLETE。
- 全文字评分资源已安装并实际运行。合成测试覆盖完美预测、全空预测、标点与大小写、
  六区域类型和空类型；全部八个文字指标保留。区域与独立实现容差 1e-12。
  这些是评分器验证，不能作为产品效果分数。
- BGE 权重已下载、固定 revision 并成功执行；真实语义向量、重排、双路 scope、
  仓库重开、来源字节检查和不同 revision 拒绝测试通过。测试使用自制资料，没有模型网关调用。
- 发现授权读取按文档逐条查询，影响大手册导入；改为联表查询并保留实时撤销检查。
  20 文档用例要求查询数有界，同时检验旧 scope 在撤销后立即失效。
- 50 题及三 profile 的问题白名单已准备，按 val 顺序前 5 本手册各 10 题选择。
  每本手册全部页面进入候选，不能只导入 gold 页。真实 B1/B2 pilot 已在本轮获批运行；完整 50 题问答仍待单独预算。

本地记录目录：`artifacts/shopguide/m03-completion/`。资源、数据和私有答案不发布到仓库。

## 当前实测结果

真实 BGE 对 5 本手册的全部 **522 页、11,091 条证据**完成建库/审核/激活、重开与
50 题检索；50/50 有结果，未发现被测候选越 scope。建库（含图块/资产审计）约
1,073.6 秒，50 次查询约 529.3 秒。索引 identity/row hash 在 `retrieval-v2/summary.json`。

| 检索指标 | R@1 | R@3 | R@5 |
|---|---:|---:|---:|
| 官方兼容的手册页数加权 QA→page | 0.1929 | 0.2280 | 0.2939 |
| 问题微平均诊断 | 0.6600 | 0.7000 | 0.7400 |

不是两套不同预测。五本手册分别有 10、172、11、317、12 页，各取 10 题，top1
命中分别为 10、4、9、0、10；大手册表现较差，在官方页数加权下占比更高。
不能只展示 0.66 作为“官方 Recall@1”。单进程查询 p50 10.463 秒、p95 10.692 秒，
包含每个检索通道的完整索引健康检查；不满足 PLAN 拟议热检索 1.5 秒目标，
也不是 5 并发性能验收。

真实问答 pilot：2 道开发题 ×3 profile×B1/B2，共 12 次问答，**25 次请求**，
报告输入 184,348、输出 13,954 tokens。全部六组完整文字/区域指标已计算；
失败都保留在两题分母，样本量不能用于宣称方法优劣。

| Profile | B1 状态 | B2 状态 | B1 / B2 ROUGE-L | B1 / B2 区域 instance F1 |
|---|---|---|---|---|
| given-page | 1 answered、1 partial | 1 answered、1 error | 0.3590 / 0.2738 | 0.3429 / 0.2500 |
| retrieved-top1 | 1 answered、1 partial | 2 error | 0.3552 / 0 | 0.3538 / 0 |
| multipage（扩展） | 2 answered | 2 error | 0.3959 / 0 | 0.4266 / 0 |

全量八个文字指标、六类区域与失败 ID 见 `pilot/*.metrics.json`；精简总表在
`pilot-summary.json`。B2 五次失败都在写作请求完成后、语义核验前被本地校验拒绝，
原记录的通用错误码不足以确定具体字段，不能擅自归因于某个引用规则。

补充定位单独保存、不混入上述成绩：首次 2 请求后遭遇 HTTP 分块响应中断；
它不属于最初五个校验失败的已证实根因。增加 `IncompleteRead/BadStatusLine` 离线
失败回归后，网关统一捕获 HTTPException 为有界错误，无自动重试、不回传原响应碎片。
随后同题 3 请求诊断成功，未稳定复现原失败；B2 稳定性仍未验收，未修改提示或放宽原图校验。

本轮合计 **30 次模型请求 = 25 pilot + 2 中断定位 + 3 后续定位**。
已知用量为输入 210,716、输出 15,979 tokens；中断定位的两请求缺少完整用量记录，
因此总 tokens 与货币成本不能填 0 或当成已精确计量。详见 `request-accounting.json`。

用户本轮明确授权“**批准，五十次以下的都不用问我**”；该偏好适用于后续小规模验证，
不把完整大实验拆批来绕过整体预算。原 30 请求 pilot proposal 保持不可变，批准记录
在 `pilot/approval.json`，执行记录在 `pilot/started.json`。


## 复现参考环境

先按 lock 中 SHA 获取 MPMQA 与 NLGEval checkout；原仓库地址为
[MPMQA](https://github.com/AIM3-RUC/MPMQA) 和 [NLGEval](https://github.com/Maluuba/nlg-eval)。
所有下列命令在仓库根目录执行；下载归档必须与 `reference.lock.json` 的 hash 一致。

```bash
uv venv .venv-pm209-reference --python 3.11
uv pip sync --python .venv-pm209-reference/bin/python configs/pm209/requirements.lock
# NLGEval checkout 必须先固定到 lock 的 nlgeval_revision。
uv pip install --python .venv-pm209-reference/bin/python --no-deps ./external/nlg-eval
.venv-pm209-reference/bin/python scripts/shopguide/prepare_pm209_reference.py \
  --lock configs/pm209/reference.lock.json --source external/MPMQA \
  --nlgeval-source external/nlg-eval \
  --stanford-archive external/stanford-corenlp-full-2015-12-09.zip \
  --java-archive external/pm209-jre8.tar.gz --java-home external/pm209-jre8
.venv-shopguide/bin/python scripts/shopguide/pm209_reference_parity.py \
  --python .venv-pm209-reference/bin/python --source external/MPMQA \
  --lock configs/pm209/reference.lock.json --java-home external/pm209-jre8 \
  --out artifacts/shopguide/new-reference-validation
```

资源来源和 SHA 在 lock 内；不通过省略 SPICE、改用 LLM judge 或放松预处理来解决环境错误。
T5 tokenizer 来源差异、空类别处理和 Java 8 限制见 ADR。

## 复现真实检索

[BGE-M3 官方用法](https://huggingface.co/BAAI/bge-m3) 与
[BGE reranker 官方模型卡](https://huggingface.co/BAAI/bge-reranker-v2-m3) 为实现依据。
缓存须预置 `configs/pm209/bge.lock.json` 指定的 revision/文件；加载默认 local_files_only。
`prepare_bge.py --download` 可获取锁定权重并逐文件核验，省略 --download 则只验证本地缓存。

```bash
UV_PROJECT_ENVIRONMENT=.venv-shopguide-bge uv sync --locked \
  --extra dev --extra storage --extra shopguide --extra shopguide-dev --extra retrieval
.venv-shopguide-bge/bin/python scripts/shopguide/prepare_bge.py --download \
  --out artifacts/shopguide/new-bge-files.json
.venv-shopguide-bge/bin/python scripts/shopguide/bge_smoke.py \
  --models configs/pm209/bge.lock.json --device cuda:1 \
  --out artifacts/shopguide/new-bge-scope
.venv-shopguide-bge/bin/python scripts/shopguide/pm209_baseline.py prepare \
  --prepared data/pm209/prepared-v1 --models configs/pm209/bge.lock.json \
  --out artifacts/shopguide/new-val50-schedule
.venv-shopguide-bge/bin/python scripts/shopguide/pm209_baseline.py retrieval \
  --schedule artifacts/shopguide/new-val50-schedule \
  --corpus data/pm209/prepared-v1/corpus --root .shopguide/new-bge-val50 \
  --out artifacts/shopguide/new-bge-retrieval --device cuda:0
.venv-shopguide/bin/python scripts/shopguide/pm209_baseline.py score-retrieval \
  --schedule artifacts/shopguide/new-val50-schedule --prepared data/pm209/prepared-v1 \
  --predictions artifacts/shopguide/new-bge-retrieval/retrieval.jsonl \
  --out artifacts/shopguide/new-bge-retrieval/metrics.json
```

数据 root 和输出目录必须是新的。此检索过程不用 LLM，生成的指标只描述页面检索，
没有文字回答正确率、区域回答成绩或 B2 对 B1 的效果提升。

## 真实问答验证的预算和正式评分

`campaign-prepare` 只读取本地模型配置并冻结请求/config/源码 SHA，不发送请求。
默认 pilot：两题（不同手册）×三 profile×B1/B2，最多 30 请求、61,440 输出 tokens；
完整 50 题上限为 750 请求。费用取决于实际输入、图像和服务费率，token/request 上限
不是货币上限。当前实验模型费率与不可变服务 revision 未取得，不推测费用。pilot 已获批并完成，
小规模验证的后续授权规则见上文；下面保留新建运行的可复现命令。

```bash
.venv-shopguide-bge/bin/python scripts/shopguide/pm209_baseline.py campaign-prepare \
  --schedule artifacts/shopguide/m03-completion/schedule \
  --root .shopguide/pm209-bge-val50-v2 --private-config LLM.config \
  --out artifacts/shopguide/new-pilot --device cuda:1
# 得到本轮预算授权后才执行；SHA 使用 prepare 的真实输出。
.venv-shopguide-bge/bin/python scripts/shopguide/pm209_baseline.py campaign-run \
  --schedule artifacts/shopguide/m03-completion/schedule \
  --root .shopguide/pm209-bge-val50-v2 --private-config LLM.config \
  --out artifacts/shopguide/new-pilot --device cuda:1 \
  --approved-max-requests 30 --campaign-sha256 <prepare输出的SHA>
```

每个 job 有自己的 requests、evaluation manifest、predictions 和 `.runs` 文件夹。
将 `<job>` 换成实际名称，例如 `pm209-retrieved-top1-B2`，分别评分：

```bash
.venv-shopguide/bin/shopguide pm209-score \
  --requests artifacts/shopguide/new-pilot/<job>.requests.jsonl \
  --predictions artifacts/shopguide/new-pilot/<job>.predictions.jsonl \
  --evaluation-manifest artifacts/shopguide/new-pilot/<job>.evaluation.json \
  --private data/pm209/prepared-v1/eval_private --corpus data/pm209/prepared-v1/corpus \
  --reference-python .venv-pm209-reference/bin/python --reference-source external/MPMQA \
  --reference-lock configs/pm209/reference.lock.json --reference-java-home external/pm209-jre8 \
  --out artifacts/shopguide/new-pilot/<job>.metrics.json
```

旧 `--engineering` 路径保留，不能与正式参考参数混用。完整正式评测与 M3 验收状态
取决于实际基线、错误案例审查与协议冻结，不由本页的实现说明自动关闭。

## 工程复验

HTTP 异常修复前 `.venv-shopguide/bin/python -m pytest tests -q`：**212 passed，15 warnings，
144.68 秒**，Ruff/Mypy 通过。wheel/sdist 构建及新增参考模块/lock 包含性核对通过；
`uv lock --check` 通过。没有重跑前端/浏览器，因为本轮未修改这些代码。
HTTP 异常修复后：相关回归 **74 passed**；最终全量 **214 passed、15 warnings、
147.06 秒**，Ruff/Mypy 再次通过。最终报告为 `final-tests.txt/xml`。

初次 safetensors-only 下载未包含 BGE-M3 的 pytorch_model.bin，加载报错；补齐锁定
权重后成功。Java 21 的 SPICE 先遇到反射访问限制，继续核查发现缺少 Nashorn，
改为隔离 Java 8 后所有文字指标通过。原日志保留；最新 lock 仅将 Java 下载入口
从 latest 改成确定版本链接，指标代码和资源字节未变，衔接证据为
`reference-lock-url-pin.json`。

首次 522 页导入在排查授权 N+1 时主动中断，原 root 保留 FAILED 快照；
重新在 `.shopguide/pm209-bge-val50-v2` 构建，不能把第一次的半成品视为 ACTIVE。

## 尚未完成与下一份可执行清单

完整 50 题问答（不是已经完成的 50 题检索）仍未执行；B2 失败稳定性、完整错误案例
审查、正式效果目标和完整 test 验收未关闭。M0 的设备/BGE 版本已有实测，DeepSeek
不可变服务版本、完整计费口径仍未取得。M0–M3 不能据此整体勾为验收通过。

已准备 `artifacts/shopguide/m03-completion/val50-proposal/campaign.json`：
50 题×3 profile×B1/B2，共 300 次问答、最多 **750 次请求**，每请求最多 2,048 输出 tokens，
无自动重试。源码和输入均冻结；该大实验尚未获批/启动，不属于“50 次以下”授权。
SHA-256：`d5fa00575f1b48a0bcc24dc76d27ff4d43e82daeaa8447cd0d88152e335f36b3`。
原 pilot 在 HTTPException 修复前完成，原始结果和源码哈希保留；新 proposal 使用修复后的代码。
