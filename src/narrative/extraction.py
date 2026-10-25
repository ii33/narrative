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
from .order_ids import allocate

NODE_TYPES = ("person", "group", "item", "location", "scene", "event")

PROMPT = """你在为一部小说的叙事数据库提取「章节结构」。

下面是《{title}》全文，共 {chars} 字。

任务：按原文顺序，提取这一章出现的**全部节点**和**发生的事件**。

节点类型（type 只能取这六种）：
- person：人物。有名有姓、有称呼、被当作个体对待的。
- group：群体或组织。公司、门派、势力、家族。
- item：物品。法宝、武器、服饰、日常物件。
- location：地点。城市、星球、房间、湖、战场等可指认的空间。
- scene：场景。一段在同一地点、连续时间内发生的叙事片段，通常由地点变化或时间跳跃分隔。
- event：事件。这一章实际发生的事：一个动作、一次对话产生的转折、一个状态变化。

## 事件粒度（最重要）

- **一件事只输出一个事件。** 同一段连续动作（取出→戴上→试用）算一件事，不要拆成多个。
- 同一件事不要输出两条措辞不同的记录（例如「沈砚告知来电」和「沈砚转告来电」是同一条）。

## 说明性内容也是披露

法宝是什么、星球叫什么、人物有什么经历、修真界有什么规矩——这些段落没有动作，
但都是原文明确告诉读者的信息，**必须提取**，可以写成事件（「老周展示并解释纳芥手镯」），
也可以写成属性。不要因为"这一段没发生什么事"就跳过。
- 事件名称概括程度以「一句话能说清发生了什么」为准，不要细到单个动作，也不要粗到「发生冲突」。
- **每个场景至少要有一个事件。** 只列场景、不列事件是不合格的输出。
- 战斗、争吵这类连续过程，按「一次交手」「一个转折」拆成事件，不要把整段塞进场景了事。

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
- 信息来自某个人的视角时，用 `scope` 标明归属，`holder` 填那个人：
  - `world`：原文直接叙述的内容（默认，不填 holder）；
  - `cognition`：某个人怎样理解、相信、知道、记得或怀疑。**"知道"也归这一类**，
    原文的确定程度照抄进 value（"知道老周赢了"就记 `value: "老周赢了"`，不要改成"确定"）；
  - `claim`：某个人对外说出的内容（口头、信件、公告），holder 填说的人。
  三种之间**不自动转换**：说了不等于相信，相信不等于世界成立。
- `source` 记信息是怎么表达出来的：`narration`（叙述）、`speech`（人物发言）、
  `thought`（人物想法）、`observation`（人物观察）、`document`（书信碑文等载体）。拿不准就留空。
- `related_nodes` 填原文明示涉及的其他节点（观察者、被归因的人、信息载体），
  **不要填 holder**——holder 已经由 scope 那一栏说明。
- 每条属性必须有原文引文，只写原文明确写出的。
- property 用英文小写标识，命名的是**被说的那个方面**（age、skin、height、holder、injury、
  statement、plan），不要用中文。
  **不要用"变化"命名**：不写 `height_change`、`appearance_change`、`skin_change`。
  原文说"变高了"，就记 `height = 变高了`；说"白了许多"，就记 `skin = 白了许多`。
- value_type 取 string、number、boolean、node、any；holder 这类指向节点的属性用 node，
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

人物：基本信息 / 外貌 / 身份与经历 / 能力与身体状态 / 持有与使用 / 关系 / 心理与意图 / 其他
- 基本信息：姓名、别名、性别、年龄、种族、籍贯
- 外貌：身高、体型、肤色、面容、发型、穿着
- 身份与经历：职业、职务、称号、组织身份、出身、教育经历
- 能力与身体状态：修为、技能、天赋、体质、伤势、健康
- 持有与使用：财物、装备、携带物、佩戴物
- 关系：亲属、师徒、朋友、敌对、隶属
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

输出一个 JSON 对象，不要任何解释文字：

{{"nodes": [
  {{"name": "林舟", "type": "person", "quotes": ["原文逐字引文"], "aliases": ["林总"]}},
  {{"name": "站前旅社", "type": "location", "quotes": ["原文逐字引文"], "aliases": []}},
  {{"name": "撞破女友与陈默", "type": "scene", "quotes": ["原文逐字引文"], "aliases": []}},
  {{"name": "林舟推开房门撞见女友与陈默", "type": "event", "quotes": ["原文逐字引文"],
    "aliases": [], "participants": ["林舟", "陈默", "女友"], "scene": "撞破女友与陈默"}},
  {{"name": "林舟用水果刀刺伤陈默", "type": "event", "quotes": ["原文逐字引文"],
    "aliases": [], "participants": ["林舟", "陈默", "水果刀"], "scene": "撞破女友与陈默"}}
],
 "properties": [
  {{"node": "林舟", "property": "age", "value": 29, "value_type": "number",
    "category": "基本信息",
    "quotes": ["原文逐字引文"], "scope": "world", "holder": null,
    "source": "narration", "related_nodes": [], "at_event": "沈砚告知林舟陈总来电"}},
  {{"node": "铜钥匙", "property": "holder", "value": "林舟", "value_type": "node",
    "category": "归属与持有",
    "quotes": ["原文逐字引文"], "scope": "world", "holder": null,
    "source": "narration", "related_nodes": ["老周"],
    "at_event": "老周赠送铜钥匙，林舟戴上试用"}},
  {{"node": "老周", "property": "state", "value": "赢了那场交战", "value_type": "string",
    "category": "其他",
    "quotes": ["原文逐字引文"], "scope": "cognition", "holder": "林舟",
    "source": "thought", "related_nodes": ["苏娘"],
    "at_event": "苏娘施展落英阵，老周以铁尺破解"}}
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
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        start = body.find("{")
        end = body.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型回复里没有 JSON 对象") from None
        return json.loads(body[start:end + 1])


VALUE_TYPES = ("string", "number", "boolean", "node", "any")
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
            index = text.find(quote)
            if index < 0:
                rejected.append({"name": f"{node_name}.{property_id}", "reason": "引文不在原文中",
                                 "quote": quote[:60]})
                continue
            good.append({"quote": quote, "start": index})
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
            index = text.find(quote)
            if index < 0:
                rejected.append({"name": name, "reason": "引文不在原文中", "quote": quote[:60]})
                continue
            good.append({"quote": quote, "start": index})
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
        })

    # Reading order inside each type, then grouped for display.
    nodes.sort(key=lambda item: (item["start"], item["name"]))

    grouped: dict[str, list[dict]] = {node_type: [] for node_type in NODE_TYPES}
    for node in nodes:
        grouped[node["type"]].append(node)
    properties = _parse_properties(payload.get("properties"), text, {item["name"] for item in nodes}, rejected)
    scenes = {item["name"] for item in grouped["scene"]}
    for event in grouped["event"]:
        if event["scene"] and event["scene"] not in scenes:
            event["scene_not_found"] = True
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
    seen: dict[tuple[str, str], int] = {}
    for record in ops.property_records():
        if record.get("kind") != "property_value":
            continue
        node_type = (nodes.get(record.get("node_id")) or {}).get("node_type") or "other"
        key = (record.get("property_id") or "", node_type)
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


def extract_chapter(
    title: str,
    text: str,
    *,
    settings: HarnessSettings,
    api_key: str,
    workdir: Path,
    ops=None,
) -> dict:
    """Read the chapter, then (when a project is given) compare against stored values.

    Round one extracts without knowing what is already recorded. Round two hands
    the model the stored values for exactly the properties it proposed and asks
    which are genuinely new; extra rounds are allowed if it asks for more.
    """
    runner = HarnessRunner(settings, api_key, workdir)
    known = known_properties(ops) if ops is not None else None
    try:
        reply: HarnessReply = runner.agent_turn(
            build_prompt(title, text, known), f"extract-{uuid4().hex}"
        )
    finally:
        runner.close()
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "raw.txt").write_text(reply.raw, encoding="utf-8")
    (workdir / "metadata.json").write_text(
        json.dumps(reply.metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result = parse_nodes(reply.raw, text)
    if ops is not None and result.get("properties"):
        from .extraction_review import review_properties

        reviewed = review_properties(
            ops, result, settings=settings, api_key=api_key, workdir=workdir
        )
        result["properties"] = reviewed["properties"]
        result["review"] = {
            "dropped": reviewed["dropped"],
            "unresolved": reviewed.get("unresolved") or [],
            "rounds": reviewed["rounds"],
        }
    result.update({
        "chapter": title,
        "characters": len(text),
        "model": settings.model,
        "provider": settings.provider,
        "usage": reply.metadata,
        "raw": reply.raw,
    })
    return result


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
    positions: list[tuple[tuple[int, int], str]] = []
    for node in snapshot["nodes"]:
        disclosure = node.get("disclosure")
        if isinstance(disclosure, dict) and isinstance(disclosure.get("start"), int):
            positions.append((
                (int(orders.get(disclosure.get("source_id"), 99)), disclosure["start"]),
                node["id"],
            ))
    taken = {node["id"] for node in snapshot["nodes"]}

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
        node_id = allocate(positions, position, taken)
        taken.add(node_id)
        positions.append((position, node_id))
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
            "disclosure": {"source_id": data["source_id"], "start": item["start"]},
            "evidence": {"source_id": data["source_id"], "quote": item["quotes"][0]},
        }
        if item["type"] == "scene":
            payload["events"] = scene_children.get(item["name"], [])
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
    definitions: dict[str, str] = {
        item["id"]: item.get("value_type", "string")
        for item in ops.property_records()
        if item["kind"] == "property_definition"
    }
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
        if node_id is None:
            continue
        property_id = prop["property"]
        known_type = definitions.get(property_id)
        if known_type is None:
            definitions[property_id] = prop["value_type"]
            records.append({
                "kind": "property_definition", "id": property_id,
                "value_type": prop["value_type"], "cardinality": "one",
                "persistence": "until_changed",
            })
        elif known_type != prop["value_type"]:
            continue
        value = prop["value"]
        if prop["value_type"] == "node":
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
                continue
            value = target
        quote = prop["quotes"][0]
        anchor = event_by_name.get(prop.get("at_event") or "")
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
        record["anchor_basis"] = "model" if prop.get("at_event") else "nearest"
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
