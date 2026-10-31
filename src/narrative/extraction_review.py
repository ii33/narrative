"""Second pass: compare a chapter's extracted properties against stored values.

Round one extracts without knowing what is already recorded. Round two hands the
model the stored values for exactly the properties it proposed, and asks which
are genuinely new. Extra rounds are allowed when the model asks for more context,
but the default is two.
"""

from __future__ import annotations

import json
from uuid import uuid4

from .extraction import _payload
from .graph_operations import GraphOperations
from .harness import HarnessRunner, HarnessSettings

MAX_ROUNDS = 4
REVIEW_VERSION = "review/8"

REVIEW_PROMPT = """你在为一部小说的属性记录做去重。

第一轮从本章提取了这些属性：

{proposed}

这些属性在数据库中已记录的值（截至本章之前）：

{stored}

这一阶段的策略是**宽松保留**：误删会丢掉原文信息，重复只是多一条记录，可以校对时再归并。

只丢弃**确定的重复**：

1. 同一原文位置、同一个值，重复输出了两遍 → 丢弃多出来的那条。
2. 不同位置披露同一个值 → **保留**，这是新的确认，有自己的出处。
3. 属性名称不同但意思接近（`height` 与 `height_change`）→ **各自保留**，不要合并，
   也不要改名；它们是两种表达，留给校对归并。
4. 值不同、模糊描述（"变好了""似乎更差"）、疑似矛盾 → **全部保留**。
5. 拿不准的一律**保留**，不要放进 drop。

`at_event` 填**披露这个值所在的事件**名称，它只表示读者读到哪里知道这件事，
不要求该事件导致变化。填不出来就留空，不影响保留。

## 旧值失效

除了「新值取代旧值」，还有一种情况：旧值**没有被新值取代**，但本章的叙述说明它
已经不成立了（情绪过去了、伤好了、身份变了、人走了）。这种要在 `supersede` 里给出：

- `at_event` 必填：读者在本章哪个事件读到「它不再成立」。**必须从下面「本章事件」里
  照抄一个名字**，一个字都不要改——写错名字这条失效就落不下去。
- 只给**明确终止**的：原文没有交代，就不要猜。拿不准的一律不给。
- 不要因为「和旧值不一样」就给：同一方面在不同时点有不同值是正常的推进，
  取代关系由程序按披露顺序自动标记，不需要你重复。
- 给出旧值时要照抄它原来的写法，便于对上记录。
- **不限于本章提议的属性。** 上面「已记录的值」里可能带着本章没有提议、但被本章内容
  推翻的方面（本章用更宽的名字写了同一件事，比如「外貌」里说肤色变白了，而旧记录里
  还有一条「肤色」）。这种也要在 `supersede` 里给出，`property` 用旧记录里的名字。
- **一时的状态会过去，别让它一直挂着。** 判断依据是每条旧值下面的「原话」：
  - 情绪（悲伤、紧张、很满意）、一时的姿态（打坐恢复中、目瞪口呆）、当场的效果
    （刚改造完、浑身是血）——这类**不是长期属性**。本章时间往前走了、场景换了、
    也没再确认，读者读到这里时它已经不成立，要判失效；`at_event` 填**读者意识到它
    已经过去的那一处**（他接着去做别的事、或新场景开始）。
  - 伤势、身份、持有物、修为这类**会一直成立直到被改变**的，本章没明说变化就不要判失效。
  - 拿不准是长期还是一时的，就不判——宁可留一条旧状态，也不要编一个终止点。
- **一时的状态会过去，别让它一直挂着。** 判断依据是每条旧值下面的「原话」：
  - 情绪（悲伤、紧张、很满意）、一时的姿态（打坐恢复中、目瞪口呆）、当场的效果
    （刚改造完、浑身是血）——这类**不是长期属性**。本章时间往前走了、场景换了、
    也没再确认，读者读到这里时它已经不成立，要判失效；`at_event` 填**读者意识到它
    已经过去的那一处**（他接着去做别的事、或新场景开始）。
  - 伤势、身份、持有物、修为这类**会一直成立直到被改变**的，本章没明说变化就不要判失效。
  - 拿不准是长期还是一时的，就不判——宁可留一条旧状态，也不要编一个终止点。

## 本章事件（`at_event` 只能从这里照抄）

{events}

## 本章原文

判断「提议是否忠于原文」「旧值是否被这一章推翻」都以下面这段原文为准。原文与提议
冲突时，**以原文为准**：提议漏了、写歪了、把两件事并成一件，都在 `unsure` 里指出来
（`question` 写清哪里对不上），不要默默放过。

{text}

如果连信息都不够，返回 `{{"need": ["<你还想查什么>"]}}`，我会补给你。

只输出 JSON：

{{"keep": [{{"node": "林舟", "property": "height", "value": "又长高了一些", "at_event": ""}}],
 "drop": [{{"node": "林舟", "property": "height", "value": "一百八十公分",
           "reason": "同一位置重复输出"}}],
 "supersede": [{{"node": "林舟", "property": "state", "value": "心里对女人有点发怵",
                "at_event": "林舟走进站前旅社", "reason": "进了酒店之后没再提，情绪已经过去"}}],
 "unsure": [{{"node": "林舟", "property": "skin", "value": "白净",
             "question": "不确定这是变化还是同义描述"}}]}}
"""


UNSURE_PROMPT = """以下几条你上一轮拿不准。现在给出更完整的信息：

{context}

请给出终判：**只有确认是同一条记录的重复才 drop**（同一位置、同一个值）。
其余一律 keep，包括同值不同出处、模糊描述、互相矛盾的内容。不要再用 unsure。

只输出 JSON：

{{"keep": [{{"node": "林舟", "property": "skin", "value": "白净", "at_event": "林舟被改造"}}],
 "drop": [{{"node": "林舟", "property": "skin", "value": "皮肤黑", "reason": "同义描述"}}]}}
"""


def _context_for(ops: GraphOperations, data: dict, unsure: list[dict]) -> str:
    """Full history for each uncertain property, plus the sentence around it."""
    snapshot = ops.graph_snapshot()
    nodes = {item["id"]: item for item in snapshot["nodes"]}
    events = {item["id"]: item.get("name", "") for item in snapshot["nodes"]
              if item["node_type"] == "event"}
    sources = {item["id"]: item for item in ops.sources()}
    text = ""
    if data.get("source_id") in sources:
        text = ops.source(data["source_id"])["text"]

    blocks = []
    for item in unsure:
        if not isinstance(item, dict):
            continue
        node_name, property_id = item.get("node"), item.get("property")
        lines = [f"## {node_name}.{property_id} = {item.get('value')}",
                 f"你的疑问：{item.get('question', '')}"]
        history = []
        for record in ops.property_records():
            if record["kind"] != "property_value" or record.get("property_id") != property_id:
                continue
            node = nodes.get(record.get("node_id"))
            if node is None or node["name"] != node_name:
                continue
            history.append(
                f"  - {record.get('value')} @ {events.get(record.get('event_id'), '?')}"
            )
        lines.append("该属性已记录的完整历史：" if history else "该属性没有任何历史记录。")
        lines.extend(history)
        quote = ""
        for prop in data.get("properties", []):
            if prop.get("node") == node_name and prop.get("property") == property_id:
                quote = prop["quotes"][0]
                break
        if quote and text:
            start = max(0, text.find(quote) - 120)
            end = text.find(quote) + len(quote) + 120
            lines.append("原文上下文：" + text[start:end].replace("\n", " "))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _stored_values(ops: GraphOperations, data: dict) -> list[dict]:
    """The *current* value of each proposed property, not its whole history.

    Only the latest applicable entry per property is handed over: the question is
    "is this new", and a value seen once and superseded is not what it should be
    compared against. Chapter-internal rolling is left to the prompt, because the
    proposals carry their own positions.
    """
    # Scope and holder are part of the comparison key: a world fact and a
    # character's belief are different records even when the value reads the same.
    wanted: set[tuple[str, str, str, str]] = {
        (
            str(prop["node"]), str(prop["property"]),
            str(prop.get("scope") or "world"), str(prop.get("holder") or ""),
        )
        for prop in data.get("properties", [])
    }
    if not wanted:
        return []
    snapshot = ops.graph_snapshot()
    orders = {
        row["source_id"]: row["ordinal"] for row in snapshot.get("source_orders", []) or []
    }
    nodes = {item["id"]: item for item in snapshot["nodes"]}
    events = {item["id"]: item.get("name", item["id"]) for item in snapshot["nodes"]
              if item["node_type"] == "event"}

    def position(record: dict) -> tuple[int, int]:
        disclosure = record.get("disclosure")
        if not isinstance(disclosure, dict) or not isinstance(disclosure.get("start"), int):
            return (99, 0)
        return (int(orders.get(disclosure.get("source_id"), 99)), disclosure["start"])

    # 本章提到的人/物，它们**已有的其它方面**也要一并比较：一句话可能让某个旧方面
    # 不再成立，而那个方面本章根本没提议（「外貌」里写了肤色，旧的「肤色」就该失效）。
    mentioned = {str(prop["node"]) for prop in data.get("properties", [])}

    latest: dict[tuple[str, str, str, str], tuple[tuple[int, int], dict]] = {}
    for record in ops.property_records():
        if record["kind"] != "property_value":
            continue
        node = nodes.get(record.get("node_id"))
        if node is None:
            continue
        holder = nodes.get(record.get("holder_id") or "", {})
        key = (
            str(node["name"]), str(record.get("property_id")),
            str(record.get("scope") or "world"), str(holder.get("name") or ""),
        )
        if key not in wanted and str(node["name"]) not in mentioned:
            continue
        current = (position(record), record)
        if key not in latest or current[0] > latest[key][0]:
            latest[key] = current

    return [
        {
            "node": key[0],
            "property": key[1],
            "scope": key[2],
            "holder": key[3],
            "value": record.get("value"),
            "at_event": events.get(record.get("event_id"), ""),
            "chapter": position(record)[0],
            "start": position(record)[1],
            # 旧值自己的那句原文：判断「它现在还有没有效」要看它是怎么被说出来的，
            # 光看「心情 = 悲伤」看不出这是一时的还是长期的。
            "quote": str((record.get("evidence") or {}).get("quote") or "")[:60],
        }
        for key, (_pos, record) in sorted(latest.items())
    ]


def _format_proposed(data: dict) -> str:
    lines = []
    for prop in sorted(data.get("properties", []), key=lambda item: item.get("start", 0)):
        lines.append(
            f"- [{prop.get('start', 0)}] {prop['node']}.{prop['property']} = {prop['value']}"
            f"（引文：{prop['quotes'][0][:24]}）"
        )
    return "\n".join(lines) or "（无）"


def _format_stored(rows: list[dict]) -> str:
    if not rows:
        return "（这些属性在数据库中还没有记录）"
    return "\n".join(
        f"- {row['node']}.{row['property']} = {row['value']}"
        f"{' [' + row['scope'] + ('/' + row['holder'] if row.get('holder') else '') + ']' if row.get('scope', 'world') != 'world' else ''}"
        f" @ {row['at_event']}（第{row.get('chapter', '?')}章 {row.get('start', '?')}）"
        + (f"\n    原话：{row['quote']}" if row.get("quote") else "")
        for row in rows
    )


RELATION_PROMPT = """再看一遍这一章的**人物关系**：谁和谁是什么关系。

只写本章原文明确说出的关系，不要推测，不要写「关系不错」这种空话。
同一对人、同一件事只写一条（同一对人可以有好几种关系，那是几条不同的事）。

本章出现的人物只有下面这些，`from` / `to` **必须从这里选，一个字不差**：

{people}

每人每条给出：

- `from`：关系的这一方
- `to`：关系的另一方
- `kind`：**对方是 `from` 的什么人**——从 `from` 这一方说，不是从对方说。
  用原文的词，不要写称呼（「大哥」「老弟」「老板」是叫法，不是关系）。
  - **A 是 B 的父亲，就写两条、词正好相反**：
    `{{"from": "A", "to": "B", "kind": "儿子"}}`（A 的儿子是 B）
    `{{"from": "B", "to": "A", "kind": "父亲"}}`（B 的父亲是 A）。
    师父/徒弟、上司/下属、丈夫/妻子、叔父/侄 同理。
  - **两侧写同一个词等于没写方向**：`A —父子→ B`、`B —父子→ A` 读不出谁是谁的父亲。
    也不要拿成对的叫法当关系名（父子、夫妻、师徒、上下级、主仆、兄弟）——那是一个对子，
    不是某一方的身份。要写就写清是哪一边。
  - **本来就对等的叫法只写一条**（同伙、仇敌、恋人、朋友）：两个方向说的是同一件事，
    写两条只是同一个词出现两次。
  - **单向的只写有的一侧**：A —崇拜→ B，不要给 B 编一个「被崇拜」。
  - **可以带状态**：写进 `kind` 的括号里——「恋人（冷战中）」「师徒（已决裂）」。
    状态变了也算一次变化：旧那条要撤，新的一条带上新状态。
- `quotes`：可以给，也可以不给。给的话要**逐字**引用、引用**完整的一句**（只出现一次）；
  给不出来就留空，程序会用落点事件自己的引文——关系常常是从整段里读出来的，一句话说不清
- `at_event`：读者在哪一步知道这件事，从下面的「本章事件」里照抄一个

只输出 JSON：{{"relations": [ … ]}}。没有就返回空数组。A 是 B 的父亲、C 与 B 是仇敌时：

{{"relations": [
  {{"from": "A", "to": "B", "kind": "儿子", "at_event": "…"}},
  {{"from": "B", "to": "A", "kind": "父亲", "at_event": "…"}},
  {{"from": "B", "to": "C", "kind": "仇敌", "at_event": "…"}}
]}}

## 本章事件

{events}

## 本章原文

{text}"""


NO_JSON_PROMPT = "上一条回复里没有 JSON。请只输出那个 JSON 对象，不要任何解释或代码块。"

ANCHOR_REPAIR_PROMPT = """下面这些「旧值失效」写的事件名，在本章事件里找不到。请只从「本章事件」
里挑一个最贴近的，把 `at_event` 改成**原文照抄**的那个名字；确实没有对应事件的，把这一条删掉。

本章事件：
{events}

要修正的条目：
{items}

只输出 JSON：{{"supersede": [ … ]}}，其余字段照抄。"""


def _turn(runner, session: str, instruction: str, ops, rounds: list[int]) -> dict:
    """One review turn, plus any follow-ups the model asks for."""
    while rounds[0] < MAX_ROUNDS:
        rounds[0] += 1
        reply = runner.agent_turn(instruction, session)
        try:
            payload = _payload(reply.raw)
        except ValueError:
            # 偶尔它会把 JSON 写成一段说明。追问一次；再不行就当这一轮没答。
            rounds[0] += 1
            if rounds[0] >= MAX_ROUNDS:
                raise
            reply = runner.agent_turn(NO_JSON_PROMPT, session)
            payload = _payload(reply.raw)
        need = payload.get("need")
        if not need:
            return payload
        extra = _answer_need(ops, need)
        instruction = (
            "这是你要求补充的信息：\n"
            + json.dumps(extra, ensure_ascii=False, indent=2)
            + "\n请继续，按同样的 JSON 格式给出结果。"
        )
    raise ValueError(f"对比超过 {MAX_ROUNDS} 轮仍未给出结果")


def _repair_anchors(runner, session, items, events, ops, rounds) -> list[dict]:
    """事件名对不上时问模型要准确的名字——判断「哪个事件」是它的事，不是程序的。"""
    from .extraction import _squash

    known = {_squash(name) for name in events}
    broken = [
        item for item in items
        if isinstance(item, dict) and _squash(str(item.get("at_event") or "")) not in known
    ]
    if not broken or not events:
        return items
    instruction = ANCHOR_REPAIR_PROMPT.format(
        events="\n".join(f"- {name}" for name in events),
        items=json.dumps(broken, ensure_ascii=False, indent=2),
    )
    try:
        payload = _turn(runner, session, instruction, ops, rounds)
    except ValueError:
        return items
    fixed = payload.get("supersede")
    if not isinstance(fixed, list):
        return items
    # 修好的条目按 (node, property, value) 换回去；模型删掉的就不修了。
    by_key = {
        (str(item.get("node")), str(item.get("property")), str(item.get("value"))): item
        for item in items if isinstance(item, dict)
    }
    for item in fixed:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("node")), str(item.get("property")), str(item.get("value")))
        if key in by_key:
            by_key[key]["at_event"] = item.get("at_event")
    return list(by_key.values())


def _relation_round(
    runner, session, data, keep, events, ops, rounds, *, text: str
) -> tuple[list[dict], list[dict]]:
    """人物关系：看这一章，对照**现有的关系**，只写变了的部分。

    关系性质放在属性名上（「仇敌」「师父」），值指向对方节点——对方的页面在反向引用里
    能看到这条，两个人就通过一条记录连起来了。

    一个人和另一个人的关系变了（「恋人」变「仇敌」），旧那条要撤掉；没变的不动。
    判断是模型的，撤哪一条由它点名（`record_id`），程序只负责落地。
    """
    snapshot = ops.graph_snapshot() if ops is not None else {"nodes": []}
    nodes = {str(node["id"]): node for node in snapshot.get("nodes", [])}
    people = {
        str(node.get("name"))
        for node in (data.get("nodes") or [])
        if str(node.get("type") or "") == "person" and node.get("name")
    }
    people |= {
        str(node.get("name"))
        for node in nodes.values()
        if node.get("node_type") == "person" and node.get("name")
    }
    if len(people) < 2:
        return [], []

    # 现有的关系记录：值指向人物节点的那些，就是「谁和谁现在是什么关系」。
    current: list[dict] = []
    if ops is not None:
        for record in ops.property_records():
            if record.get("kind") != "property_value":
                continue
            subject, target = str(record.get("node_id") or ""), str(record.get("value") or "")
            if nodes.get(subject, {}).get("node_type") != "person":
                continue
            if nodes.get(target, {}).get("node_type") != "person":
                continue
            current.append({
                "record_id": record.get("id"),
                "from": nodes[subject].get("name"),
                "kind": record.get("property_id"),
                "to": nodes[target].get("name"),
            })
    instruction = RELATION_PROMPT.format(
        people="\n".join(f"- {name}" for name in sorted(people)),
        current="\n".join(
            f"- {row['from']} —{row['kind']}→ {row['to']}（记录 {row['record_id']}）"
            for row in current
        ) or "（还没有记录任何关系）",
        events="\n".join(f"- {name}" for name in events) or "（本章没有事件）",
        text=text or "（拿不到本章原文）",
    )
    rounds[0] += 1
    if rounds[0] > MAX_ROUNDS:
        return [], []
    try:
        reply = runner.agent_turn(instruction, session)
        payload = _payload(reply.raw)
    except ValueError:
        return [], []
    rows = payload.get("relations")
    if not isinstance(rows, list):
        return [], []

    seen = {
        (str(item.get("node")), str(item.get("property")), str(item.get("value")))
        for item in keep if isinstance(item, dict)
    }
    existing = {(row["from"], row["to"]): row for row in current}
    event_quote = {
        str(node.get("name")): str((node.get("quotes") or [""])[0])
        for node in (data.get("nodes") or [])
        if str(node.get("type") or "") == "event"
    }
    out: list[dict] = []
    retire: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        left, right = str(row.get("from") or ""), str(row.get("to") or "")
        kind = str(row.get("kind") or "").strip()
        if left not in people or right not in people or not kind:
            continue
        anchor = str(row.get("at_event") or "")
        # 关系没变就维持原样，不重复记一条。
        old = existing.get((left, right)) or existing.get((right, left))
        if old and str(old["kind"]) == kind:
            continue
        if old and str(old["kind"]) != kind:
            retire.append({
                "target_id": old["record_id"],
                "at_event": anchor,
                "reason": f"关系由「{old['kind']}」变为「{kind}」",
            })
        key = (left, kind, right)
        if key in seen:
            continue
        seen.add(key)
        # 引文优先用模型给的、且在原文里只出现一次的整句；
        # 关系常常是从整段里读出来的，一句话说不清——那就用落点事件自己的引文。
        start, quote = -1, ""
        for candidate in [str(item) for item in row.get("quotes") or [] if str(item).strip()]:
            if text.count(candidate) == 1:
                start, quote = text.find(candidate), candidate
                break
        if start < 0:
            quote = event_quote.get(anchor, "")
            if quote and text.count(quote) == 1:
                start = text.find(quote)
        if start < 0:
            continue
        out.append({
            "start": start,
            "node": left, "property": kind, "value": right, "value_type": "node",
            "category": "关系", "quotes": [quote], "scope": "world", "holder": None,
            "source": "narration", "related_nodes": [right],
            "at_event": anchor,
        })
    return out, retire


def review_properties(
    ops: GraphOperations,
    data: dict,
    *,
    settings: HarnessSettings,
    api_key: str,
    workdir,
) -> dict:
    """Filter the proposed properties against stored values.

    Round two separates certain keep/drop from unsure ones; round three takes the
    unsure ones with their full history and surrounding text and forces a verdict.
    """
    stored = _stored_values(ops, data)
    source = ops.source(str(data.get("source_id") or "")) if data.get("source_id") else {}
    events = [
        str(node.get("name"))
        for node in (data.get("nodes") or [])
        if str(node.get("type") or "") == "event" and node.get("name")
    ]
    instruction = REVIEW_PROMPT.format(
        proposed=_format_proposed(data), stored=_format_stored(stored),
        events="\n".join(f"- {name}" for name in events) or "（本章没有事件）",
        text=source.get("text") or "（拿不到本章原文）",
    )
    runner = HarnessRunner(settings, api_key, workdir)
    session = f"review-{uuid4().hex}"
    rounds = [0]
    try:
        payload = _turn(runner, session, instruction, ops, rounds)
        keep = list(payload.get("keep") or [])
        drop = list(payload.get("drop") or [])
        unsure = list(payload.get("unsure") or [])
        supersede = list(payload.get("supersede") or [])
        if unsure:
            context = _context_for(ops, data, unsure)
            try:
                payload = _turn(
                    runner, session, UNSURE_PROMPT.format(context=context), ops, rounds
                )
            except ValueError:
                # 终判没拿到：那几条按「拿不准」留着，不连累本章其他内容。
                payload = {}
            keep += list(payload.get("keep") or [])
            drop += list(payload.get("drop") or [])
            supersede += list(payload.get("supersede") or [])
            unsure = list(payload.get("unsure") or []) if payload else unsure
        supersede = _repair_anchors(runner, session, supersede, events, ops, rounds)
        related, retire = _relation_round(
            runner, f"relation-{uuid4().hex}", data, keep, events, ops, rounds,
            text=source.get("text") or "",
        )
        keep += related
        result = _apply(
            data, keep, drop, rounds[0], unresolved=unsure, supersede=supersede
        )
        # 关系变了：模型点名了要撤哪条记录，跟着这一批一起落。
        result["retire"] = retire
        return result
    finally:
        runner.close()


def _answer_need(ops: GraphOperations, need: list) -> dict:
    """Only one kind of follow-up is supported: values for named properties."""
    snapshot = ops.graph_snapshot()
    nodes = {item["name"]: item for item in snapshot["nodes"]}
    events = {item["id"]: item.get("name", "") for item in snapshot["nodes"]
              if item["node_type"] == "event"}
    result: dict = {"requested": need, "values": []}
    for record in ops.property_records():
        if record["kind"] != "property_value":
            continue
        node = next((n for n in snapshot["nodes"] if n["id"] == record.get("node_id")), None)
        if node is None:
            continue
        result["values"].append({
            "node": node["name"],
            "property": record["property_id"],
            "value": record.get("value"),
            "at_event": events.get(record.get("event_id"), ""),
        })
    result["nodes"] = list(nodes)[:200]
    return result


def _apply(data: dict, keep: list, drop: list, rounds: int,
           unresolved: list | None = None, supersede: list | None = None) -> dict:
    """Rebuild the property list from the model's verdict."""
    proposed = {
        (prop["node"], prop["property"], str(prop["value"])): prop
        for prop in data.get("properties", [])
    }
    kept: list[dict] = []
    for item in keep:
        if not isinstance(item, dict):
            continue
        key = (item.get("node"), item.get("property"), str(item.get("value")))
        original = proposed.get(key)
        if original is None:
            # 后面几轮自己加的行（人物关系）没有对应提议，原样收下；
            # 其余对不上提议的条目（值写法有出入）照旧丢掉——它们没有引文和位置。
            if item.get("category") != "关系" or "start" not in item:
                continue
            merged = dict(item)
        else:
            merged = dict(original)
        if item.get("at_event"):
            merged["at_event"] = str(item["at_event"])
        kept.append(merged)
    return {
        "properties": kept,
        "dropped": [item for item in drop if isinstance(item, dict)],
        "unresolved": [item for item in (unresolved or []) if isinstance(item, dict)],
        # Old values this chapter says are over. Only a proposal: the anchor event
        # and the target record are resolved later, and anything unresolvable is
        # dropped rather than guessed.
        "supersede": [item for item in (supersede or []) if isinstance(item, dict)],
        "rounds": rounds,
        "stored_seen": True,
    }
