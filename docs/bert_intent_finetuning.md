# BERT 意图分类开发版微调

> 最新状态：342 条扩充数据已重新微调并按用户要求在本地真实客服启用，见 [新版训练与实装](bert_expanded_installation.md)。下文是保留的 90 条 bootstrap 阶段记录。

已实际完成中文 BERT 多标签微调，并导出可重新加载的 checkpoint。数据目前全部为自编开发样例，未发现现成的已标注真实客服数据；因此这是开发版模型，不是生产效果验收或真实中文客服基准。

## 模型与数据

底座为 [google-bert/bert-base-chinese 官方模型卡](https://huggingface.co/google-bert/bert-base-chinese)，固定 revision `8f23c25b06e129b6c986331a13d8d025a92cf0ea`，模型卡标注 Apache-2.0。采用新增六标签分类头、sigmoid 和 BCEWithLogitsLoss；微调整个 encoder 与分类头，不是仅加载预训练 BERT 就称作已训练分类器。

可复现开发数据位于 `configs/intent/bootstrap.jsonl`，数据卡为 `bootstrap-data-card.json`。共 90 条：train 60、val 15、test 15，含六类单诉求、少量多诉求、否定和空标签样例。划分在训练前固定；程序拒绝相同 group_id 或完全相同规范空白文本跨分区。仍可能存在语义近似表达，不是独立人工复核的真实对话测试集，不能以程序去重代替真实数据审计。

分类器只看当前句；暂停/撤回和多轮指代仍由最终 Judge/reducer 处理。空标签表示本句无当前办理/咨询诉求，不表示清空历史状态。

## 训练结果

训练脚本 `scripts/shopguide/train_intent_bert.py`：8 epochs、AdamW lr=3e-5、batch=12、max_length=96、seed=20260910、训练集正例权重、梯度裁剪。使用本地 CUDA 训练，不使用 DeepSeek 请求额度。按验证集 Macro-F1 选择 epoch 与全局阈值，最终只评估一次开发 test；没有据 test 继续调参。

选中 epoch 8，阈值 0.5。验证集 Macro-F1=0.8381、整组标签完全匹配=0.6667；15 条自编 test 的 Macro-F1=0.9429、整组匹配=0.8667。样本太小且是同源自编表达，不能外推为真实客服准确率。

产物位于 `artifacts/shopguide/bert-intent-bootstrap/training/`：protocol.json、history.json、selection.json、test.json、model-card.json 与 checkpoint/。模型卡记录每个 checkpoint 文件 SHA-256，训练协议记录底座 revision、数据、目录、脚本 hash 和软件版本。

## 候选层接入

`intent/bert.py` 已实现本地 checkpoint 加载、完整文件 hash 与标签检查，默认拒绝 development_only 模型；研究使用必须显式 allow_development=True。该标记表示受限开发使用，不要求重复向用户申请权限。

CandidateRetriever 可接入 classifier，真实 sigmoid 输出作为独立 bert 通道，取 Top3 参与 RRF。没有以 0.5 阈值自动执行业务，也没有用该分数冒充校准置信度。所有意图仍经过 Judge 与原状态/授权流程。

AgentRunner 可设置 intent_classifier_path 与 allow_development_intent_classifier=True；要求意图候选层启用，并把模型卡 hash 写入运行配置。未提供路径时 BERT 分支显式关闭，不自动把开发模型升级到默认客服配置。

重新加载实际训练权重后，“查询工单记录，另外怎么安装这个商品？”的 BERT Top3 为 service.progress、product.howto、product.troubleshoot，两个目标都进入候选融合。此适配测试的 BERT 为真实训练权重，其他检索通道使用标明的 fake fixture，不能称作完整生产模型联验。证据为 adapter-result.json。

## 工程验证与后续

训练数据隔离、开发模型默认拒绝、候选和意图回归共 19 项通过；Ruff/Mypy 通过。模型训练、保存、重载和候选融合实际执行。没有新增远端 LLM 请求。

后续需要：人工复核的真实对话数据、按会话及近似改写族分组、各类别和未知样本覆盖、上下文边界例、分类器贡献评测与阈值校准。真实数据可以沿用同一训练脚本重新训练，需新的不可变输出目录，不覆盖本次失败/成功记录。完整意图专用精排训练、RAG 页面/图片预算改造仍是独立未关闭项。
