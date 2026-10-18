# 上下文预算与自动压缩（SDK/runtime 0.1.5rc1）

本轮依据安装的 `deepseek-harness-sdk==0.1.5rc1` 与同版本 runtime 二进制内嵌的官方插件源码、README，以及 `--dump-default-config`/`--dump-config` 核实配置；未以当前主分支文档替代安装版本。运行器同时检查两个已安装发行包的版本。

| 设置 | 新项目默认 | 约束与作用 |
| --- | ---: | --- |
| `context_window` | 1,000,000 | 严格正整数，设置路由模型的真实 SDK capacity |
| `output_reserve` | 131,072 | 严格非负整数，压缩触发前预留的主输出空间 |
| `compaction_reserve` | 65,536 | 严格非负整数，为压缩指令、近期增长等保留额外空间 |
| `compaction_max_tokens` | 32,768 | 严格正整数，且小于上下文窗口；单次摘要输出上限 |
| `max_tokens` | 131,072 | 主请求单次输出上限，与窗口预留独立；显式 `null` 时由服务默认决定 |

有效输出预留为 `max(output_reserve, max_tokens or 0)`；窗口必须严格大于有效输出预留与压缩预留之和。摘要上限独立于压缩预留，不要求二者相等或前者更小。摘要上限小于窗口只是最低可行性检查，并不证明摘要输入与输出一定能容纳；压缩预留应覆盖实际提示与近期增长。

SDK 旧版本没有直接的两个 reserve 配置字段。运行器将触发阈值换算为 `context_window - effective_output_reserve - compaction_reserve`，再以 `thresholdRatio = threshold / context_window` 配置 `dsh-compaction-basic`；默认触发阈值是 803,392。近期原文保留比例沿用插件的 16%，当阈值更小时取阈值比例的一半，确保严格低于触发阈值。

`sdk-minimal` 原本没有 token-meter 或 compaction。运行器保持提取安全 patch，在后续动态 patch 中通过受支持的 `insert` 语法挂载同版本 `dsh-token-meter`、`dsh-compaction-basic`，并同时设置 `llm-deepseek.defaultContextWindow` 与精确模型 catalog 的 `contextWindow`，避免 catalog 的旧容量覆盖用户窗口。shell、会话上传、LLM retry 继续禁用；摘要额外尝试和溢出重试均为 0。

摘要使用仅在本地 SDK 内部存在的模型别名 `__narrative_compaction__`；网关在发送前将其替换为所选真实模型 ID，并应用摘要专属输出上限。其他调用使用主输出上限。二者均通过已选服务、已有会话头和同一个网关计量，不依赖提示词匹配。

`context_policy` 保存无密钥的有效配置；`compaction_events` 保存安全的生命周期和统计字段；`model_requests` 保存本轮所有主请求与摘要请求的 `purpose`、输出上限、原始文本、结束原因、usage、完整性。`compaction_request_count` 和总请求数分别记录摘要与全部请求。每轮进入网关会话时重置这些请求记录，历史轮次不会重复计费。摘要请求是额外模型调用，必须计入本轮预算。

计量与触发边界：该版本 token-meter 以固定启发式估算文本（约四字符一 token，加角色与块开销）。只有规范请求 envelope 相同且 provider usage 不低于完整估算锚点时，才复用提供方 usage；表面变化按同一锚点计入增减。中文与 JSON 可能被低估，配置窗口与触发阈值不构成精确 tokenizer 或严格上下文上限保证。压缩发生在已有已路由请求的步骤边界，无法预先压缩第一条尚未建立路由的请求；它不能切分不可分消息、缩小系统提示或强制保留的近期内容。

该版本插件自身在压力压缩失败后可能警告并继续生成。本项目在网关锁存摘要截断、HTTP/传输失败或流未完成，并在 SDK 尝试继续生成时拒绝向上游转发；不自动重试。`compaction/end` 的错误也会锁存。`HarnessReply` 对压缩失败强制报告 `finish_reason=error`、`compaction_incomplete=true`、`compaction_failure`，`blocked_generation_count` 记录本地阻止的主请求数；已发摘要的原始响应和 usage 保留。GraphAgent 现有结束状态检查先于动作解析，因此该轮标为 incomplete 且不提出候选。生命周期错误通知与 SDK 后续 HTTP 存在并发时，网关尽可能提前阻止，运行器仍后验报告失败；仅插件加载或存在压缩事件不代表实际输出质量通过。

工程证据由 `tests/test_context_runtime.py` 覆盖：固定 runtime 的组合配置、安全 patch、无模型启动，以及本地伪服务上的真实 SDK 摘要→主请求。用极小触发阈值验证摘要真实发生、131,072/32,768 两种输出上限各自生效、原始文本与 usage 保存、第二轮仅计本轮两次调用。摘要 length 截断和 HTTP 失败均验证只发一次摘要、后续主请求在本地被阻止、原始响应保存与脱敏，以及 GraphOperations 最终 incomplete 且无候选。这是工程验证，不替代真实服务与原文验收。
