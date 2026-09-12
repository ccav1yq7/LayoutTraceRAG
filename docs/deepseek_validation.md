# DeepSeek 接入与验证

2026-09-09：当前模型已按用户最新指定切换为 **deepseek-v4-flash-vision-exp**，
配置从本地 `LLM.config` 读取。选图、Writer 和 Verifier 均使用该模型。

真实调用结果：

| 检查 | 实际结果 |
|---|---|
| 视觉探测 | 正确读取图片中的 K7P4，并识别红色方形和蓝色圆形 |
| 视觉探测 token | 输入 271，输出 37；约 0.869 秒 |
| B2 自制面板问答 | 选图、Writer、独立 Verifier 三次真实调用成功 |
| B2 图像输入 | 累计 6 次，展示 2 个不同资产 ID |
| B2 token / 耗时 | 输入 4,305，输出 975；约 9.921 秒 |
| B2 核验 | structural=pass，semantic=supported（独立模型核验，不是官方正确率） |

之前的 deepseek-v4-flash 也完成了 B1 文字问答，但那属于历史测试；当前
模型已更新为视觉实验版。没有将纯文本模型的图片占位输入计作视觉通过。

本轮 B2 使用自制 TEST-DS 面板，hash embedding/RRF 均为测试实现，模型网关
为真实 DeepSeek。结果的 component_modes 为 embedding=fake、gateway=real、
reranker=fake，因此整体仍标工程 fake。此结果不证明真实 BGE 或 PM209 基线效果。

复现（新建输出文件，不覆盖原始日志）：

```bash
.venv-shopguide/bin/shopguide ask \
  --root .shopguide/deepseek-vision-smoke --snapshot snapshot_ds_vision \
  --principal user_demo --product product_ds_vision --variant variant_ds_vision \
  --baseline B2 --test-retrieval --private-config LLM.config \
  --question 'How do I start this synthetic training panel? Show the relevant image and preserve the warning.' \
  --out artifacts/shopguide/deepseek/recheck.json
```

真实 BGE 路径继续使用 embedding/reranker 的模型与 revision 参数，并保留
--private-config LLM.config。--fake 和 --test-retrieval 不能混用。

原始结果：`artifacts/shopguide/deepseek/vision-probe.json`、
`live-b2-vision.json`。原 GPT 转发服务的 503 保留为历史问题，不代表 DeepSeek
仍不可用。正式模型 revision、真实检索环境、NLGEval 与完整 val 基线仍待完成。

接入回归：115 项测试通过；Ruff 和 Mypy（39 个文件）通过，wheel 构建及
独立安装的 provider 配置加载通过。凭据未进入包或本轮日志。
