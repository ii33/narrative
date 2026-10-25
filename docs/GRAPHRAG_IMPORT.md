# GraphRAG → 统一候选导入

## 流程

```text
GraphRAG export / 人工样例
  → 实体映射为节点（精确名称 + 类型；或已保存的外部 ID 映射）
  → 明确的结构化事实映射为属性记录 / 时序断言
  → change_precheck
  → change_propose
  → change_accept（人工或显式 --accept）
  → graph_query / property_state
```

mapper 不绕过统一候选，也不自动接受。预览 `graphrag_preview` 仍是只读操作。

## 输入

- **GraphRAG export**：`narrative-standard-export.json` 的 `tables.entities`、`tables.relationships`、`tables.covariates`。
- **人工样例**：`source_id`、`entities`、`events`、`facts`（`before` / `holder` / `belief`）。

## 映射规则

| 输入 | 落点 | 说明 |
| --- | --- | --- |
| `entities` 的人物/物品/地点/组织/事件 | 节点 | 已保存的外部 ID 映射或唯一确认别名才复用；仅名称/类型相同只作为身份候选，保留独立节点 |
| `relationships` 自由文本 | `unresolved` | 没有可靠的 predicate/property 映射，不硬转 |
| `covariates` 自由文本 claim | `unresolved` | 不自动当世界事实或人物信念；保留原文与原因 |
| 人工样例 `holder` | 事件变更（`at_event` + `boundary`）或明确初值（`at="initial"`） | 只有 `boundary` 没有事件时进 `unresolved`，不生成初值 |
| 人工样例 `belief` | `scope=belief` 的属性记录 | 必须有人物 holder，且提供 `at_event` 或 `at="initial"`；`at_event` 可以等于认知对象事件，但必须提供出现在证据中的 `time_basis`；否则保留为 unresolved。世界范围不因此得到同一值 |
| 人工样例 `before` | `temporal` 断言 | 事件边界 + 原文证据 |
| 人工样例 `contains` | `containment` 断言（父事件→子事件） | 一段连续动作只算一件事；分阶段时用父事件收拢，`order_index` 只表示同父阅读顺序 |

三条硬规则：

1. GraphRAG 的 `claim` **不自动**对应 `scope=claim`；无法判断语义时进 `unresolved`。
2. 普通关系继续走属性合同，不重新打开已停用的 relation/attribute 断言。
3. 抽取与推演分开；没有支持组的记录默认是独立记录，不冒充派生结论。

## 事件粒度

一段原文里的连续动作（取出手镯 → 戴上 → 从里面取出东西）是**一件事**，不是几个并列事件。

- 模型转换：只输出一个事件；确实要分阶段时，输出上层事件并用 `{"kind":"contains","parent":"<上层>","child":"<阶段>","evidence":"..."}` 声明归属。
- mapper：`contains` 生成 `containment` 断言（`semantic=containment`，`value_type=node`），父事件成为层级视图的根，子事件下沉一层。
- 事件名用完整动作短语（“老周赠铜钥匙”），不用“开始/结束”“第一步/第二步”。

三章数据里，`老周取出手镯`、`林舟戴上铜钥匙`、`手中现出一沓钞票`、`现出鸡蛋大小的钻石`、`现出长刀`
原本是五个并列根事件；现在归入 `老周赠铜钥匙`，层级视图只显示这一个根，展开才看到五个步骤。

## 证据、身份、时间

- 证据：`ops.locate(source_id, quote)` 必须恰好一处；否则进 `unresolved`。
- 身份：只有持久化外部 ID 映射或唯一确认别名才复用；仅名称/类型相同会新建独立节点，并在 `identity_note` / `identity_candidates` 中标记，不自动合并。
- 时间：人工样例的 `before` 用事件边界；GraphRAG 事件没有边界信息时只建事件节点，不猜顺序。
- 披露：GraphRAG 输入没有可靠披露位置时，不填默认值；相关记录留待人工样例或后续规则。

## 重跑

- `mapping.json` 保存 `namespace → external_id → internal_id`。
- `imports.json` 保存导入指纹（export 内容 + 映射版本 + namespace）。
- 同一指纹重跑直接复用原候选，不新建节点、不空增修订。
- 来源或规则变化会产生新指纹，形成可检查的差异。

## 真实导入结果（三章中文索引）

| 项 | 数值 |
| --- | ---: |
| 输入实体 | 75 |
| 新节点 | 75 |
| 复用节点 | 0 |
| 未解决 relationship | 114 |
| 未解决 claim | 118 |
| 候选 | `064f49aa6ef84320bc9a0a710eb86059` |
| 接受修订 | 3 → 4 |
| 重跑 | 复用原候选，修订保持 4 |

节点查询可命中：林舟、老周、苏娘、火纹石、火星、云顶星。本次是全新项目，75 个实体都是新节点，`identity_candidates` 为 0；若导入到已有同名节点的项目，会保留独立身份并列出候选，不合并。

**限制**：自由文本 relationships/claims 全部进 `unresolved`；实体没有附带节点级证据；GraphRAG 事件没有边界，因此只建事件节点，不建事件顺序。真实导入的“状态”目前是节点存在与可检索，不是属性状态。

## 真实 claim 转换（一个片段）

从三章真实 claims 中选 `809b4364-6984-419d-84c3-cf8ab0792d54`：

> 林舟戴上了手镯，立即明白其中奥秘……

转换：

- 事件：`林舟戴上铜钥匙`（事件节点，边界为戴上动作结束）。
- 属性：`铜钥匙.holder`，初值老周，事件结束变为林舟。
- 证据：初值 `老周手中有如变戏法般冒出另一只手镯`；变化 `林舟戴上了手镯`。
- 候选 `ae64cf49dde24428b94eb045fe6c6ba9`，接受修订 4→5。
- 接受后查询：`铜钥匙.holder = 林舟`。
- 重跑：复用候选，修订保持 5。

**判读方式**：这一条使用人工判读填写 `entity_map` + `facts`，程序负责证据定位、引用、预检和候选。`entity_map` + `facts` 就是模型辅助转换应输出的合同；模型调用本身尚未接入。因此它证明的是“真实 claim → 结构化状态 → 统一候选 → 查询”的转换链路，不是自动语义提取的覆盖率。

同一片段其余 relationships/claims 仍在 `unresolved`；真实导入的 232 条未转换项没有被错误写入。

## 模型辅助转换（真实调用）

`scripts/model_claim_convert.py` 已接通真实模型：`deepseek-chat`，prompt `claim-conversion/1`，schema `claim-conversion-json/1`。
模型输出 `entity_map + facts + unresolved + evidence_notes`，程序负责解析、证据定位、身份映射和统一候选；原始响应与规范化结果分开保存。

首个真实 claim（`809b...`）结果：候选 `e8d2c2bdf1854deeb961b78738667b66`，接受修订 6，`铜钥匙.holder = 林舟`；重跑复用候选，修订保持 6。模型把持有变化边界判为 `start`，人工对照为 `end`；差异已记录为待修订，没有静默修改。

完整输入、原始响应、语义核对和进度记录见 [真实模型 claim 转换报告](MODEL_CLAIM_CONVERSION.md)。

## 人工样例结果

原文：

> 甲持有信件。甲把信交给乙。乙以为信里是账单。

| 查询 | 结果 |
| --- | --- |
| 交付前 `letter.holder` | 甲 |
| 交付后 `letter.holder` | 乙 |
| 乙的认知 `letter.content` | 账单 |
| 世界范围 `letter.content` | `unrecorded` |
| 重跑 | 复用原候选，修订不变 |

样例候选 `f3811598a83a4d629465891e2ac20f04`，接受修订 2。

## 命令

```powershell
uv run python scripts/graphrag_import.py `
  --project data/import/graph.sqlite3 `
  --export data/ms-graphrag-threechapters-zh-20261020/narrative-standard-export.json `
  --namespace sample_zh `
  --mapping data/import/mapping.json `
  --imports data/import/imports.json `
  --out data/import
```

人工样例用 `--sample sample.json`；`--accept` 仅用于显式本地验证。

## 未完成

- 自由文本 relationship/claim 的语义映射（需要谓词/属性抽取规则或模型初审）；
- GraphRAG 事件边界与参与者映射；
- 节点级证据与披露位置的自动定位；
- 导入差异报告（同一来源版本、不同规则）。
