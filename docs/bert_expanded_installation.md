# 扩充意图数据微调与本地实装

已按用户“开始微调然后实装”执行，真实客服入口默认选择新版开发权重。实装指本地代码和模型配置已经接通并验证 Agent 自动加载，不表示公网部署、长期进程已启动或生产质量已验收。

## 数据

独立新版 `configs/intent/expanded-v2/dataset.jsonl` 共 342 条：train 198、val 72、test 72。216 条单诉求、90 条双诉求、36 条无当前业务诉求。基础是 126 个自编场景族及其改写，组合样例只使用所属分区的来源族；不把组合数量当作独立真实会话量。

旧 90 条 bootstrap 数据和旧权重原样保留，不混入新版分区。新版与旧版没有规范化精确重复；来源族无跨分区，0.84 字符相似度审计未发现跨分区近重复。该启发式不能证明语义独立，数据全部为自编/组合开发样例，尚无真实客服独立测试集。人工诉求用于识别标签，不表示人工功能已接通。

可复现构建：`.venv-shopguide/bin/python scripts/shopguide/build_intent_expansion.py`。基础表达在 families.json，分组、统计、重复审计和 hash 在 data-card.json。

## 微调

沿用锁定中文 BERT revision `8f23c25b06e129b6c986331a13d8d025a92cf0ea`，多标签 sigmoid/BCE、8 epochs、lr=3e-5、batch=12、max_length=96、seed=20260910。在验证集上选择 checkpoint 和阈值，再只评估一次开发 test。

选中 epoch 6、阈值 0.7。验证集 Macro-F1 0.9518、整组匹配 0.8889；72 条自编 test 的 Macro-F1 0.9159、整组匹配 0.8194。不能与旧 15 条 test 的分数直接比较提升/退步，也不能将此结果称为真实客服准确率。

训练与权重：`artifacts/shopguide/bert-intent-expanded-v2/`，包含 protocol、history、selection、test、model-card 和 checkpoint。训练脚本、数据和完整 checkpoint 的 hash 已核对。

## 本地实装

`configs/intent/active-classifier.json` 已设置 enabled=true，指向上述模型；model_card_sha256 锁定身份，allow_development=true 明确对应用户授权启用开发版，并没有把模型卡改为 production_approved。

真实 Agent 在意图候选层启用且没有显式指定其他 classifier 时自动读取该配置。`SHOPGUIDE_INTENT_CLASSIFIER_CONFIG` 可指定其他配置；显式指定的配置缺失、模型卡 hash 不符或权重缺失均报错，不回退到未训练/假分类器。fake 演示默认不自动加载该本地模型；固定 B1/B2 对照也不擅自增加分支。

实例缓存最多保留两份已校验模型，以模型卡 hash、目录、标签/定义和设备为键；当前安装使用 CPU 推理，训练使用 CUDA。更新模型需要更新不可变权重目录及注册 hash；运行中的已加载实例不热改权重。已运行旧版代码的进程需正常重启，新启动的真实 Agent 会读取注册配置。本轮检查常用本地服务端口未发现现有监听，未启动常驻服务。

BERT Top3 加入 RRF，仍保留其他信号、全目录保护和 LLM Judge。验证阈值用于记录分类评测，不以它丢弃融合候选或授予业务授权。对“查工单并问连接方法”，即使第二意图分数低于阈值，也可作为候选交给后续判断。

已实际测试权重重载、三个输入的本地推理、候选融合，以及使用用户真实 gateway 配置构造 Agent 后自动使用同一模型。其余检索用明确 fake fixture；未执行整条远端 LLM 链路，未消耗新 API 请求。证据：install-test.json。

停用本地分支可将 active-classifier.json 的 enabled 改为 false；旧 90 样例模型保留，可通过显式路径作历史实验，不自动切换。

## 验证与限制

训练数据/候选/意图及安装边界回归见本目录 tests.xml；包含配置缺失、hash 变化与显式停用。Ruff/Mypy 通过。后续仍需真实客服数据、独立对话分组评测、校准及完整协作验收；业务授权、暂停撤回和人工暂停约束保持不变。
