"""Extract a chapter's nodes and events by reading the text directly.

GraphRAG is not in this path. The model reads the whole chapter and returns the
nodes the data model needs — people, groups, items, locations, scenes, events —
each pinned to exact quotes. Every quote is verified against the source before
anything reaches a candidate. Nothing is written to a project here.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from .harness import HarnessReply, HarnessRunner, HarnessSettings
from .order_ids import allocate, order_key, prefix_for
from .property_categories import aspect_of
from .property_labels import STORYLINE_PROGRESS
from .property_store import record_position

# 提示词改一次，这个号跟着动；缓存的提取结果里会写下来，便于对照。
PROMPT_VERSION = "extract/17"

NODE_TYPES = ("person", "group", "item", "location", "scene", "event", "storyline")

PROMPT = """你在为一部小说的叙事数据库提取「章节结构」。

下面是《{title}》全文，共 {chars} 字。

任务：按原文顺序，提取这一章出现的**全部节点**和**发生的事件**。

节点类型（type 只能取这六种）：
- person：人物。有名有姓、有称呼、被当作个体对待的。
- group：群体或组织。公司、门派、势力、家族。
- item：物品。法宝、武器、服饰、日常物件。
- location：地点。城市、星球、房间、湖、战场等可指认的空间。
- scene：场景。一段在同一地点、连续时间内发生的叙事片段，通常由地点变化或时间跳跃分隔。
  **每个场景都必须用 `storyline` 字段挂到一个大事件下面**（见下一节）。
- storyline：大事件。跨多个场景、**甚至跨章**的一段完整过程，有起因有结果。
  大事件常常到结尾才有名字：过程里先给一个**暂称**（`provisional_name`），收尾那一步再定正式名
  （`name`）。读者读到跨度中间时看到的是暂称，读完之后才看到正式名。没把握就只写 name。
- event：事件。**改变了什么的一次发生**——状态、关系、地点、目标或认知发生了变化。
  层级见下面的「四个层级」：动作是事件里的细节，不单独建节点。

## 四个层级：大事件 → 场景 → 事件 → 动作

原文是一层套一层的。判断某个东西该放哪一层，看它「改变了什么」的范围有多大：

- **大事件**（storyline 节点）：跨多个场景、甚至跨章的一段完整过程，有起因有结果
  （「老周把林舟带进另一个世界」）。它下面挂的是**场景**，而且**每个场景都要有归属**。
  一章里这段过程还没走完、或者看不出边界，就建一个用**暂称**的大事件——后面的章节会
  把它补完、定下正式名。不要为了凑一个节点把半截故事硬切出来，但也不要让场景悬空。
- **场景**（scene 节点）：同一地点、连续时间的一小段。地点变了、时间跳了，就是新场景。
- **事件**（event 节点）：场景里**改变了什么**的一次发生——状态、关系、地点、目标或认知变了。
- **动作**（不建节点）：「接起电话」「转身离开」「喝彩」「破口大骂」「一拳砸碎石块」。
  动作是事件的细节，写进事件名里；只有它自己改变了什么，才升成事件。

判断方法：

- 一个场景里通常只有**几个**事件，其余都是动作；同一段连续动作（取出→戴上→试用）是**一个**事件。
- 事件名写「谁做了什么、结果如何」——比单个动作大，比「发生冲突」小。
- 战斗、争吵：整场属于同一个大事件，场景按地点/回合切，事件按「结果变了」切。
- 一章通常属于一个（或半个）大事件、几个场景、十几个事件。**这些数字只是参照，不要为了凑数拆。**
- 同一件事不要输出两条措辞不同的记录（例如「沈砚告知来电」和「沈砚转告来电」是同一条）。

  ✗「老周取出新衣」「林舟换上新衣」「林舟照镜子」→ ✓「老周为林舟换上新衣，林舟在镜中看到新形象」
  ✗「林舟接电话」「林舟取消会议」「林舟让司机返回」→ ✓「林舟接完电话后取消会议，赶回酒店」

## 说明性内容也是披露

法宝是什么、星球叫什么、人物有什么经历、修真界有什么规矩——这些段落没有动作，
但都是原文明确告诉读者的信息，**必须提取**，可以写成事件（「老周展示并解释纳芥手镯」），
也可以写成属性。不要因为"这一段没发生什么事"就跳过。
- 事件名称概括程度以「一句话能说清发生了什么」为准，不要细到单个动作，也不要粗到「发生冲突」。
- **每个场景至少要有一个事件。** 只列场景、不列事件是不合格的输出。
- 战斗、争吵这类连续过程，**整场算一个事件**；只有结果真的变了（谁受伤、谁被制住、法宝被破）
  才在同一场景里再记一条。

规则：
1. 名称用原文用词。人物用原文最常见的称呼（例如「林舟」而不是「林总」），其他称呼放进 aliases。
2. 每个节点给 1-2 段原文引文，必须**逐字复制**：不改字、不改标点、不跨段拼接、不加省略号。
   引文是证据，程序会逐字比对；对不上的会被丢弃。
3. 事件的 participants 写原文出现的人物、物品、地点名称；只写真正参与这个动作的。
4. 事件归属到一个 scene（填 scene 字段，写场景名称）。scene 必须是本章已列出的场景。
5. 只写原文明确写出的内容。不推断，不补心理活动，不补因果。
6. 背景设定、外貌描写本身不是节点；但如果它引入了一个具体对象（例如某件法宝、某个地名），
   那个对象就是节点。
7. 节点按首次出现的位置排列，事件按发生顺序排列。

另外提取「属性与状态」：

- 人物的明确属性：年龄、身材、外貌、身份、状态、伤情。
- 物品/地点的状态：持有者、位置、是否损坏、外形。
- **物品必须有一条 `持有者`**，`value_type` 一律写 `either`（可以指向人物，也可以是普通字符串）：
  - 原文能看出在谁手上（拿着、戴着、穿着、收进怀里、被谁给出、被谁抢走）→ 值填**人物名**；
  - 明确没有主人（被遗弃、无主之物）→ 值写 `无`；
  - 原文没说清在谁手里 → 值写 `未知`。
  换手时再记一条，位置照常是那次披露。**不要因为不确定就不写这一条。**
  例：「老周从怀里掏出一把铜钥匙」→ `铜钥匙.持有者 = 老周`；
      「林舟接过钥匙揣进兜里」→ 再记一条 `铜钥匙.持有者 = 林舟`；
      「废墟里散落着几件无主法器」→ `法器.持有者 = 无`。
- 信息来自某个人的视角时，用 `scope` 标明归属，`holder` 填那个人：
  - `world`：原文直接叙述的内容（默认，不填 holder）；
  - `cognition`：某个人怎样理解、相信、知道、记得或怀疑。**"知道"也归这一类**，
    原文的确定程度照抄进 value（"知道老周赢了"就记 `value: "老周赢了"`，不要改成"确定"）；
  - `claim`：某个人对外说出的内容（口头、信件、公告），holder 填说的人。
  三种之间**不自动转换**：说了不等于相信，相信不等于世界成立。
  **叙述者讲的就是 `world`，哪怕句子里带「发现」「知道」「感觉」**：发现是叙述，值写的是
  被发现的那个状态（「他发现自己变高了」→ `身高 = 变高了`、`world`）。只有原文明确转述
  某人的想法、猜测、记忆或说法时，才用 `cognition` / `claim` 并填 holder。
- `source` 记信息是怎么表达出来的：`narration`（叙述）、`speech`（人物发言）、
  `thought`（人物想法）、`observation`（人物观察）、`document`（书信碑文等载体）。拿不准就留空。
- `related_nodes` 填原文明示涉及的其他节点（观察者、被归因的人、信息载体），
  **不要填 holder**——holder 已经由 scope 那一栏说明。
- **每个场景给一条 `storyline_progress`**：一句话概括「这个大事件进行到这个场景时，
  读者已经知道的部分」。**`node` 写场景名**，不是地点名、不是人物名——场景是这一层
  唯一带属性的东西，写错地方这句话就挂不到大事件上。只写这一步新走出来的东西，
  不要把上一场说过的再抄一遍。
  例：第一场 `"老周找上门，说林舟得跟他走"`，第二场 `"两人到了旧车站，铁门还没开"`。
  这几个名字不是必须一致——读者看到的本来就是「到这里为止」的样子。
- **大事件自己不写属性**：结局、结果、状态都不写。它的样子由各个场景的
  `storyline_progress` 攒出来，写完最后一场才完整。
- **事件不写属性**：事件名已经说了发生了什么。状态、身份、持有者、位置这些记在
  人物／物品／地点／场景上，不要挂在事件节点上。
- 每条属性必须有原文引文，只写原文明确写出的。
- property 用英文小写标识，命名的是**被说的那个方面**（age、skin、height、holder、injury、
  statement、plan），不要用中文。
  **不要用"变化"命名**：不写 `height_change`、`appearance_change`、`skin_change`。
  原文说"变高了"，就记 `height = 变高了`；说"白了许多"，就记 `skin = 白了许多`。
- value_type 取 string、number、boolean、node、either、any；holder 这类指向节点的属性用 node，
  value 填节点名称。
  **同一个属性在不同时候可以是数值、描述或相对说法，不要因为形态不同就换个名字。**
  比如身高：`height = 180`、`height = 矮小`、`height = 变高了` 都是 `height`，
  这种属性 `value_type` 写 `any`。
- **每披露一次就记一条，按披露顺序排列。** 一条新记录本身就代表这次披露（可能是首次、
  确认、或改口），不需要另建"变化记录"，也不需要标注"这是变化还是状态"。
  值可以是数值（180）、描述（矮小）、相对说法（变高了）、或带不确定语气（似乎更差）。
  不要只记最终状态，也不要因为"和前面差不多"就跳过。
## 属性命名与去重（四类常见错误）

- **一个方面只用一个名字。** 身高只写 `height`，不要再出现 `stature`、`body_height`、
  `height_cm`；外貌只写 `appearance`，不要再写 `look`、`face_description`。
  写之前先看本章是否已经用过某个名字，用过就沿用。
- **同一段原文的同一个方面只记一条。** 句子里提了两次、或既有数值又有描述，
  都合并成一条，值照原文写；不要拆成两条记录。
- **数值和描述共用一个属性名。** `height = 180`、`height = 矮小`、`height = 变高了`
  都是 `height`；带单位直接写进值里（`"180 公分"`），不要另起 `height_cm`。
- **宽窄不要混用。** 一句话说的是具体方面（肤色、身高、发型）就记到那个名字上，不要塞进
  「外貌」这种笼统名字；「外貌」只留给整段的整体评价（「风流潇洒」「像十八九岁的大男孩」）。
  一句话里既有整体又有局部，各记一条——否则同一个方面在两条不同的线上，后文改口时旧值
  永远显得「还没被取代」。
- **属性名用中文短语**：`身份`、`持有者`、`修为境界`、`兵力`。不要造 `identity`、`holder`
  这种英文标识符——界面上会和中文混在一起。**「已有属性名」里出现过的名字照旧沿用**
  （包括里面的英文名），同一方面写两个名字等于两条线。
- **矛盾说法各自保留，但必须标来源。** 两个人对同一件事说法不同时，记成两条
  不同 `scope`/`holder` 的记录；不要为了"一致"丢掉其中一条，也不要都写成 `world`。
- **过时的说法不删。** 后文改口时新记录照常追加，旧记录保留——程序按披露顺序
  取最后一条作为当前值，历史留给读者回查。
- **提到就要记。** 原文明确说了某人的年龄、身份、伤势、持有物、所在地，
  哪怕一句话带过也要记一条属性，不要只记事件。

- 每条给 `at_event`：写**原文披露这个值时所处的事件**名称（必须是本章列出的事件）。
  它是读者读到哪里才知道这件事的位置，**不要求这个事件导致属性变化**。
  一段对话里说出年龄，`at_event` 就是那段对话所在的事件；读者此前不知道，到这里才知道。

## 属性分类（展示用）

每条属性再给一个 `category`，从该节点类型的分类表里选一个；**拿不准就写「其他」**，
照常保存，不影响取值。分类只决定这条记录显示在哪个大类下，不改变属性名，也不要求拆分属性。
**分类名照抄表里的写法**（简体、一字不改）：原文是繁体也照抄，写成「實力與資源」会认不出来，
只能落到「其他」。

人物：基本信息 / 外貌 / 身份与经历 / 能力与身体状态 / 持有与使用 / 关系 / 心理与意图 / 其他
- 基本信息：姓名、别名、性别、年龄、种族、籍贯
- 外貌：身高、体型、肤色、面容、发型、穿着
- 身份与经历：职业、职务、称号、组织身份、出身、教育经历
- 能力与身体状态：修为、技能、天赋、体质、伤势、健康
- 持有与使用：财物、装备、携带物、佩戴物
- 关系：亲属、师徒、朋友、敌对、隶属。**属性名写具体的关系**（师徒 / 父子 / 恋人 / 仇敌 /
  上司 / 下属），值指向对方那个人；不要把「关系」本身当属性名，也不要把关系的描述写成
  一句话（「与沈砚是多年的恋人」）——那是句子，不是属性
- 心理与意图：情绪、愿望、计划、打算、态度

物品：基本信息 / 外观与构造 / 功能与特性 / 状态 / 归属与持有 / 关联 / 其他
- 基本信息：名称、别名、种类、来源
- 外观与构造：颜色、形状、尺寸、材质、结构
- 功能与特性：用途、效果、能力、使用条件、限制
- 状态：完好、破损、耗尽、启动、封存
- 归属与持有：所有者、持有人、使用者
- 关联：组成、容纳、配套

群体：基本信息 / 构成与规模 / 实力与资源 / 立场与关系 / 状态 / 其他
地点：基本信息 / 环境与外观 / 位置与范围 / 势力与居住者 / 状态 / 其他
事件：基本信息 / 参与者 / 过程与结果 / 其他
场景：基本信息 / 内容 / 其他

`state`、`status` 这种宽泛的名字也要给分类，**同一个属性名在不同记录里可以归不同大类**：
`state = 衣服破烂` 归外貌，`state = 身受重伤` 归能力与身体状态，`state = 心情烦躁` 归心理与意图。
拿不准就写「其他」，不要为了分类把一条记录拆成两条。

## 已有属性名（优先沿用）

{known}

输出一个 JSON 对象，不要任何解释文字。**`nodes` 和 `properties` 是两个并列的数组**：
节点进 `nodes`，属性进 `properties`，属性行（`node` / `property` / `value`）不要塞进 `nodes`。

{{"nodes": [
  {{"name": "林舟", "type": "person", "quotes": ["原文逐字引文"], "aliases": ["林总"]}},
  {{"name": "站前旅社", "type": "location", "quotes": ["原文逐字引文"], "aliases": []}},
  {{"name": "撞破女友与陈默", "type": "scene", "quotes": ["原文逐字引文"], "aliases": [],
    "storyline": "林舟发现自己被背叛"}},
  {{"name": "林舟推开房门撞见女友与陈默", "type": "event", "quotes": ["原文逐字引文"],
    "aliases": [], "participants": ["林舟", "陈默", "女友"], "scene": "撞破女友与陈默"}},
  {{"name": "林舟用水果刀刺伤陈默", "type": "event", "quotes": ["原文逐字引文"],
    "aliases": [], "participants": ["林舟", "陈默", "水果刀"], "scene": "撞破女友与陈默"}}
],
 "properties": [
  {{"node": "林舟", "property": "年龄", "value": 29, "value_type": "number",
    "category": "基本信息",
    "quotes": ["原文逐字引文"], "scope": "world", "holder": null,
    "source": "narration", "related_nodes": [], "at_event": "沈砚告知林舟陈总来电"}},
  {{"node": "铜钥匙", "property": "持有者", "value": "林舟", "value_type": "either",
    "category": "归属与持有",
    "quotes": ["原文逐字引文"], "scope": "world", "holder": null,
    "source": "narration", "related_nodes": ["老周"],
    "at_event": "老周赠送铜钥匙，林舟戴上试用"}},
  {{"node": "断剑", "property": "持有者", "value": "未知", "value_type": "either",
    "category": "归属与持有",
    "quotes": ["原文逐字引文"], "scope": "world", "holder": null,
    "source": "narration", "related_nodes": [],
    "at_event": "废墟里散落着断剑与破甲"}},
  {{"node": "老周", "property": "状态", "value": "赢了那场交战", "value_type": "string",
    "category": "其他",
    "quotes": ["原文逐字引文"], "scope": "cognition", "holder": "林舟",
    "source": "thought", "related_nodes": ["苏娘"],
    "at_event": "苏娘施展落英阵，老周以铁尺破解"}},
  {{"node": "雨夜的门口", "property": "storyline_progress",
    "value": "老周找上门，说林舟得跟他走", "value_type": "string",
    "category": "大事件",
    "quotes": ["原文逐字引文"], "scope": "world", "holder": null,
    "source": "narration", "related_nodes": [],
    "at_event": "林舟接过铜钥匙"}}
]}}

章节正文：

{text}
"""


def _known_property_lines(known: list[dict] | None) -> str:
    """The names already in the project, so a new chapter reuses them."""
    if not known:
        return "（本工程还没有属性名，这是第一批；命名时注意一个方面只用一个名字。）"
    labels = {
        "person": "人物", "group": "群体", "item": "物品",
        "location": "地点", "event": "事件", "scene": "场景",
    }
    lines = []
    for item in known:
        node_type = labels.get(item.get("node_type") or "", item.get("node_type") or "未分类")
        category = item.get("category") or "其他"
        lines.append(f"- `{item['property']}`（{node_type} · {category}）")
    return "\n".join(lines)


def build_prompt(title: str, text: str, known: list[dict] | None = None) -> str:
    return PROMPT.format(
        title=title, chars=len(text), text=text, known=_known_property_lines(known)
    )


def _strip_fence(raw: str) -> str:
    body = raw.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    return body.strip()


def _payload(raw: str) -> dict:
    body = _strip_fence(raw)
    # strict=False 允许字符串里出现裸换行/制表符：中文原文里偶尔会带，严格模式会整段拒收。
    for candidate in (body, body[body.find("{"):body.rfind("}") + 1] if "{" in body else ""):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            try:
                return json.loads(candidate, strict=False)
            except json.JSONDecodeError:
                continue
    if "{" not in body:
        raise ValueError("模型回复里没有 JSON 对象")
    raise ValueError("模型回复里的 JSON 解析不了")


VALUE_TYPES = ("string", "number", "boolean", "node", "any", "either")
ALLOWED_PARTICIPANTS = ("person", "group", "item", "location")


def _parse_properties(rows, text: str, names: set[str], rejected: list[dict]) -> list[dict]:
    """Verify property observations the same way as nodes: exact quote, known owner."""
    if not isinstance(rows, list):
        return []
    properties: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        node_name = str(row.get("node") or "").strip()
        property_id = str(row.get("property") or "").strip()
        value_type = str(row.get("value_type") or "string").strip()
        if not node_name or not property_id:
            continue
        if node_name not in names:
            rejected.append({"name": f"{node_name}.{property_id}", "reason": "属性所属节点不在本批"})
            continue
        if value_type not in VALUE_TYPES:
            rejected.append({"name": f"{node_name}.{property_id}", "reason": f"值类型无效：{value_type}"})
            continue
        quotes = [str(item) for item in (row.get("quotes") or []) if str(item).strip()]
        good = []
        for quote in quotes:
            index, found = _locate(text, quote)
            if index < 0:
                rejected.append({"name": f"{node_name}.{property_id}", "reason": "引文不在原文中",
                                 "quote": quote[:60]})
                continue
            good.append({"quote": found, "start": index})
        if not good:
            rejected.append({"name": f"{node_name}.{property_id}", "reason": "没有可用的原文引文"})
            continue
        good.sort(key=lambda item: item["start"])
        raw_value = row.get("value")
        if value_type == "number":
            if not isinstance(raw_value, (int, float, str)):
                rejected.append({"name": f"{node_name}.{property_id}", "reason": "数值无法解析"})
                continue
            try:
                value = int(raw_value)
            except (TypeError, ValueError):
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    rejected.append({"name": f"{node_name}.{property_id}", "reason": "数值无法解析"})
                    continue
        elif value_type == "boolean":
            value = bool(raw_value)
        else:
            value = str(raw_value if raw_value is not None else "").strip()
        if value_type == "node" and value not in names:
            rejected.append({"name": f"{node_name}.{property_id}", "reason": "属性值指向的节点不在本批"})
            continue
        properties.append({
            "node": node_name,
            "property": property_id,
            "value": value,
            "value_type": value_type,
            "start": good[0]["start"],
            "quotes": [item["quote"] for item in good],
            "scope": str(row.get("scope") or "world").strip(),
            "holder": str(row.get("holder") or "").strip(),
            "at_event": str(row.get("at_event") or "").strip(),
            "source": str(row.get("source") or "").strip(),
            # A display grouping, not a fact. Empty is allowed and means 其他.
            "category": str(row.get("category") or "").strip(),
            "related_nodes": [
                str(item).strip()
                for item in (row.get("related_nodes") or [])
                if str(item).strip()
            ],
        })
    properties.sort(key=lambda item: item["start"])
    return properties


WRAPPING_QUOTES = "\"'\u2018\u2019\u201c\u201d\u300c\u300d\u300e\u300f"


def _locate(text: str, quote: str) -> tuple[int, str]:
    """找到引文在原文里的位置，返回 (下标, 实际用的引文)。

    先按原样找；找不到就只去掉**首尾的引号**再试一次——模型经常把整句再套一层
    引号（`“他说‘火纹石改过外形’”` 多套了一层），多出来的是排版不是内容。只剥首尾、
    不动中间，所以「逐字对上」这条并没有松。
    """
    index = text.find(quote)
    if index >= 0:
        return index, quote
    trimmed = quote.strip().strip(WRAPPING_QUOTES).strip()
    if trimmed and trimmed != quote:
        index = text.find(trimmed)
        if index >= 0:
            return index, trimmed
    return -1, quote


def parse_nodes(raw: str, text: str) -> dict:
    """Verify every quote and pin each node to its first appearance.

    A node survives only if at least one quote is a literal substring of the
    source; rejected quotes are reported with the reason instead of dropped
    silently.
    """
    payload = _payload(raw)
    rows = payload.get("nodes")
    if not isinstance(rows, list):
        raise ValueError("模型回复缺少 nodes 数组")

    # 模型偶尔把属性行写进 `nodes` 里（长章节更容易）：一行有 node/property 没有 type，
    # 那就是属性，不是节点。按**形状**认，不按数组位置认——否则这些行会被当成
    # 「类型无效」丢掉，整章的属性、关系、大事件进展一起消失，还不报错。
    strays: list[dict] = []
    kept_rows: list = []
    for row in rows:
        if (
            isinstance(row, dict)
            and not str(row.get("type") or "").strip()
            and str(row.get("node") or "").strip()
            and str(row.get("property") or "").strip()
        ):
            strays.append(row)
        else:
            kept_rows.append(row)
    rows = kept_rows

    nodes: list[dict] = []
    rejected: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        node_type = str(row.get("type") or "").strip()
        if node_type not in NODE_TYPES:
            rejected.append({"name": name, "reason": f"类型无效：{node_type}"})
            continue
        quotes = [str(item) for item in (row.get("quotes") or []) if str(item).strip()]
        good: list[dict] = []
        for quote in quotes:
            index, found = _locate(text, quote)
            if index < 0:
                rejected.append({"name": name, "reason": "引文不在原文中", "quote": quote[:60]})
                continue
            good.append({"quote": found, "start": index})
        if not name or not good:
            if name:
                rejected.append({"name": name, "reason": "没有可用的原文引文"})
            continue
        good.sort(key=lambda item: item["start"])
        participants = [
            str(item).strip() for item in (row.get("participants") or []) if str(item).strip()
        ]
        nodes.append({
            "name": name,
            "type": node_type,
            "start": good[0]["start"],
            "quotes": [item["quote"] for item in good],
            "aliases": [
                str(item).strip() for item in (row.get("aliases") or []) if str(item).strip()
            ],
            "participants": participants,
            "participants_not_in_text": [item for item in participants if item not in text],
            "scene": str(row.get("scene") or "").strip(),
            # 场景归属的大事件（可选）：大事件跨场景、甚至跨章。
            "storyline": str(row.get("storyline") or "").strip(),
            # 大事件在过程中还没定名时的叫法。
            # 大事件在过程中还没定名时的叫法。
            "provisional_name": str(row.get("provisional_name") or "").strip(),
        })

    # Reading order inside each type, then grouped for display.
    nodes.sort(key=lambda item: (item["start"], item["name"]))

    grouped: dict[str, list[dict]] = {node_type: [] for node_type in NODE_TYPES}
    for node in nodes:
        grouped[node["type"]].append(node)
    declared = payload.get("properties")
    declared = declared if isinstance(declared, list) else []
    if strays:
        declared = declared + strays
    properties = _parse_properties(declared, text, {item["name"] for item in nodes}, rejected)
    scenes = {item["name"] for item in grouped["scene"]}
    for event in grouped["event"]:
        if event["scene"] and event["scene"] not in scenes:
            event["scene_not_found"] = True
    storylines = {item["name"] for item in grouped["storyline"]}
    for scene in grouped["scene"]:
        if scene["storyline"] and scene["storyline"] not in storylines:
            scene["storyline_not_found"] = True
    return {
        "nodes": nodes,
        "properties": properties,
        "grouped": grouped,
        "counts": {node_type: len(items) for node_type, items in grouped.items()},
        "rejected": rejected,
    }


def known_properties(ops) -> list[dict]:
    """Property names already in the project, with the type they are used on.

    Handing these back to the model is what keeps one aspect from acquiring three
    names across chapters. It is context, not a closed list: a new aspect still
    gets a new name.
    """
    from . import property_categories

    snapshot = ops.graph_snapshot()
    nodes = {node["id"]: node for node in snapshot.get("nodes", [])}
    config = ops.property_categories()
    # Names in use are the canonical ones: a name that was merged or renamed away
    # must not be handed back to the model, or the next chapter reintroduces it.
    aliases = ops.property_aliases()
    seen: dict[tuple[str, str], int] = {}
    for record in ops.property_records():
        if record.get("kind") != "property_value":
            continue
        node_type = (nodes.get(record.get("node_id")) or {}).get("node_type") or "other"
        name = record.get("property_id") or ""
        key = (aliases.get(name, name), node_type)
        seen[key] = seen.get(key, 0) + 1
    return [
        {
            "property": property_id,
            "node_type": node_type,
            "category": property_categories.suggest(property_id, node_type, None, config)[0],
            "count": count,
        }
        for (property_id, node_type), count in sorted(seen.items())
        if property_id
    ]


def read_chapter(
    title: str,
    text: str,
    *,
    settings: HarnessSettings,
    api_key: str,
    workdir: Path,
    ops=None,
) -> dict:
    """第一轮：整章读。不带库里的旧值，只认原文。

    返回校验过的提议（节点、属性、被丢弃的引文）。原始响应和元数据落在
    `workdir` 里，随时可以回看模型到底说了什么。
    """
    runner = HarnessRunner(settings, api_key, workdir)
    known = known_properties(ops) if ops is not None else None
    session = f"extract-{uuid4().hex}"
    raws: list[str] = []

    def _check(reply: HarnessReply) -> None:
        """空回复 + 服务端报错时，把真正的原因说出来，别只说「没有 JSON」。"""
        if reply.raw.strip():
            return
        problem = str(reply.metadata.get("service_error") or "").strip()
        raise ValueError(f"模型没有返回内容：{problem or '空回复'}")

    try:
        reply: HarnessReply = runner.agent_turn(build_prompt(title, text, known), session)
        raws.append(reply.raw)
        _check(reply)
        # 先落盘再解析：解析失败时，磁盘上留着的必须是**这次**的回复。
        (workdir / "raw.txt").write_text(reply.raw, encoding="utf-8")
        try:
            result = parse_nodes(reply.raw, text)
        except ValueError:
            # 一次重试：让它把完整的 JSON 再输出一遍，别把整章丢掉。
            reply = runner.agent_turn(
                "上一条回复不是完整的 JSON（可能被截断或混了说明）。"
                "请重新输出那个 JSON 对象：只输出 JSON，不要解释，不要代码块。",
                session,
            )
            raws.append(reply.raw)
            _check(reply)
            result = parse_nodes(reply.raw, text)
    finally:
        runner.close()
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "raw.txt").write_text(reply.raw, encoding="utf-8")
    if len(raws) > 1:
        (workdir / "raw-attempts.json").write_text(
            json.dumps(raws, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (workdir / "metadata.json").write_text(
        json.dumps(reply.metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result.update({
        "chapter": title,
        "prompt_version": PROMPT_VERSION,
        "characters": len(text),
        "model": settings.model,
        "provider": settings.provider,
        "usage": reply.metadata,
        "raw": reply.raw,
    })
    return result


def review_chapter(
    ops,
    data: dict,
    *,
    settings: HarnessSettings,
    api_key: str,
    workdir: Path,
) -> dict:
    """第二、三轮：拿库里已有的值对照，只丢确定的重复。

    这一步是「对照」，不是「再读一遍」：它看的是第一轮解析出来的提议，加上这些
    属性在库里已经记录的值。旧值失效（`supersede`）也在这里给，能不能落实由
    `build_candidate` 判断。
    """
    if not data.get("properties"):
        return data
    from .extraction_review import review_properties

    try:
        reviewed = review_properties(
            ops, data, settings=settings, api_key=api_key, workdir=workdir
        )
    except ValueError as exc:
        # 复核没跑通（模型回了段话、或反复不给 JSON）：本章照常落图，把失败记下来。
        # 宁可少一道对照，也不要整章作废。
        result = dict(data)
        result["review"] = {
            "dropped": [], "unresolved": [], "rounds": 0,
            "version": "review/6", "error": str(exc),
        }
        return result
    result = dict(data)
    result["properties"] = reviewed["properties"]
    # 旧值是不是真的失效，要等 `build_candidate` 把它们解析成记录和事件；
    # 解析不出来的会被丢掉。
    result["supersede"] = reviewed.get("supersede") or []
    # 关系变了：模型点名要撤的记录，跟着这一批一起落（按记录编号撤）。
    result["retire"] = reviewed.get("retire") or []
    from .extraction_review import REVIEW_VERSION

    result["review"] = {
        "dropped": reviewed["dropped"],
        "unresolved": reviewed.get("unresolved") or [],
        "rounds": reviewed["rounds"],
        # 复核提示词也带版本：两次提取的差别要能分辨是「读」还是「对」变了。
        "version": REVIEW_VERSION,
    }
    return result


def extract_chapter(
    title: str,
    text: str,
    *,
    settings: HarnessSettings,
    api_key: str,
    workdir: Path,
    ops=None,
) -> dict:
    """一轮读完再加对照：`read_chapter` + `review_chapter` 的组合。

    分步的版本在工具面里（`extract_propose` / `extract_review`），两边调的是
    同一对函数，不会各说一套。
    """
    result = read_chapter(
        title, text, settings=settings, api_key=api_key, workdir=workdir, ops=ops
    )
    if ops is None:
        return result
    return review_chapter(
        ops, result, settings=settings, api_key=api_key, workdir=workdir
    )


def _checkpoint_at(start: int, checkpoints: list[tuple[int, int, str]]) -> str | None:
    """The event whose reading segment contains this position.

    Prefer the event whose quote covers the position; otherwise the nearest one
    *before* it, because the information appears in that event's segment; before
    the chapter's first event it belongs to the opening segment. Text after the
    last event hangs on that last event rather than being dropped.

    The result is a reading anchor. It says nothing about when the value holds in
    the story — a value learned in a flashback is disclosed here, not there.
    """
    if not checkpoints:
        return None
    for begin, end, event_id in checkpoints:
        if begin <= start <= end:
            return event_id
    preceding = [item for item in checkpoints if item[0] <= start]
    if preceding:
        return max(preceding, key=lambda item: item[0])[2]
    return min(checkpoints, key=lambda item: item[0])[2]


def _squash(text: str) -> str:
    """只留汉字、字母和数字：标点不同不该影响「说的是不是同一个事件」。"""
    return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _anchor_for(name: str, event_by_name: dict[str, str]) -> tuple[str | None, str]:
    """把模型写的 at_event 对到本章真实的事件上，返回 (事件编号, 对上的方式)。

    **只认原样和「去掉标点后原样」两种**：判断两个名字像不像同一件事是语义判断，
    归模型；这里只做结构上的查表。对不上就返回 None，由复核轮去问模型要准确的名字，
    问不到就报出来——宁可不标失效，也不要锚到一个错的位置。
    """
    if name in event_by_name:
        return event_by_name[name], "exact"
    key = _squash(name)
    if not key:
        return None, ""
    for event, event_id in event_by_name.items():
        if _squash(event) == key:
            return event_id, "normalized"
    return None, ""


def _dedupe_retractions(records: list[dict], accepted: list[dict]) -> list[dict]:
    """一条记录只能撤一次。

    三个来源（复核判的、结构推的、模型点名的）都用「目标 + 事件」算编号，同一个目标
    被两个来源同时撤就会撞出同一个 id，预检直接判重复。这里按目标收成一条——复核判的
    留着（它说得清理由），其余丢掉；已经撤过的目标也不再重复撤。
    """
    already = {
        str(record.get("target_id")) for record in accepted
        if record.get("kind") == "property_retraction"
    }
    rank = {"review": 0, "audit": 1, "human": 2}
    best: dict[str, dict] = {}
    for record in records:
        if record.get("kind") != "property_retraction":
            continue
        target = str(record.get("target_id") or "")
        if not target or target in already:
            continue
        current = best.get(target)
        if current is None or rank.get(str(record.get("basis")), 3) < rank.get(
            str(current.get("basis")), 3
        ):
            best[target] = record
    keep = {id(record) for record in best.values()}
    return [
        record for record in records
        if record.get("kind") != "property_retraction" or id(record) in keep
    ]


def _supersede_retractions(
    ops, batch: list[dict], accepted: list[dict]
) -> list[dict]:
    """Older values this extraction replaces, as retraction records.

    The rule lives in `property_store.supersede_plan` so the audit, the
    extraction and a hand-written edit cannot disagree about what "superseded"
    means. Here it only becomes records, riding in the same candidate as the
    value that caused them — a value and its invalidation never land apart.
    """
    from .property_store import supersede_records

    return supersede_records(accepted, batch)


def _retire_by_id(
    data: dict, event_by_name: dict[str, str], skipped: list[dict]
) -> list[dict]:
    """按记录编号撤回：关系变化这类判断，模型能直接指出是哪一条变了。"""
    out: list[dict] = []
    for item in data.get("retire") or []:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target_id") or "")
        anchor, _how = _anchor_for(str(item.get("at_event") or ""), event_by_name)
        if not target or not anchor:
            skipped.append({
                "name": target or "（未指名）",
                "reason": f"关系变更锚点对不上本章事件：{item.get('at_event') or '（空）'}",
            })
            continue
        digest = hashlib.sha1(f"{target}:{anchor}".encode()).hexdigest()[:12]
        out.append({
            "kind": "property_retraction",
            "id": f"retract_{digest}",
            "target_id": target,
            "reason": str(item.get("reason") or "").strip() or "关系有变化",
            "event_id": anchor,
            "basis": "review",
        })
    return out


def _review_supersede(
    data: dict,
    records: list[dict],
    accepted: list[dict],
    node_ids: dict[str, str],
    event_by_name: dict[str, str],
    skipped: list[dict] | None = None,
) -> list[dict]:
    """Turn the review round's "this old value is over" into retraction records.

    Only two things are needed and both must be exact: the old record (node +
    aspect + the value as it was written) and the event where the reader learns it
    no longer holds. Anything that does not resolve is dropped — a guessed anchor
    would invent a reading position the text does not have.
    """
    items = data.get("supersede") or []
    skipped = skipped if skipped is not None else []
    if not items:
        return []
    marked = {
        record.get("target_id") for record in accepted + records
        if record.get("kind") == "property_retraction"
    }
    candidates: dict[tuple[str, str, str], list[dict]] = {}
    for record in accepted + records:
        if record.get("kind") != "property_value":
            continue
        key = (
            str(record.get("node_id")),
            aspect_of(record.get("property_id") or ""),
            str(record.get("value")),
        )
        candidates.setdefault(key, []).append(record)

    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        node_id = node_ids.get(str(item.get("node") or ""))
        anchor, how = _anchor_for(str(item.get("at_event") or ""), event_by_name)
        if not node_id or not anchor:
            skipped.append({
                "name": f"{item.get('node')}.{item.get('property')}",
                "reason": f"失效锚点对不上本章事件：{item.get('at_event') or '（空）'}",
            })
            continue
        key = (node_id, aspect_of(str(item.get("property") or "")), str(item.get("value")))
        found = candidates.get(key) or []
        if not found:
            continue
        # The latest such record is the one the claim is about.
        target = max(found, key=record_position)
        if target.get("id") in marked:
            continue
        digest = hashlib.sha1(f"{target.get('id')}:{anchor}".encode()).hexdigest()[:12]
        out.append({
            "kind": "property_retraction",
            "id": f"retract_{digest}",
            "target_id": target.get("id"),
            "reason": str(item.get("reason") or "").strip() or "本章之后不再成立",
            "event_id": anchor,
            # 这是模型判的，不是结构推的：basis 分开记，才看得出失效是谁发现的。
            "basis": "review",
        })
        marked.add(target.get("id"))
    return out


def build_candidate(ops, data: dict, *, accepted: set[str] | None = None) -> dict:
    """Turn one extraction result into graph objects + property records.

    Two orders are kept apart. The disclosure chain is derived from source
    positions and never stored. ``predecessors``/``successors`` hold only the
    relations the source states, so a missing edge means "not established yet",
    not "comes next in the text". Property values are anchored to the disclosure
    checkpoint where the reader learns them.
    """
    snapshot = ops.graph_snapshot()
    # Ids are disclosure positions, so a new mention needs to know where it sits
    # relative to the nodes that already exist.
    orders = {
        row["source_id"]: row["ordinal"] for row in snapshot.get("source_orders", []) or []
    }
    current_ordinal = orders.get(data["source_id"]) or (
        max((int(value) for value in orders.values()), default=0) + 1
    )
    # 每种节点各有自己的编号序列：人从 P1 开始，物品从 I1 开始，互不相关。
    # 所以位置表和占用表都按类型分开——两个类型共用一本流水号就不是两套编号了。
    positions: dict[str, list[tuple[tuple[int, int], str]]] = {}
    taken: dict[str, set[str]] = {}
    for node in snapshot["nodes"]:
        node_type = str(node.get("node_type") or "")
        taken.setdefault(node_type, set()).add(node["id"])
        disclosure = node.get("disclosure")
        if isinstance(disclosure, dict) and isinstance(disclosure.get("start"), int):
            positions.setdefault(node_type, []).append((
                (int(orders.get(disclosure.get("source_id"), 99)), disclosure["start"]),
                node["id"],
            ))

    existing: dict[tuple[str, str], str] = {}
    name_counts: dict[tuple[str, str], int] = {}
    for node in snapshot["nodes"]:
        if node.get("retracted"):
            continue
        key = (node["node_type"], node["name"])
        existing.setdefault(key, node["id"])
        name_counts[key] = name_counts.get(key, 0) + 1
    selected = accepted if accepted is not None else {item["name"] for item in data["nodes"]}

    # A shared name is not an identity. Entries collapse only when the type, the
    # name *and* the sentence all match — that is the model repeating itself,
    # not two different deeds that happen to be called the same thing.
    unique: dict[tuple[str, str, str], dict] = {}
    for item in data["nodes"]:
        if item["name"] not in selected:
            continue
        quote = item["quotes"][0] if item["quotes"] else ""
        key = (item["type"], item["name"], quote)
        if key in unique:
            merged = unique[key]
            for extra in item["quotes"]:
                if extra not in merged["quotes"]:
                    merged["quotes"].append(extra)
            continue
        unique[key] = dict(item)
    nodes = list(unique.values())
    by_name: dict[str, dict] = {}
    for item in nodes:
        by_name.setdefault(item["name"], item)
    ids: dict[tuple[str, str], str] = {}
    # Two mentions can share a name; the sentence is what keeps them apart, so
    # each mention gets its own id while references still resolve by name.
    mention_ids: dict[tuple[str, str, str], str] = {}
    objects: list[dict] = []
    identity_candidates: list[dict] = []

    event_items = [item for item in nodes if item["type"] == "event"]
    event_items.sort(key=lambda item: item["start"])

    # Resolve every id first: an event may reference a node that comes later.
    # Entities keep their identity across chapters; events do not, because two
    # scenes may legitimately narrate two different deeds under one name.
    # One extraction record is identified by what it says *and* where it says it:
    # the same sentence can legitimately appear twice in a book.
    known_by_quote = {
        (
            item["node_type"], item["name"],
            (item.get("evidence") or {}).get("quote"),
            (item.get("disclosure") or {}).get("source_id"),
            (item.get("disclosure") or {}).get("start"),
        ): item["id"]
        for item in snapshot["nodes"]
        if not item.get("retracted")
    }
    for item in nodes:
        key = (item["type"], item["name"])
        quote = item["quotes"][0] if item["quotes"] else ""
        mention = (item["type"], item["name"], quote)
        record_key = (item["type"], item["name"], quote, data["source_id"], item["start"])
        if record_key in known_by_quote:
            mention_ids[mention] = known_by_quote[record_key]
            ids.setdefault(key, mention_ids[mention])
            continue
        # Reuse an entity only when the project holds exactly one node of that
        # name and type. Once there are several, the name proves nothing, so the
        # new mention stays separate until someone confirms the identity.
        reused = (
            existing.get(key)
            if item["type"] != "event" and name_counts.get(key, 0) == 1
            else None
        )
        if reused:
            mention_ids[mention] = reused
            ids.setdefault(key, reused)
            # Reuse is a convenience, not a confirmed identity: the name happened
            # to be unique. Record it so the decision stays auditable.
            identity_candidates.append({
                "name": item["name"], "node_type": item["type"],
                "existing_ids": [reused], "new_id": None,
                "source_id": data["source_id"], "start": item["start"],
                "decision": "candidate",
                "reason": "同名同类型在库中只有一个匹配项，据此指向它；这是候选，不是确认",
            })
            continue
        position = (current_ordinal, item["start"])
        node_type = str(item["type"])
        node_id = allocate(
            positions.setdefault(node_type, []),
            position,
            taken.setdefault(node_type, set()),
            prefix_for(node_type),
        )
        taken[node_type].add(node_id)
        positions[node_type].append((position, node_id))
        mention_ids[mention] = node_id
        ids.setdefault(key, node_id)
        if item["type"] != "event" and existing.get(key):
            identity_candidates.append({
                "name": item["name"], "node_type": item["type"],
                "existing_ids": [
                    node["id"] for node in snapshot["nodes"]
                    if node["node_type"] == item["type"] and node["name"] == item["name"]
                    and not node.get("retracted")
                ],
                "new_id": mention_ids[mention],
                "source_id": data["source_id"], "start": item["start"],
                "decision": "separated",
                "reason": "同名同类型已有多个候选，未合并；确认后再归并",
            })

    # Composite events: a scene lists the atomic events inside it. No assertion
    # family needed — containment is a node field.
    scene_children: dict[str, list[str]] = {}
    for event in event_items:
        scene_name = event.get("scene")
        quote = event["quotes"][0] if event["quotes"] else ""
        if scene_name and ("scene", scene_name) in ids:
            scene_children.setdefault(scene_name, []).append(
                mention_ids[("event", event["name"], quote)]
            )

    # 场景说自己属于哪个大事件：反过来给大事件填上它的场景。
    storyline_children: dict[str, list[str]] = {}
    for scene in nodes:
        if scene["type"] != "scene":
            continue
        parent = str(scene.get("storyline") or "")
        if parent and ("storyline", parent) in ids:
            quote = scene["quotes"][0] if scene["quotes"] else ""
            storyline_children.setdefault(parent, []).append(
                mention_ids[("scene", scene["name"], quote)]
            )

    existing_ids = {item["id"] for item in snapshot["nodes"]}
    for item in nodes:
        key = (item["type"], item["name"])
        quote = item["quotes"][0] if item["quotes"] else ""
        node_id = mention_ids[(item["type"], item["name"], quote)]
        if node_id in existing_ids:
            continue  # already reviewed: re-extraction does not rewrite it
        payload = {
            "kind": "node",
            "id": node_id,
            "node_type": item["type"],
            "name": item["name"],
            "aliases": item.get("aliases") or [],
            "context_id": "canon",
            "identity_note": "原文提取",
            **(
                {"provisional_name": str(item.get("provisional_name") or "").strip()}
                if item["type"] == "storyline" and item.get("provisional_name")
                else {}
            ),
            **(
                {"provisional_name": str(item.get("provisional_name") or "").strip()}
                if item["type"] == "storyline" and item.get("provisional_name")
                else {}
            ),
            "disclosure": {"source_id": data["source_id"], "start": item["start"]},
            "evidence": {"source_id": data["source_id"], "quote": item["quotes"][0]},
        }
        if item["type"] == "scene":
            payload["events"] = scene_children.get(item["name"], [])
        if item["type"] == "storyline":
            # 大事件下面挂的是场景：跨场景、甚至跨章都正常。
            children = storyline_children.get(item["name"], [])
            payload["events"] = children
            # 它是一段而不是一点：披露位置取它第一个场景的位置，别用模型随手引的那句。
            starts = [
                scene["start"] for scene in nodes
                if scene["type"] == "scene" and scene.get("storyline") == item["name"]
            ]
            if starts:
                payload["disclosure"] = {
                    "source_id": data["source_id"], "start": min(starts),
                }
        if item["type"] == "storyline":
            # 大事件下面挂的是场景：跨场景、甚至跨章都正常。
            payload["events"] = storyline_children.get(item["name"], [])
        if item["type"] == "event":
            resolved: list[str] = []
            for name in item.get("participants") or []:
                target = None
                candidate = by_name.get(name)
                if candidate and candidate["type"] in ALLOWED_PARTICIPANTS:
                    target = ids.get((candidate["type"], name))
                if target is None:
                    for kind in ALLOWED_PARTICIPANTS:
                        target = existing.get((kind, name))
                        if target:
                            break
                if target and target not in resolved:
                    resolved.append(target)
            payload["participants"] = resolved
        objects.append(payload)

    # A source needs a reading position before disclosure queries can order it.
    registered_sources = {
        item["source_id"] for item in snapshot.get("source_orders", []) or []
    }
    if data["source_id"] not in registered_sources:
        ordinal = max(
            (int(item.get("ordinal", 0)) for item in snapshot.get("source_orders", []) or []),
            default=0,
        ) + 1
        objects.append({
            "kind": "source_order",
            "id": f"order_{data['source_id'][:12]}",
            "source_id": data["source_id"],
            "ordinal": ordinal,
            "context_id": "canon",
        })

    # Disclosure checkpoints: the event a reader has just passed when a value
    # becomes visible. This is a reading position, not a moment of change — the
    # same field must never mean both.
    event_by_name: dict[str, str] = {}
    checkpoint_order: list[tuple[int, int, str]] = []
    for event in event_items:
        quote = event["quotes"][0] if event["quotes"] else ""
        event_id = mention_ids[("event", event["name"], quote)]
        event_by_name.setdefault(event["name"], event_id)
        quote_len = len(quote)
        checkpoint_order.append((event["start"], event["start"] + quote_len, event_id))

    # Properties are one sequence of (value, checkpoint). A value is visible from
    # the checkpoint the reader reaches it at; before that it is simply unknown.
    # It does not follow that the checkpoint caused the value, or that the value
    # began to hold in the story world at that moment.
    recorded = {item["id"] for item in ops.property_records()}
    used_ids: set[str] = set()
    skipped: list[dict] = []
    records: list[dict] = []
    # 场景名 → 编号，用来把「大事件走到哪了」这句话挂到场景的最后一步：这一场
    # 读完才算读到它，读一半就显示等于提前剧透。
    scene_name_by_id = {
        node_id_: name for (kind, name), node_id_ in ids.items() if kind == "scene"
    }
    # The model may hand back a spelling that was merged or renamed away. Reading
    # it as the name in use keeps one aspect from growing a second definition the
    # moment a human renames it.
    aliases = ops.property_aliases()
    definitions: dict[str, str] = {
        aliases.get(item["id"], item["id"]): item.get("value_type", "string")
        for item in ops.property_records()
        if item["kind"] == "property_definition"
    }
    # 同一批里同一个方面可能既有 node 又有 either：either 只是允许「无 / 未知」这类普通值，
    # 两者是同一类。整批先定下类型，免得先出现的 node 把后面的 either 顶掉。
    batch_types: dict[str, str] = {}
    for prop in data.get("properties", []):
        name = aliases.get(prop["property"], prop["property"])
        if batch_types.get(name) != "either":
            batch_types[name] = prop["value_type"]

    # 事件 → 它所属的场景。`storyline_progress` 只能挂在场景上；模型有时把 node 写成
    # 地点名（「古战场」既是地点又是场景）或事件名，那就按**落点事件所在的场景**归位——
    # 那是它自己给的位置，不是猜的。归不回去就原样留着，审核会报出来。
    scene_of_event: dict[str, str] = {}
    for scene_name, event_ids in scene_children.items():
        scene_id = ids.get(("scene", scene_name))
        if not scene_id:
            continue
        for event_id in event_ids:
            scene_of_event.setdefault(event_id, scene_id)

    def progress_scene(prop: dict, node_id: str | None) -> str | None:
        if str(prop.get("property") or "") != STORYLINE_PROGRESS:
            return node_id
        if node_id and node_id in scene_name_by_id:
            return node_id
        anchor = event_by_name.get(str(prop.get("at_event") or ""))
        if anchor and anchor in scene_of_event:
            return scene_of_event[anchor]
        if node_id in scene_of_event:  # node 写的是事件名
            return scene_of_event[node_id]
        return node_id

    for prop in data.get("properties", []):
        node_id = ids.get((prop["node_type"], prop["node"])) if "node_type" in prop else None
        if node_id is None:
            for kind in ("person", "group", "item", "location", "scene", "event"):
                node_id = ids.get((kind, prop["node"]))
                if node_id:
                    break
        if node_id is None:
            for kind in ("person", "group", "item", "location", "scene", "event"):
                node_id = existing.get((kind, prop["node"]))
                if node_id:
                    break
        node_id = progress_scene(prop, node_id)
        if node_id is None:
            continue
        property_id = str(prop["property"])
        property_id = aliases.get(property_id) or property_id
        proposed_type = prop["value_type"]
        known_type = definitions.get(property_id)
        if known_type is None:
            known_type = str(batch_types.get(property_id) or proposed_type)
            definitions[property_id] = known_type
            records.append({
                "kind": "property_definition", "id": property_id,
                "value_type": known_type, "cardinality": "one",
            })
        elif known_type != proposed_type and {known_type, proposed_type} != {"node", "either"}:
            continue
        value_type = known_type
        value = prop["value"]
        if value_type in {"node", "either"}:
            target = None
            for kind in ALLOWED_PARTICIPANTS:
                target = ids.get((kind, value))
                if target:
                    break
            if target is None:
                for kind in ("person", "group", "item", "location"):
                    target = existing.get((kind, value))
                    if target:
                        break
            if target is None:
                # either 允许写「无」「未知」这类非节点值；node 类型解析不到就跳过。
                if value_type == "node":
                    continue
            else:
                value = target
        quote = prop["quotes"][0]
        anchor = event_by_name.get(prop.get("at_event") or "")
        basis = "model" if prop.get("at_event") else "nearest"
        if property_id == STORYLINE_PROGRESS:
            # 大事件进展说的是**这一场读完时的样子**，所以锚在这一场最后一步，
            # 而不是模型随手引的那句。引文仍在场景之内，位置照旧记着。
            scene_events = scene_children.get(scene_name_by_id.get(node_id or "", "")) or []
            if scene_events:
                anchor = max(scene_events, key=lambda item: order_key(item) or (999,))
                basis = "scene_end"
        if not anchor:
            anchor = _checkpoint_at(prop["start"], checkpoint_order)
        if not anchor:
            # Nothing in this chapter covers the position. Anchoring to a made-up
            # start would make the value visible before the reader learns it, so
            # the value is reported instead of silently dropped.
            skipped.append({
                "name": f"{prop['node']}.{property_id}",
                "reason": "没有可锚定的披露事件：本章事件未覆盖该位置",
            })
            continue
        digest = hashlib.sha1(
            f"{node_id}:{property_id}:{anchor}:{value}".encode()
        ).hexdigest()[:12]
        record_id = f"val_{digest}"
        if record_id in recorded or record_id in used_ids:
            continue
        used_ids.add(record_id)
        record = {
            "kind": "property_value", "id": record_id,
            "node_id": node_id, "property_id": property_id,
            "value": value, "event_id": anchor,
            "disclosure": {"source_id": data["source_id"], "start": prop["start"]},
            "evidence": {"source_id": data["source_id"], "quote": quote},
        }
        # Who chose the anchor, not what it proves: "model" means the model named
        # the event; it still only locates the disclosure, not the story change.
        record["anchor_basis"] = basis
        if prop.get("source"):
            record["source"] = str(prop["source"])
        # The category the model chose. It is a suggestion: a human can move the
        # record later, and an absent one simply resolves to 其他.
        if prop.get("category"):
            record["category"] = prop["category"]
        # Who else the sentence involves. Resolved to ids; unknown names drop out
        # rather than becoming dangling references.
        related: list[str] = []
        for name in prop.get("related_nodes") or []:
            target = None
            for kind in ALLOWED_PARTICIPANTS:
                target = ids.get((kind, name)) or existing.get((kind, name))
                if target:
                    break
            if target and target not in related:
                related.append(target)
        if related:
            record["related_nodes"] = related
        scope = prop.get("scope") or "world"
        if scope != "world":
            holder = None
            for kind in ("person", "group"):
                holder = ids.get((kind, prop.get("holder", "")))
                if holder:
                    break
            if holder is None:
                for kind in ("person", "group"):
                    holder = existing.get((kind, prop.get("holder", "")))
                    if holder:
                        break
            if holder is None:
                continue
            record["scope"] = scope
            record["holder_id"] = holder
        records.append(record)

    # 失效有两个来源，顺序不能反：
    #   ① 复核轮判的「这一章说它不成立了」——语义判断，锚在读者得知的那一刻；
    #   ② 程序判的「同一个方面出现了新值」——结构判断，锚在新值出现的事件。
    # 先落 ①，② 会把已经被 ① 标掉的目标跳过（`supersede_plan` 里看 `marked`），
    # 于是同一个事实只有一个锚点，而且是更懂语义的那个。
    accepted_records = ops.property_records(include_retracted=True)
    known_names: dict[str, str] = {}
    for node in snapshot["nodes"]:
        known_names.setdefault(node["name"], node["id"])
    for (_kind, name), node_id in ids.items():
        known_names.setdefault(name, node_id)
    records.extend(
        _review_supersede(
            data, records, accepted_records, known_names, event_by_name, skipped
        )
    )
    records.extend(_supersede_retractions(ops, records, accepted_records))
    # 关系轮直接点名要撤的记录（「这两个人的关系变了」），不需要再按名字去猜。
    records.extend(_retire_by_id(data, event_by_name, skipped))
    # 同一个目标可能被两个来源同时撤（复核说「不成立了」，结构说「有新值了」）：
    # 收成一条，否则两条撤回编号相同，预检会判重复、整章落不下去。
    records = _dedupe_retractions(records, accepted_records)

    revision = ops.graph_project_info()["revision"]
    if not objects and not records:
        return {
            "objects": [], "property_records": [], "diagnostics": [], "errors": [],
            "revision": revision, "empty": True, "node_count": 0,
            "property_count": 0, "scene_links": 0, "identity_candidates": [],
            "skipped": skipped,
        }
    diagnostics = ops.change_precheck(objects, records, revision)
    errors = [item for item in diagnostics if item.get("severity") == "error"]
    return {
        "objects": objects,
        "property_records": records,
        "diagnostics": diagnostics,
        "errors": errors,
        "revision": revision,
        "empty": False,
        "identity_candidates": identity_candidates,
        "skipped": skipped,
        "node_count": len([item for item in objects if item["kind"] == "node"]),
        "property_count": len([
            item for item in records if item["kind"] == "property_value"
        ]),
        "scene_links": sum(len(items) for items in scene_children.values()),
    }


# -- 只有拿不准的才要人看 ------------------------------------------------

def split_confident(ops, built: dict, data: dict) -> dict:
    """把一次提取分成「可以直接进图」和「要人看一眼」两份。

    可信的标准是**没有反面证据**：引文逐字对上（build 阶段已经保证）、预检没报它、
    属性审核没报它、模型没标存疑、同一事件里同一方面没有并列值。剩下的才算存疑——
    这样人只看那一小撮，而不是逐条点头。

    粒度到**记录**：一个节点的身份没问题、只是某条取值有疑问时，节点照样进图，只把
    那条取值留下来。节点自己有问题（预检报它、参与者里有存疑节点）才整节点留。

    返回 ``{"confident": {...}, "doubtful": {...}, "reasons": [...]}``。
    """
    from .property_audit import audit_candidate

    objects = list(built.get("objects", []))
    records = list(built.get("property_records", []))
    nodes = {obj["id"]: obj for obj in objects if obj.get("kind") == "node"}
    # 记录可能挂在上一章已有的节点上：显示原因时也要查得到名字。
    names = {node["id"]: node.get("name") for node in ops.graph_snapshot().get("nodes", [])}
    names |= {node_id: node.get("name") for node_id, node in nodes.items()}
    by_id = {record["id"]: record for record in records if record.get("id")}
    values = [record for record in records if record.get("kind") == "property_value"]

    doubtful_nodes: dict[str, str] = {}
    doubtful_records: dict[str, str] = {}

    def flag_node(node_id: str | None, why: str) -> None:
        if node_id and node_id in nodes:
            doubtful_nodes.setdefault(str(node_id), why)

    def flag_record(record_id: str | None, why: str) -> None:
        if record_id and record_id in by_id:
            doubtful_records.setdefault(str(record_id), why)

    for row in built.get("diagnostics") or []:
        if row.get("severity") != "error":
            continue
        target = str(row.get("object_id") or "")
        why = f"预检 {row.get('code')}"
        if target in nodes:
            flag_node(target, why)
        elif target in by_id:
            flag_record(target, why)

    for item in ((data.get("review") or {}).get("unresolved") or []):
        name = str(item.get("node") or "")
        aspect = str(item.get("property") or "")
        wanted = item.get("value")
        why = f"模型拿不准：{item.get('why') or item.get('reason') or '未说明'}"
        matched = [
            record for record in values
            if nodes.get(str(record.get("node_id")), {}).get("name") == name
            and str(record.get("property_id")) == aspect
            and (wanted is None or str(record.get("value")) == str(wanted))
        ]
        for record in matched:
            flag_record(record["id"], why)
        if not matched:
            for node_id, node in nodes.items():
                if node.get("name") == name:
                    flag_node(node_id, why)

    # 分类名、缺中文名只是显示上的分组，不影响这条记录成立，不值得拦下来让人看。
    # 重名、分组、中文名都只是「看起来像同一件事」，不影响这条记录成立。
    # 一条提示就把枢纽节点拦下来，会顺着参与者把整章都拖成存疑——那等于没提。
    cosmetic = {
        "category_unknown", "category_unassigned", "label_missing",
        "aspect_overlap", "repeated_value",
    }
    for issue in audit_candidate(ops, built).get("issues") or []:
        if issue.get("severity") not in {"error", "warning"}:
            continue
        if issue.get("code") in cosmetic:
            continue
        why = f"审核 {issue.get('code')}"
        flagged = issue.get("record_ids") or []
        for record_id in flagged:
            flag_record(record_id, why)
        if not flagged:
            flag_node(issue.get("node_id"), why)

    # 同一方面、同一作用域、**同一个位置**出现两个不同值才是并列（一句话说了两件事）。
    # 位置不同的由取代机制处理（后者接手），不在这里拦——两套判定不能各说一套。
    groups: dict[tuple, list[dict]] = {}
    for record in values:
        disclosure = record.get("disclosure") or {}
        groups.setdefault((
            record.get("node_id"), record.get("property_id"),
            record.get("event_id"), record.get("scope"),
            disclosure.get("source_id"), disclosure.get("start"),
        ), []).append(record)
    for rows in groups.values():
        if len({str(row.get("value")) for row in rows}) <= 1:
            continue
        for row in rows:
            flag_record(row["id"], "同一位置同一方面有两个不同值")

    # 闭合：存疑节点带着自己的记录一起留；引用了它的节点也留（否则参与者悬空）。
    doubtful_names = {nodes[node_id].get("name") for node_id in doubtful_nodes}
    for record in records:
        if record.get("kind") != "property_definition" and str(
            record.get("node_id")
        ) in doubtful_nodes:
            flag_record(record.get("id"), "所属节点待确认")
    for node_id, node in nodes.items():
        if node_id in doubtful_nodes:
            continue
        references = set(node.get("participants") or [])
        if node.get("scene"):
            references.add(str(node["scene"]))
        if references & doubtful_names:
            flag_node(node_id, "参与者里有待确认的节点")

    # 撤回不分来源：结构推的（有新值顶掉）和模型判的（这一章说它不成立了）都跟着批次走。
    # 它们不是「值」，没有并列可言；判错了在历史里看得见，重录一条就回来了。

    confident_objects = [
        obj for obj in objects
        if not (obj.get("kind") == "node" and obj["id"] in doubtful_nodes)
    ]
    doubtful_objects = [
        obj for obj in objects
        if obj.get("kind") == "node" and obj["id"] in doubtful_nodes
    ]
    confident_records = [r for r in records if r.get("id") not in doubtful_records]
    doubtful = [r for r in records if r.get("id") in doubtful_records]

    reasons = [
        {"node_id": node_id, "name": nodes[node_id].get("name"), "why": why}
        for node_id, why in doubtful_nodes.items()
    ]
    reasons += [
        {
            "record_id": record_id,
            "node_id": by_id[record_id].get("node_id"),
            "name": names.get(str(by_id[record_id].get("node_id"))),
            "property_id": by_id[record_id].get("property_id"),
            "value": by_id[record_id].get("value"),
            "why": why,
        }
        for record_id, why in doubtful_records.items()
    ]
    return {
        "confident": {"objects": confident_objects, "records": confident_records},
        "doubtful": {"objects": doubtful_objects, "records": doubtful},
        "reasons": reasons,
    }


def commit_extraction(ops, data: dict, *, review: str = "doubtful") -> dict:
    """把一份提取落进图：可信的直接进，存疑的留成候选。

    ``review="all"`` 退回老做法：全部进候选，等人一条条看。
    """
    built = build_candidate(ops, data)
    note = f"原文提取：{data.get('chapter') or data.get('source_id') or ''}"
    source = "correction" if built.get("requires_correction") else "model"

    if built.get("empty"):
        return {
            "empty": True, "candidate": None, "accepted": None,
            "diagnostics": [], "doubtful": [], "review": review,
        }
    if built["errors"]:
        return {
            "errors": True, "candidate": None, "accepted": None,
            "diagnostics": built["diagnostics"], "doubtful": [], "review": review,
        }

    if review == "all":
        candidate = ops.change_propose(
            built["objects"], built["property_records"], built["revision"],
            source=source, note=note,
        )
        return {
            "candidate": candidate, "accepted": None, "empty": False, "errors": False,
            "diagnostics": built["diagnostics"], "doubtful": [], "review": "all",
            "node_count": built["node_count"],
        }

    split = split_confident(ops, built, data)
    accepted = None
    confident = split["confident"]
    if confident["objects"] or confident["records"]:
        revision = ops.graph_project_info()["revision"]
        try:
            batch = ops.change_propose(
                confident["objects"], confident["records"], revision,
                source=source, note=f"{note}（可信部分）",
            )
            ops.change_accept(batch, 1)
        except ValueError as exc:
            # 拆开之后过不了预检：宁可全部交给人，也不要吞掉一条。
            candidate = ops.change_propose(
                built["objects"], built["property_records"], built["revision"],
                source=source, note=note,
            )
            return {
                "candidate": candidate, "accepted": None, "empty": False, "errors": False,
                "diagnostics": built["diagnostics"], "doubtful": split["reasons"],
                "review": review, "fallback": str(exc), "node_count": built["node_count"],
            }
        accepted = {
            "candidate": batch,
            "revision": ops.graph_project_info()["revision"],
            "node_count": len([
                obj for obj in confident["objects"] if obj.get("kind") == "node"
            ]),
            "property_count": len([
                rec for rec in confident["records"] if rec.get("kind") == "property_value"
            ]),
        }

    candidate = None
    doubtful = split["doubtful"]
    if doubtful["objects"] or doubtful["records"]:
        candidate = ops.change_propose(
            doubtful["objects"], doubtful["records"],
            ops.graph_project_info()["revision"], source=source, note=f"{note}（待确认）",
        )
    return {
        "candidate": candidate, "accepted": accepted, "empty": False, "errors": False,
        "diagnostics": built["diagnostics"], "doubtful": split["reasons"],
        "review": review, "node_count": built["node_count"],
    }
