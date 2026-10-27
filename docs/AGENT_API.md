# 给程序和模型的接口（MCP / CLI）

同一套能力有两个门：**命令行**（人用）和 **MCP 服务**（模型用）。两者都落在
`src/narrative/tool_api.py` 的同一张工具表上，所以「这个场景结束时各方是什么状态」这个
问题，人敲命令和模型调工具得到的答案不可能不一致。

界面（Qt Quick）不是这套接口的依赖：这里没有任何窗口状态，只有工程文件。

## 一条规则：`cut` 是阅读位置

所有读操作都可以带 `cut`（事件编号）。它回答的是「读到这一步时，读者知道什么」，
不是「故事里最终是什么」。

- 省略 `cut` = 最新
- `cut=34` = 读到事件 34 为止；之后的披露不算，失效的记录不出现
- `scene_state` 的 `cut` 自动取这个场景的最后一个事件

## 工具表

| 工具 | 做什么 | |
|---|---|---|
| `list_projects` | 列出某个目录下的工程（只读，不打开） | 读 |
| `project_info` | 工程标题、修订号、节点数、原文数、待审候选数 | 读 |
| `list_nodes` | 按阅读位置列出节点；可按类型、关键字筛选，返回分类计数 | 读 |
| `get_node` | 一个节点的全部字段与它在某阅读位置上的属性 | 读 |
| `node_properties` | 一个节点在某阅读位置上的属性（失效的不返回） | 读 |
| `scene_state` | 一个场景：它包含的节点，以及这一场结束时各方是什么状态 | 读 |
| `event_state` | 一个事件：相关节点，以及读到这一步时各方是什么状态 | 读 |
| `statistics` | 统计：节点分类计数、原文、属性记录种类、撤回数、审核计数 | 读 |
| `search` | 按关键字检索节点与断言 | 读 |
| `list_sources` / `read_source` | 原文清单；按字符位置读一段 | 读 |
| `audit_properties` | 属性审核：重复值、缺定义、缺证据、锚点缺失等结构问题 | 读 |
| `list_candidates` | 列出候选（默认待审） | 读 |
| `add_property` | 补录一条属性披露：立刻生效；更早的值从这次披露起失效 | 写 |
| `correct_property` | 更正一条取值：追加正确的记录并撤回原来那条 | 写 |
| `retract_properties` | 把记录设为失效：不删除，但从这个位置起不再显示 | 写 |
| `revise_node` | 修订节点的名称、类型、别名或说明（立刻生效） | 写 |
| `merge_properties` / `rename_property` | 两个名字合成一个方面；一个方面改名 | 写 |
| `categorize_records` | 把记录移到某个显示大类 | 写 |
| `accept_candidate` / `reject_candidate` | 处理候选（节点/属性/提取结果） | 写 |
| `extract_source` | 用工程配置的模型提取一篇原文；`commit=true` 同时生成待审候选 | 写 |

写操作遵守界面里那套规则，不是另一套：

- 记录不原地改。更正 = 追加正确的 + 撤回错的，两半在同一个候选里。
- 补录会立刻生效，并把同方面更早的值从这次披露起标为失效。
- 失效不删行：记录还在库里，只是不再显示。
- 没有引文就没有原文位置：这条记录算「该事件里最后说的」。

## 命令行

```bash
# 通用两件套：永远和工具表同步
narrative tool list
narrative tool call get_node '{"node_id": "7", "cut": "34"}' --project <工程.sqlite3>

# 名字更短的别名，参数是真 flag
narrative info   --project P
narrative nodes  --project P --type person --limit 10
narrative node 7 --project P --cut 34
narrative scene 28 --project P
narrative event 34 --project P
narrative stats  --project P
narrative search 火纹石 --project P

# 编辑
narrative add-property 7 state 神色平静 164 --project P
narrative correct-property value_abc123 三十一岁 --project P --reason 抄错了
narrative retract --ids value_abc123 --reason 这句读错了 --project P
narrative revise-node 2 --name 沈砚 --project P
narrative extract <source_id> --commit --project P
```

默认输出是给人看的一行行；加 `--json` 得到完整结构。

## MCP

```bash
narrative-mcp --project data/graph-projects/example.sqlite3
```

stdio、换行分隔的 JSON-RPC 2.0（MCP 的标准传输），只支持 `initialize`、`tools/list`、
`tools/call`、`ping`。给了 `--project` 之后工具不必再传工程；没给的话每个工具会多一个
必填的 `project` 参数。

客户端配置（WorkBuddy / Claude Desktop 之类）大致长这样：

```json
{
  "mcpServers": {
    "narrative-graph": {
      "command": "C:/a/wk/narrative/.venv/Scripts/python.exe",
      "args": ["-m", "narrative.mcp_server",
               "--project", "C:/a/wk/narrative/data/graph-projects/example.sqlite3"]
    }
  }
}
```

一条 `tools/call` 的样子：

```json
{"jsonrpc":"2.0","id":3,"method":"tools/call",
 "params":{"name":"scene_state","arguments":{"scene_id":"28"}}}
```

结果在 `result.content[0].text` 里，是一段 JSON 文本；工具自己出错时是
`isError: true` 加一句人话，协议层出错才是 JSON-RPC `error`。

## 从代码里用

```python
from narrative import tool_api

payload = tool_api.call("event_state", {"event_id": "34"}, project="data/graph-projects/example.sqlite3")
```

`tool_api.describe()` 给出 MCP 形状的工具清单，可以直接喂给任何需要 tools 定义的模型。
