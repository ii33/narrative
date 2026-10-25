# GraphRAG 交叉审核（只读）

GraphRAG 不能直接生成项目的 Node/Assertion 合同对象，但可以作为**对抗式召回检查器**：它用另一条抽取路径看同一段原文，用来暴露候选可能漏掉的实体、粗粒度事件和可疑 claims。它**不是 ground truth**，缺席不等于候选错。

## 1. 它检查什么

| 检查 | 做法 | 输出 |
| --- | --- | --- |
| 节点覆盖 | 用节点中文名/别名定位原文 text_unit，再看同单元内是否有类型兼容的 GraphRAG 实体 | `matched / unit_only / missing / no_evidence` |
| 别名候选 | 同单元有类型兼容实体但名称不同 | `unit_only` + 候选标题 |
| 时间对 | 对 `before` 断言只检查两个事件是否有 GraphRAG 关系 | `not_comparable`（GraphRAG 没有事件顺序） |
| claims | 与图节点相关的 GraphRAG claims，重点看 `SUSPECTED` | 可疑 claim 列表 |
| 证据映射 | 图 evidence 引文是否能在 GraphRAG text_unit 中原样找到 | `unmapped_quotes` |

## 2. 用法

```powershell
uv run python scripts/graphrag_cross_check.py `
  --project data/example-run/graph.sqlite3 `
  --graphrag-root data/ms-graphrag-threechapters-20261018/standard-run `
  --aliases examples/graphrag_aliases_sample.json `
  --out data/example-run/property-acceptance-20261020/cross-check
```

输出 `cross-check.json` 与 `cross-check.md`；不写任何正式图对象。

`--aliases` 是中文正名到 GraphRAG 罗马化标题的映射。没有它时，名称匹配会退化为 `unit_only`，但不会误报 `missing`。

## 3. 三章实测（2026-10-21，英文索引）

| 指标 | 结果 |
| --- | --- |
| 节点总数 | 62 |
| matched | 32 |
| unit_only | 20 |
| missing | 8 |
| no_evidence | 2 |
| 时间对 | 21，全部 `not_comparable` |
| 涉及图节点的 claims | 110 / 113 |
| `SUSPECTED` claims | 5 |
| 未映射 evidence 引文 | 0 |

按类型：

- **人物 8/8 matched**（林舟、老周、苏娘、陈默、何九、阿七、沈砚、女友）。
- **物品 15/17 matched**，1 unit_only，1 no_evidence。
- **地点 7/7 matched**。
- **组织 2/3 matched**，1 no_evidence。
- **事件 0 matched**：15 unit_only、8 missing。GraphRAG 的事件抽取粒度更粗（例如把多个事件合成 `ANSHENG FRAUD INCIDENT`、`BODY MODIFICATION`、`MARS TELEPORTATION`），无法逐事件对齐。

这说明：**GraphRAG 适合检查“实体有没有被看见”，不适合检查“事件边界和顺序对不对”**。

### 3.1 中文索引对比（2026-10-21）

同一图夹具、同一命令，只把 `--graphrag-root` 换成中文索引：

| 指标 | 英文索引 | 中文索引 |
| --- | ---: | ---: |
| matched | 32 | 31 |
| unit_only | 20 | 20 |
| missing | 8 | 9 |
| no_evidence | 2 | 2 |
| 涉及节点的 claims | 110 | 115 |
| `SUSPECTED` claims | 5 | 20 |

中文索引没有提高实体覆盖率（本来已靠中文名定位 + 别名表达到 32），但 claims 召回从 110 升到 115，`SUSPECTED` 从 5 升到 20。**claims 更多、噪声也更多**；事件仍然是 15 unit_only + 8 missing。中文索引的价值主要在标题、社区报告和 claims 可读性，不在事件级覆盖。

## 4. 已知限制

1. 当前索引是英文/罗马化标题（82/82 实体标题为 ASCII），必须靠别名表桥接；中文索引建成后可去掉大部分别名。
2. GraphRAG 没有事件顺序，`before` 断言全部 `not_comparable`，不能拿它验时序。
3. `missing` 只表示“同单元没有类型兼容实体”，不表示节点错；事件尤其如此。
4. `SUSPECTED` claim 是提示，不是矛盾判定；需要人读原文。
5. 交叉审核只读，不产生候选、不改修订。

## 5. 下一步

1. 中文索引建成后重跑同一命令，对比 matched/unit_only/missing 与 claims 语言。
2. 若事件仍大面积 `unit_only`，把事件检查降级为“章节级覆盖”，不再逐事件比对。
3. 把 `SUSPECTED` claims 接入候选审核页，作为人工复核提示；不要自动拒绝。
