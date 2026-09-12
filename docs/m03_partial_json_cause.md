# 部分回答与核验 JSON 专项诊断

针对前次 case-0 核验错误和 case-6 无图 partial，各运行一次，共 6 请求；累计 838/870，剩余 32。源码冻结哈希与连续账本检查通过，无自动重试。只捕获最终输出 content、finish_reason 和 usage，不捕获隐藏推理或请求凭据。证据目录：`artifacts/shopguide/coverage-diagnostics/cause-probe/`。

## 无图 partial：直接原因已确认

原问题为“What should you do if any part is missing or damaged?”。前次 Selector 查看 4 图但选择为空，Writer 和 Verifier 仅使用文字；回答给出了不要使用音箱、联系经销商或 Bose 的指引，结构与语义核验通过。FixedRAG 在 deliver 阶段对 B2 且无 used_images 无条件标 partial。因此该标签描述缺图，不能用来认定问题只回答一部分。问题本身没有要求图片；当前产品完整性标签与内容完整性混在一起。

本次同题 Selector 选择 2 图并最终 answered，说明选图结果有运行波动。前次不选图的具体主观原因未记录，不能断言无相关图片或模型不具备读图能力。候选图前四张限制的影响仍需单独审计。合理修复方向是将内容完整性与配图状态分开，并依据问题是否需要图来判定交付完整性，而非简单把所有无图回答改为 answered。

## 核验 JSON：历史具体错误无法追溯

前次失败点是模型最终正文的严格 JSON 解析。该代码同时拒绝 JSON 语法错误和重复键；不是业务字段校验，也不是已记录的输出超时。旧正文未留存，无法进一步确定是哪一种错误或具体位置。

本次该样例三个阶段均为 finish_reason=stop，正文均通过严格 JSON 解析；最后交付 partial（本次 Writer 自报 partial，与上述另一题无图降级不同）。其 Verifier 没有复现旧错误。不能用新成功输出解释旧失败，也不能声称已找到或修复旧 JSON 的具体根因。

本轮六份输出都通过严格 JSON 解析。诊断捕获可区分 syntax（含行列位置）与 duplicate_key，但只有再次发生时才能提供更具体证据。停止继续盲目请求；本轮没有更改模型提示或放宽解析、核验规则，也未更新全量覆盖率。
