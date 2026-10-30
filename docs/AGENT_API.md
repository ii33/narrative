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
| `list_projects` | 列出某个目录下的工程包（只读，不打开） | 读 |
| `project_info` | 工程标题、修订号、节点数、原文数、待审候选数 | 读 |
| `list_nodes` | 按阅读位置列出节点；可按类型、关键字筛选，返回分类计数 | 读 |
| `list_moments` | 阅读位置的层级：场景 → 事件，一列一层；`cut` 只能用这里的编号 | 读 |
| `node_material` | 一个节点的资料卡：标题、详情、带说明的图片，各自的有效区间 | 读 |
| `get_node` | 一个节点的全部字段与它在某阅读位置上的属性 | 读 |
| `node_properties` | 一个节点在某阅读位置上的属性（失效的不返回） | 读 |
| `scene_state` | 一个场景：它包含的节点，以及这一场结束时各方是什么状态 | 读 |
| `event_state` | 一个事件：相关节点，以及读到这一步时各方是什么状态 | 读 |
| `statistics` | 统计：节点分类计数、原文、属性记录种类、撤回数、审核计数 | 读 |
| `search` | 按关键字检索节点与属性记录 | 读 |
| `list_sources` / `read_source` | 原文清单；按字符位置读一段 | 读 |
| `property_categories` | 每个节点类型的大类表（显示分组，不是事实） | 读 |
| `audit_properties` | 属性审核：重复值、缺定义、缺证据、锚点缺失等结构问题 | 读 |
| `list_candidates` | 列出候选（默认待审） | 读 |
| `add_property` | 补录一条属性披露：立刻生效；更早的值从这次披露起失效 | 写 |
| `correct_property` | 更正一条取值：追加正确的记录并撤回原来那条 | 写 |
| `retract_properties` | 让记录不再显示：给了 `event_id` 是「从这一步起不算」，省略是「从来不算」（删除） | 写 |
| `add_material_card` | 给节点加一张资料卡：标题、详情、生效/失效各挑一个场景或事件 | 写 |
| `update_material_card` / `delete_material_card` | 改一张资料卡（只改给了的字段）；删一张卡，图片一起走 | 写 |
| `add_material_image` / `delete_material_image` | 给资料卡加图（拷进工程，可带说明）；删掉某张图 | 写 |
| `revise_node` | 修订节点的名称、类型、别名或说明（立刻生效） | 写 |
| `attach_scenes` | 把一个或多个场景挂到大事件下面（立刻生效；场景原有归属会被替换） | 写 |
| `attach_scenes` | 把一个或多个场景挂到大事件下面（立刻生效；场景原有归属会被替换） | 写 |
| `merge_properties` / `rename_property` | 两个名字合成一个方面；一个方面改名 | 写 |
| `categorize_records` | 把记录移到某个显示大类 | 写 |
| `accept_candidate` / `reject_candidate` | 处理候选（节点/属性/提取结果） | 写 |
| `import_source` | 导入一段原文（标题 + 正文），返回原文编号 | 写 |
| `extract_propose` | 提取第一步：模型读整章，出节点、事件、属性（只读原文） | 写 |
| `extract_review` | 提取第二步：拿库里已有的值对照提议，只丢确定的重复 | 写 |
| `extract_build` | 提取第三步：落成对象与记录，**只算不写**；编号、锚点、取代都在这里定 | 读 |
| `extract_commit` | 提取第四步：把落好的结果变成一个待审候选 | 写 |
| `extract_source` | 用工程配置的模型提取一篇原文；`commit=true` 同时生成待审候选 | 写 |

## 提取四步

提取拆成四个工具，因为前两步要调模型（慢、要钱、可能失败），后两步是纯函数：

| 步 | 工具 | 做什么 |
|---|---|---|
| 1 | `extract_propose` | 模型读整章，出节点、事件、属性；引文逐字校验，结果落在 `<工程>_extraction/<原文>.json` |
| 2 | `extract_review` | 拿库里已有的值对照，只丢确定的重复 |
| 3 | `extract_build` | 落成对象与记录——**只算不写**，编号、锚点、取代都在这里定，返回诊断 |
| 4 | `extract_commit` | 变成一个待审候选（图要人接受才变） |

`extract_source` 是四步连跑，不是第二套实现。

写操作遵守界面里那套规则，不是另一套：

- 记录不原地改。更正 = 追加正确的 + 撤回错的，两半在同一个候选里。
- 补录会立刻生效，并把同方面更早的值从这次披露起标为失效。
- 记录不物理删除：库里 append-only，所谓「失效」「删除」都只是不再被投影读到。
- 两句话分得开：`retract_properties` 带 `event_id` 是「从这一步起不算」，省略是「从来不算」——
  后者就是界面上的「删除」按钮。
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
narrative moments --project P            # 分级阅读位置：场景 → 事件
narrative material 7 --project P --cut 34

# 编辑
narrative add-property 7 state 神色平静 164 --project P
narrative correct-property value_abc123 三十一岁 --project P --reason 抄错了
narrative retract --ids value_abc123 --reason 这句读错了 --project P
narrative revise-node 2 --name 沈砚 --project P

# 资料卡（图片拷进工程，不引用原路径）
narrative add-material 7 青衫少年 --detail 洗得发白的青衫 --from E3 --project P
narrative add-material-image 7 <资料编号> ./ref.png --caption 正面 --project P
narrative material 7 --project P --cut E5
narrative delete-material 7 <资料编号> --project P
narrative import-source 第一章 --file ch1.txt --project P   # 长文本走文件
narrative categories --project P
# 提取分四步：每一步都能单独跑，产物落在 <工程>_extraction/<原文编号>.json
narrative extract-propose <source_id> --project P   # 模型读整章
narrative extract-review  <source_id> --project P   # 对照库里的值
narrative extract-build   <source_id> --project P   # 只算不写：看会生成什么
narrative extract-commit  <source_id> --project P   # 变成待审候选
narrative extract <source_id> --commit --project P  # 一次跑完四步
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
