# 真实模型 claim 转换报告

## 1. 真实模型调用

| 项 | 值 |
| --- | --- |
| claim | `809b4364-6984-419d-84c3-cf8ab0792d54` |
| 原文 | “林舟戴上了手镯，立即明白其中奥秘……” |
| 来源 | 三章中文索引，chapter 1 |
| 模型 | `deepseek-chat`（`https://api.deepseek.com`） |
| prompt / schema | `claim-conversion/1` / `claim-conversion-json/1` |
| usage | prompt 1603 / completion 865 / total 2468 |
| 原始响应 | `data/graphrag-model-20261021/claim-809b/raw-response.json` |
| 请求与上下文哈希 | `request.json` |

模型只输出 label、facts、unresolved 和证据说明；内部 ID 由程序解析和分配。原始响应、规范化 JSON、校验诊断分开保存。

## 2. 结构化结果

`normalized.json` 的要点：

- `holder`：铜钥匙 → 林舟，事件 `wear_tanbo`，边界 `start`，证据“林舟戴上了手镯”。
- `before`：`wear_tanbo → cash_appear → diamond_appear → knife_appear`，各自附原文引文。
- `unresolved`：
  - “立即明白其中奥秘”没有说明具体内容，不生成认知；
  - 钞票、钻石、长刀是否属于铜钥匙内物品，原文只是林舟惊呼，未明确断言。
- `evidence_notes`：包含“为什么是世界事实”“哪个动作提供锚点”“两处引文为什么指同一只手镯”的简短说明。

## 3. 语义核对

| 项 | 模型 | 人工对照 | 处理 |
| --- | --- | --- | --- |
| 持有变化 | 铜钥匙 → 林舟 | 一致 | 接受 |
| 事件边界 | `start` | `end` | **保留模型输出并标记，未静默修改** |
| 初始持有 | 未输出 | 老周持有（初值） | 未补造；本条记为部分转换 |
| “明白奥秘” | `unresolved` | 不生成具体认知 | 接受为 unresolved |
| 事件顺序 | 现金 → 钻石 → 长刀 | 人工未建 | 已接受，需人工复核 |
| 指代 | “手镯”指铜钥匙 | 一致 | 接受 evidence_note |

人工修订单独记录在 `result.json` 的 `manual_reviews`，没有覆盖模型原始输出。

## 4. 正式查询

| 项 | 结果 |
| --- | --- |
| 候选 | `e8d2c2bdf1854deeb961b78738667b66` |
| 接受修订 | 6 |
| 查询 | `铜钥匙.holder` 在 `wear_tanbo.start` = **林舟** |
| 查询文件 | `result.json` / `claim-status.json` |

边界为 `start` 是模型判断；人工对照用 `end`。这条差异已标记，当前正式记录仍按模型输出生效，下一轮可提交带证据的修订。

## 5. 稳定重跑

同一 `sample.json`、同一映射、同一导入指纹重跑：

- `reused_import = true`
- 候选仍为 `e8d2c2bdf1854deeb961b78738667b66`
- 修订保持 **6 → 6**
- 没有重复节点、属性记录或额外修订

## 6. 进度记录

- `unresolved-snapshot.json`：保留 232 条原始未解决项（114 relationships + 118 claims）。
- `import-status.json`：按外部 ID 记录状态；当前 232 条中：
  - `untreated`：231
  - `partial_accepted`：1（本 claim，已接受相关 holder/事件顺序，但初始持有与边界语义未完成）

状态字段包含 `candidate_id`、`revision`、`converted`、`not_converted`、`manual_review`。

## 7. 结论与下一步

这轮证明了：

> **真实 claim → 真实模型 JSON → 程序解析/证据校验 → 统一候选 → 接受 → 状态查询 → 稳定重跑** 已经运行。

仍待完成：

1. 把模型输出的 `start` 与人工 `end` 的边界差异，作为一次显式修订提交，而不是静默改字段；
2. 让模型补出“初始持有者 = 老周”，或明确标记为无法从该片段确定；
3. 对第二条真实 claim 重复同一流程，评估模型稳定性；
4. 将 231 条 `untreated` 按优先级分批处理，不追求一次全转。


## 8. 时间语义校准（claim 809b）

模型把 `holder` 变化放在 `wear_tanbo.start`，但原文只支持“戴上完成时林舟持有”，不能确定恰好在开始还是结束取得。处理：

| 修订 | 动作 |
| ---: | --- |
| 6 | 模型候选接受（holder start + 3 条 before） |
| 7 | 显式撤回模型 `holder start` 记录 |
| 8 | 撤回为校准而加的重复完成边界观察（与人工初值在起点冲突） |
| 10 | 新增 `e0（老周取出手镯） → wear_tanbo` 的 before，证据“老周手中有如变戏法般冒出另一只手镯” |
| 11 | 新增 `wear_tanbo.start → wear_tanbo.end` 的 before，建立事件自身起止顺序 |

当前查询：

- `wear_tanbo.start`：`holder = 老周`（人工初值）
- `wear_tanbo.end`：`holder = 林舟`（人工完成边界变更，项目约定动作结果登记在完成边界）
- `as_of_revision=6`：仍还原模型当时的 `start` 记录及其冲突，不被新修订覆盖

模型 `start` 记录没有静默改写；它被显式撤回。人工 `end` 记录保留，是因为项目已有“动作结果登记在完成边界”的约定，不是因为人工对照用了 `end` 就机械替换。

`before` 链 `wear_tanbo → cash_appear → diamond_appear → knife_appear` 保留：原文明确连续描述钞票、钻石、长刀的出现，属于可支持的顺序；事件类型按“出现”解释，不把列举当顺序。

## 9. 第二条 claim：信念路径（claim 6e2c）

原文：“林舟知道老周赢了。”

模型输出：

- 1 条 `belief`：林舟知道“老周赢了”，证据“林舟知道老周赢了。”；
- 8 条 `holder`、4 个事件、3 条 before；
- 1 条 unresolved：吸精针归属不明确。

问题：8 条 holder 都写了 `boundary=start`，但没有 `at_event`，会被解释为故事开头的初值。结构校验通过，语义不成立。处理：

- 模型候选 `da1927aeece04782b2568c8912482b7c` **整批拒绝**，原始响应保留；
- 只保留有原文直接支持的 belief，人工填写修正 sample，形成候选 `d5f584d7e0364cc2b4e3f4dc2d3b61e0`，接受修订 9；
- 查询：林舟对老周的 `content` 认知 = `赢了`；世界范围同属性 = `unrecorded`；
- 重跑复用候选，修订保持 9。

这说明信念路径可用，但模型对“物品归属”会过度生成，不能整批接受。一次成功不等于稳定。

## 10. 进度

232 条原始未解决项中：

- `partial_accepted`：2（809b 持有/时序校准；6e2c 仅信念）
- `untreated`：230

台账：`data/graphrag-import-20261021/import-status.json`。


## 11. Mapper 合同修复与 v2 回归

程序层已收紧，不再依赖提示词：

- `holder` / `belief` 必须提供 `at_event` 或 `at="initial"`；
- 只有 `boundary`、没有可解析事件时，进入 `unresolved`，**不生成 `property_initial`**；
- `unresolved` 带 `fact_index`，可定位到第几条事实；
- 新增回归 `test_boundary_without_event_is_not_converted_to_initial`。

用 `claim-conversion/2` 对同一条信念 claim 再调用一次模型：

| 项 | v1 | v2 |
| --- | ---: | ---: |
| 无事件锚的 holder | 8 | 0 |
| 目标 belief | 1（正确） | 0 |
| 无关 holder | 8 | 5 |
| 未解决项 | 1 | 3 |
| 候选 | `da192...`，拒绝 | `1338...`，拒绝 |

v2 的合同错误消失，但模型仍过度生成无关 holder，并漏掉目标 belief。两个候选都整批拒绝，正式修订未变化。结论：**程序拦截有效，模型语义范围仍未收敛**，不能把 v2 当作质量提升。

## 12. 当前结论

- 链路：候选/接受/修订/查询/重跑已通过真实材料。
- 第一条模型输出：部分采用，时间语义经人工校准。
- 第二条模型原始候选：整批拒绝；最终 belief 来自人工删减修正。
- 批量自动抽取质量：当前样本不足，不能判断。
- 下一步不是扩建模型，而是继续收紧输入合同与提示词，并用失败样例做回归。


## 13. 目标限定实验 v3

输入收窄到目标 claim + 260 字上下文 + 仅林舟/老周实体，prompt 为 `claim-conversion/3`。

| 项 | v1 | v2 | v3 |
| --- | ---: | ---: | ---: |
| 目标 belief | 1（正确） | 0 | 1（自锚点） |
| 无关 holder | 8（无锚点） | 5（有事件） | 0 |
| 目标进入 unresolved | 否 | 否（只处理了其他物品） | 是，原因明确 |
| 程序结果 | 候选拒绝 | 候选拒绝 | 候选拒绝，无正式修订 |

v2 的 3 条 unresolved 只涉及桃花枝/万花罩/寒铁剑的持有初值，没有目标句；目标是**被遗漏**，不是暂缓。v3 消除了无关扩展，目标被识别，但模型把“老周获胜”事件同时当作认知对象和认知时间锚（`node == at_event`）。

程序规则已收窄：同事件可以同时是认知对象和时间锚，但 `belief` 必须提供 `time_basis`，且 `time_basis` 必须出现在证据引文中。本样例没有 `time_basis`，因此进入 `unresolved`：

```text
same event used as content and time anchor, but no explicit time-basis evidence
```

正例 `test_same_event_anchor_with_time_basis_is_allowed` 验证“比赛结束时，林舟已经知道老周赢了”可以生成观察记录，并带一条“同事件需人工复核”的 warning。候选 `2943717e9a2a429d93fc1aa62c31051d` 仍整批拒绝，`property_records` 为空，正式修订不变。这满足三项完成条件：目标有明确去向、时间没有补造、输出围绕目标。但它仍不是自动语义提取的成功，只是“任务范围 + 缺失信息处理”这一层通过。


## 14. 披露优先导入

v3 的信念输出仍没有可用的故事时间锚，但原文位置是确定的。用
`disclosure_only=True + allow_disclosure_fallback=True` 重新映射：

- 忽略模型给出的 `at_event`，按证据“林舟知道老周赢了”的原文位置登记
  `property_observation`；
- 候选 `5743189401384584814ff3f172ba3e56`，接受修订 13；
- 披露切点前：`unrecorded`；
- 披露切点后：`belief content = 老周赢了`；
- 故事时间查询：`unrecorded`；
- 未解决：0。

这条记录表达的是“读者在该原文位置看到林舟知道老周赢了”，不是“林舟在故事时间某刻得知”。披露查询与故事时间查询分开，互不污染。
