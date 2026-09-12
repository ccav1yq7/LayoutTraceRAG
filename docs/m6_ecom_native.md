# M6 ECom 原生适配：工程交付与待运行评测

2026-09-09：已实现固定版本原生 MCP 工具、官方评分器桥接、独立 trial 进程/
数据副本、调用审计、watchdog、冻结清单与 pass^k 报告。**完整对比评测尚未完成，M6 退出条件未关闭。** 用户已批准并执行 2 个 dev trial，
20 次模型请求中一个完成、另一个中止，详见 [真实冒烟记录](m6_deepseek_dev_smoke.md)。

## 参考环境与使用

上游固定 `XiaoduoAILab/ECom-Bench@bc5018daaf45eea12330941bce68cebd293cfa85`，
53 个任务、21 个原生 MCP 工具。`external/ECom-Bench` 保持干净，执行前逐一
比对已跟踪 Python、政策、JSON/YAML 等文件的实际内容与固定提交。

为避免把上游 2025 年 MCP/Core 依赖混进产品环境，建立独立 Python 3.11 环境：

```bash
uv venv .venv-ecom --python 3.11
uv pip sync --python .venv-ecom/bin/python configs/ecom/requirements.lock
.venv-shopguide/bin/shopguide ecom smoke \
  --python .venv-ecom/bin/python --upstream external/ECom-Bench \
  --out artifacts/shopguide/m6/new-native-smoke
```

输出目录必须不存在；命令失败返回非零。主 wheel 提供适配器代码，sdist 还包含
独立依赖 lock 与配置。wheel 安装到参考环境时可使用 `uv pip install --no-deps`
安装，并保留其单独锁定的依赖。当前支持 Linux/POSIX 进程组和文件锁。

## 保留的原生语义

`EComToolAdapter` 通过真实 MCP `list_tools` 读取名称、说明和原始 inputSchema，
将真实调用送到官方 server.py 注册的工具，不改名或补写“预期调用”。所有
返回 content blocks 完整保留在工具日志，多模态块不被压平成一句摘要。
同一用户回合的全部调用以 JSON 字符串参数记录在
`AIMessage.additional_kwargs.tool_calls`，由官方 `_save_tool_calls` 自行读取。
传输异常后的写入标为 outcome unknown，原 call ID 不能自动重放。

每个 trial 启动独立 worker 进程、MCP 服务和数据副本。服务关闭后保存最终
JSON、前后哈希、对话与工具轨迹，再调用官方评分器；评分器会删除工作副本，
留存的审计副本不受影响。父进程以独立进程组执行 600 秒 watchdog，超时杀死
worker/MCP 后保存失败和可用的最终数据，再清理工作副本。

评分方法直接加载 SHA 校验后的原生类定义，避免导入未使用的旧模型构造器；
没有重写 action/search/output 判定：

- action：原数据应用期望 action 后，与实际执行后的数据比较。
- search：原生必要工具名/参数哈希集合是否包含在实际调用记录中。
- output：最终服务对话是否包含原生要求的字符串。
- reward：action × search × output。time 单独报告；固定代码的时间指标阈值为
  每次响应平均 30 秒，不能把它误称为 600 秒 watchdog。

到达原生 20 用户回合上限时仍由原生评分器评分，另记 termination；不擅自
把一个通过的原生评分改为失败。进程故障/预算未执行则保留对应状态和保守零分。
关键词/必要调用匹配不是语义准确率或检索精度。

## 协议适配与信息边界

用户模拟器保留原生 `UserBased` 的方法及 wiki，替换其无工具 LLM transport；
视觉工具保留 `get_image_info` 签名和提示模板，只替换模型调用，图片 URL
转为真实多模态输入。DeepSeek 原生 tool call 回传格式依据
[官方 Tool Calls 文档](https://api-docs.deepseek.com/guides/tool_calls/)。
三个角色均配置 `deepseek-v4-flash-vision-exp`；原论文旧模型的接口不可直接复用。
当前不可变模型 revision 未提供，不能宣称与论文配置直接可比。

Actor 请求只包含原生服务政策、当前公开用户消息、工具 schema 和实际结果；
用户模拟器获得 instruction，评分器才使用 metadata.actions/searches/outputs。
测试包含隐藏目标 canary，断言它不会进入 Actor 或用户模型请求。模型没有
读文件或运行 Python 的工具。这是应用层信息隔离，**不是不同 OS 用户间的
安全沙箱**；不要把它描述成能防御拥有本机代码执行权限的攻击者。

两种冻结方法为 `native-react`（自适应原生工具循环）和 `fixed-one-batch`
（每个用户回合最多一次工具批次，然后强制文本回答）。后者是工具轮数消融，
不是已验证等价的 ShopGuide B2，也不是原论文 baseline。原 ShopGuide B2/B3
正式对照还没有完成。两方法复用相同政策、工具、模拟用户、视觉服务、20 回合/
64 工具预算和外层超时。空回复明确失败，代替上游无界重试；此改动适用于两者。

每个模型请求最多输出 2048 tokens、30 秒超时，不自动重试；跨进程文件锁
统计算法、模拟用户、视觉三类请求，失败请求也占预算。没有本地响应缓存；
不能控制供应商缓存或服务端随机数。manifest 中 seed 仅控制报告 bootstrap，
模型温度 Agent/Vision 为 0，用户为 0.3。

## 测试与产物

新增 **22 项测试**通过；全量 **173 项 Python 测试**通过。Ruff/Mypy 通过，
覆盖 69 个模块文件。独立参考检查执行两个全新 trial，真实调用订单查询和
加急工具，验证第二次从未加急状态开始，原始环境数据不变。两个自制任务的
官方 reward/action/search/output 均为 1；删去真实轨迹或实际写入的负例为 0。

这只证明两个原生工具和评分/隔离链路可工作；全部 21 个工具已经枚举并保持
schema，但未逐个做真实业务成功路径验证。不得将该自制 fixture 计入官方 53 题。
参考 MCP 旧版本有一条 Pydantic settings 前向引用警告，功能检查通过，未屏蔽。

产物：`artifacts/shopguide/m6/tests.txt/xml`、`all-tests.txt/xml`、`lint.txt`、
`reference-checks/checks.json`、各 trial 的 `calls.jsonl`、`tool-schemas.json`、
`baseline/`、`final-data/`、`data-hashes.json`、`result.json`。
首次 launcher smoke 因把 venv Python symlink resolve 到基础解释器而失败；
已改为保留 venv 路径，失败与修正后产物均保留。
CI 新增无模型费用的 `ecom-reference` job；本轮仅本地验证，远端尚未执行。

## 已冻结的运行方案与费用确认

dev 固定为任务 0/1/2，holdout 为 3–52，未将 holdout 内容发给模型或按其答案调参。
资源复查仍为 **19 个链接仅 5 个可用**；dev 的 0/2 有不可用图片依赖，任务 1
无图片依赖。原始状态在 `resources.json`；全量任务清单保留故障任务，不筛掉它们。

| 配置 | 预定范围 | 次数 |
|---|---|---|
| `configs/ecom/dev-text-smoke.json` | 已登记 dev 的任务 1，两种方法各一次 | 2 trials；已批准并执行，20 次请求用尽 |
| `configs/ecom/dev-n1.json` | dev 三题，两方法各一次 | 6 trials |
| `configs/ecom/all-n3.json` | 全部 53 题，两方法各三次 | 318 trials；正式费用预算待定 |

JSON 和相邻 `.sha256` 文件锁定任务、上游文件、适配器源码、角色模型与协议。
源码或配置改变须重新冻结，原文件不会被自动覆盖。供应商不可变 revision
仍为空，资源尚不齐全，因此这些是协议适配计划，不能打上正式 benchmark 标记。

本次已按用户批准执行以下命令；产物目录已存在，请勿覆盖：

```bash
.venv-shopguide/bin/shopguide ecom run \
  --python .venv-ecom/bin/python --upstream external/ECom-Bench \
  --manifest configs/ecom/dev-text-smoke.json --private-config LLM.config \
  --max-model-calls 20 --out artifacts/shopguide/m6/deepseek-dev-text
```

20 次是整个运行的请求上限，包含模拟用户和视觉，不等于固定金额；失败请求
也计数。预算耗尽后的计划任务记录为 budget_not_run，不能假装执行成功。
报告包含完整计划分母、缺失/失败数量、原生维度、可计算的 pass^k、按任务聚类
bootstrap 与两种方法的配对 pass^1 差值。n=1 只报告 pass^1，n=3 不外推 pass^5/8。

## 尚未验收

真实模型固定策略的单个 dev 闭环已完成，自适应策略在本次共享预算用尽时
中止。完整 dev/holdout 基线、全工具业务成功路径、图片模型/外部资源完整性、
ShopGuide B2/B3 公平对照和全量重复试验仍未完成。此前 20 次请求预算已获得
确认并用尽，未超额调用；追加评测需另行明确预算，不能视作已有无限授权。
