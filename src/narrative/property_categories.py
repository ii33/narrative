"""Display categories for property records.

A category answers "which aspect of the object does this sentence talk about".
It is not ``scope``: scope says whether a record is a world fact, someone's
cognition or someone's claim. A cognition record about an injury is still an
injury record, and a world record about clothing is still an appearance record.

Categories are deliberately loose and adjustable. The vocabulary is a per-node-
type list with a default for every type, stored as project config so it can be
renamed, extended or trimmed at any time. A record gets a category from the
first of these that applies:

1. an explicit ``property_categorization`` record — a human moved it;
2. the ``category`` the extractor wrote on the disclosure;
3. a name hint for the property (``height`` → 外貌);
4. a value hint for a generic name (``state`` = 身受重伤 → 能力与身体状态);
5. ``其他``.

Nothing is enforced at capture time: an unclassifiable record is stored under
``其他`` and stays there until someone moves it. Merging two property names is a
separate operation — a category move never renames anything.
"""

from __future__ import annotations

import json

CONFIG_KEY = "property_categories"
FALLBACK = "其他"

# Default vocabulary per node type. The last entry is always 其他; a saved config
# replaces the whole list for a type and 其他 is appended back.
DEFAULT_CATEGORIES: dict[str, list[str]] = {
    "person": [
        "基本信息", "外貌", "身份与经历", "能力与身体状态",
        "持有与使用", "关系", "心理与意图", FALLBACK,
    ],
    "item": [
        "基本信息", "外观与构造", "功能与特性", "状态",
        "归属与持有", "关联", FALLBACK,
    ],
    "group": [
        "基本信息", "构成与规模", "实力与资源", "立场与关系", "状态", FALLBACK,
    ],
    "location": [
        "基本信息", "环境与外观", "位置与范围", "势力与居住者", "状态", FALLBACK,
    ],
    # 事件只讲「发生了什么」，不挂状态；它那一栏留着，是因为老工程里还有。
    "event": ["基本信息", "参与者", "过程与结果", FALLBACK],
    # 场景是这一层唯一带属性的：它说的不是自己，而是「大事件走到这里的样子」。
    "scene": ["基本信息", "大事件", "内容", FALLBACK],
    # 大事件自己不写属性——界面上那一行是从场景借来的（见 node_properties）。
    "storyline": ["大事件", FALLBACK],
    "other": ["基本信息", FALLBACK],
}

# Property name → category. Names are the extractor's own vocabulary, so the map
# only has to cover the aspects that actually show up; anything missing falls
# through to 其他 instead of guessing.
_NAME_HINTS: dict[str, dict[str, str]] = {
    "person": {
        "name": "基本信息", "alias": "基本信息", "aliases": "基本信息",
        "gender": "基本信息", "sex": "基本信息", "age": "基本信息",
        "race": "基本信息", "species": "基本信息", "birthplace": "基本信息",
        "native_place": "基本信息",
        "height": "外貌", "weight": "外貌", "build": "外貌", "figure": "外貌",
        "appearance": "外貌", "face": "外貌", "skin": "外貌", "hair": "外貌",
        "eyes": "外貌", "clothing": "外貌", "attire": "外貌", "clothes": "外貌",
        "dress": "外貌", "look": "外貌",
        "identity": "身份与经历", "occupation": "身份与经历", "profession": "身份与经历",
        "job": "身份与经历", "title": "身份与经历", "role": "身份与经历",
        "position": "身份与经历", "rank": "身份与经历", "affiliation": "身份与经历",
        "organization": "身份与经历", "sect": "身份与经历", "school": "身份与经历",
        "origin": "身份与经历", "background": "身份与经历",
        "family_background": "身份与经历", "education": "身份与经历",
        # 属性名可以改成人话（`family_background` → 出身），推断也得认得这些写法，
        # 否则改名之后新披露会掉进「其他」，同一个方面分成两行。
        "出身": "身份与经历", "家世": "身份与经历", "背景": "身份与经历",
        "经历": "身份与经历", "学历": "身份与经历", "身份": "身份与经历",
        "职业": "身份与经历", "名声": "身份与经历", "来历": "身份与经历",
        "experience": "身份与经历", "escape_experience": "身份与经历",
        "reputation": "身份与经历", "fame": "身份与经历", "deed": "身份与经历",
        "treasure_loss": "身份与经历", "training_time": "身份与经历",
        "retreat_duration": "身份与经历",
        "cultivation": "能力与身体状态", "cultivation_stage": "能力与身体状态",
        "realm": "能力与身体状态", "skill": "能力与身体状态",
        "ability": "能力与身体状态", "talent": "能力与身体状态",
        "constitution": "能力与身体状态", "injury": "能力与身体状态",
        "health": "能力与身体状态", "hearing": "能力与身体状态",
        "vision": "能力与身体状态", "effort": "能力与身体状态",
        "armor_protection": "能力与身体状态",
        "holder": "持有与使用", "equipment": "持有与使用", "armor": "持有与使用",
        "weapon": "持有与使用", "assets": "持有与使用", "possession": "持有与使用",
        "belonging": "持有与使用",
        "relation": "关系", "relationship": "关系", "master": "关系",
        "disciple": "关系", "friend": "关系", "enemy": "关系",
        "spouse": "关系", "family": "关系", "member": "关系",
        "personality": "心理与意图", "plan": "心理与意图", "scheme": "心理与意图",
        "mission": "心理与意图", "belief": "心理与意图", "knowledge": "心理与意图",
        "attitude_toward_women": "心理与意图", "emotion": "心理与意图",
        "mood": "心理与意图", "desire": "心理与意图", "intention": "心理与意图",
        "assessment": "心理与意图",
    },
    "item": {
        "name": "基本信息", "alias": "基本信息", "aliases": "基本信息",
        "type": "基本信息", "kind": "基本信息", "source": "基本信息",
        "origin": "基本信息", "creator": "基本信息", "brand": "基本信息",
        "appearance": "外观与构造", "color": "外观与构造", "shape": "外观与构造",
        "size": "外观与构造", "diameter": "外观与构造", "mass": "外观与构造",
        "weight": "外观与构造", "material": "外观与构造", "structure": "外观与构造",
        "refinement": "外观与构造", "inscription": "外观与构造",
        "function": "功能与特性", "effect": "功能与特性", "usage": "功能与特性",
        "ability": "功能与特性", "power": "功能与特性", "feature": "功能与特性",
        "limit": "功能与特性", "condition": "功能与特性", "importance": "功能与特性",
        "trait": "功能与特性", "technology": "功能与特性",
        "holder": "归属与持有", "owner": "归属与持有", "keeper": "归属与持有",
        "user": "归属与持有",
        "content": "关联", "contents": "关联", "component": "关联", "part": "关联",
    },
    "group": {
        "name": "基本信息", "alias": "基本信息", "aliases": "基本信息",
        "type": "基本信息", "nature": "基本信息",
        "size": "构成与规模", "members": "构成与规模", "scale": "构成与规模",
        "structure": "构成与规模",
        "assets": "实力与资源", "power": "实力与资源", "resource": "实力与资源",
        "wealth": "实力与资源", "influence": "实力与资源",
        "relation": "立场与关系", "ally": "立场与关系", "enemy": "立场与关系",
        "position": "立场与关系", "affiliation": "立场与关系",
        "action": "状态",
    },
    "location": {
        "name": "基本信息", "alias": "基本信息", "aliases": "基本信息",
        "type": "基本信息", "diameter": "基本信息", "mass": "基本信息",
        "size": "基本信息",
        "landscape": "环境与外观", "scenery": "环境与外观",
        "climate": "环境与外观", "appearance": "环境与外观",
        "setting": "环境与外观", "technology": "环境与外观",
        "location": "位置与范围", "region": "位置与范围",
        "territory": "位置与范围",
        "inhabitant": "势力与居住者", "ruler": "势力与居住者",
        "population": "势力与居住者",
        "usage": "状态", "last_arrivals": "状态",
    },
    "event": {
        "name": "基本信息", "type": "基本信息", "time": "基本信息",
        "duration": "基本信息",
        "participants": "参与者", "actor": "参与者", "target": "参与者",
        "process": "过程与结果", "result": "过程与结果", "outcome": "过程与结果",
        "effect": "过程与结果",
    },
    "scene": {
        "storyline_progress": "大事件",
        "name": "基本信息", "location": "基本信息", "time": "基本信息",
        "content": "内容", "events": "内容",
    },
}

# Names that say nothing about the aspect: the value has to decide. Order
# matters — the first matching keyword wins, so the more specific ones go first.
_AMBIGUOUS = {"state", "status", "condition"}
_VALUE_HINTS: dict[str, tuple[tuple[tuple[str, ...], str], ...]] = {
    "person": (
        (("脸色", "面色", "面容", "光着", "衣物", "衣着", "头发", "皮肤", "衣服"), "外貌"),
        (("心急", "烦乱", "冷静", "气得", "发疯", "心情", "情绪", "害怕", "愤怒",
          "木然", "陌生", "高兴", "悲伤"), "心理与意图"),
        (("身体", "金光", "打坐", "恢复", "受伤", "重伤", "能量", "修为", "功力",
          "虚弱", "中毒", "昏"), "能力与身体状态"),
        (("大佬", "地位", "身份", "名声", "职务", "掌门"), "身份与经历"),
    ),
    "item": (
        (("折断", "打落", "破损", "损坏", "耗尽", "封存", "启动", "完好", "缩入",
          "半满", "碎裂"), "状态"),
    ),
    "location": (
        (("荒废", "废弃", "遗弃", "消失", "荒芜", "毁灭"), "状态"),
    ),
    "group": (
        (("诈骗", "逃", "破产", "空壳", "解散", "成立"), "状态"),
    ),
}

# A relation stated from one side is worth reading from the other. The mapping
# only covers the categories whose opposite name is genuinely different; the
# rest keep their own name when the viewer's vocabulary has it.
_INVERSE_CATEGORY: dict[str, dict[str, str]] = {
    "person": {"归属与持有": "持有与使用", "关联": "关系"},
}


def default_vocabulary() -> dict[str, list[str]]:
    return {node_type: list(values) for node_type, values in DEFAULT_CATEGORIES.items()}


def normalize_vocabulary(config: object) -> dict[str, list[str]]:
    """Merge a saved config over the defaults, dropping anything unusable.

    A saved list replaces the default list for that type. 其他 is always the last
    entry and cannot be removed: every record needs somewhere to land.
    """
    base = default_vocabulary()
    if not isinstance(config, dict):
        return base
    for node_type, values in config.items():
        if node_type not in base or not isinstance(values, list):
            continue
        kept: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            text = value.strip()
            if text and text != FALLBACK and text not in kept:
                kept.append(text)
        if kept:
            base[node_type] = kept + [FALLBACK]
    return base


def validate_vocabulary(config: object) -> dict[str, list[str]]:
    """Reject a malformed config instead of silently repairing it on save."""
    if not isinstance(config, dict) or not config:
        raise ValueError("分类配置须为按节点类型分组的对象")
    for node_type, values in config.items():
        if node_type not in DEFAULT_CATEGORIES:
            raise ValueError(f"未知节点类型：{node_type}")
        if not isinstance(values, list):
            raise ValueError(f"{node_type} 的分类须为字符串数组")
        names = [value.strip() for value in values if isinstance(value, str)]
        if len(names) != len(values):
            raise ValueError(f"{node_type} 的分类须全部是字符串")
        if any(not name for name in names):
            raise ValueError(f"{node_type} 的分类名不能为空")
        if any(len(name) > 20 for name in names):
            raise ValueError(f"{node_type} 的分类名不能超过 20 字")
        if len(names) != len(set(names)):
            raise ValueError(f"{node_type} 的分类名重复")
    return normalize_vocabulary(config)


def vocabulary_for(node_type: str | None, config: object = None) -> list[str]:
    base = normalize_vocabulary(config)
    return base.get(node_type or "", base["other"])


def suggest(
    property_id: str, node_type: str | None, value: object = None, config: object = None
) -> tuple[str, str]:
    """A category for one disclosure, plus who chose it (``name``/``value``)."""
    vocabulary = vocabulary_for(node_type, config)
    name = (property_id or "").strip().lower()
    hint = _NAME_HINTS.get(node_type or "", {}).get(name)
    if hint and hint in vocabulary:
        return hint, "name"
    if name in _AMBIGUOUS:
        text = "" if value is None else str(value)
        for keywords, category in _VALUE_HINTS.get(node_type or "", ()):
            if category in vocabulary and any(word in text for word in keywords):
                return category, "value"
    return FALLBACK, "fallback"


def resolve(
    record: dict,
    node_type: str | None,
    config: object = None,
    overrides: dict[str, str] | None = None,
) -> tuple[str, str]:
    """The category of one value record and where the decision came from.

    ``manual`` is a categorization record, ``extraction`` the category the model
    wrote with the disclosure, then the name/value hints, then the fallback.
    """
    record_id = record.get("id")
    if overrides and isinstance(record_id, str) and record_id in overrides:
        return overrides[record_id], "manual"
    explicit = record.get("category")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip(), "extraction"
    return suggest(str(record.get("property_id") or ""), node_type, record.get("value"), config)


def aspect_of(property_id: str) -> str:
    """The aspect a property name points at.

    `cultivation` and `cultivation_stage` are two names for one subject, and a
    reader wants them on one line with one current value. The first token is the
    aspect; `state`, `status` and `statement` stay apart because they share no
    prefix. Grouping by aspect never renames anything — the merge is a decision
    the audit still asks a human to make.
    """
    name = (property_id or "").strip().lower()
    # 「恋人（冷战中）」是同一个方面的两种状态：括号里的不算新方面。
    for mark in ("（", "(", "[", "【"):
        if mark in name:
            name = name.split(mark)[0]
    return name.strip().split("_")[0] if name else ""


def inverse_category(source_category: str, viewer_type: str | None, config: object = None) -> str:
    """Where the other end of a relation shows the same record."""
    mapped = _INVERSE_CATEGORY.get(viewer_type or "", {}).get(source_category, source_category)
    return mapped if mapped in vocabulary_for(viewer_type, config) else FALLBACK


def renames(previous: object, current: object) -> dict[tuple[str, str], str]:
    """Category renames between two vocabularies, as ``(node_type, old) -> new``.

    Renaming a category would otherwise silently drop its records back into 其他,
    because a derived category is only as good as the name it was derived from.
    A one-for-one replacement inside a type is treated as a rename; a wholesale
    rewrite is not guessed at.
    """
    before_all = normalize_vocabulary(previous)
    after_all = normalize_vocabulary(current)
    result: dict[tuple[str, str], str] = {}
    for node_type, names in after_all.items():
        before = [name for name in before_all.get(node_type, []) if name != FALLBACK]
        after = [name for name in names if name != FALLBACK]
        gone = [name for name in before if name not in after]
        added = [name for name in after if name not in before]
        if not gone or len(gone) != len(added) or len(gone) > 4:
            continue
        for old_name, new_name in zip(gone, added, strict=True):
            result[(node_type, old_name)] = new_name
    return result


def order_categories(
    categories: list[str], node_type: str | None, config: object = None
) -> list[str]:
    """Vocabulary order first, then anything unknown, with 其他 always last."""
    vocabulary = vocabulary_for(node_type, config)
    known = [name for name in vocabulary if name != FALLBACK and name in categories]
    unknown = sorted(name for name in categories if name not in vocabulary)
    tail = [FALLBACK] if FALLBACK in categories else []
    return known + unknown + tail


def load(store) -> dict[str, list[str]]:
    """Read the project's vocabulary, falling back to the defaults."""
    raw = store.meta(CONFIG_KEY)
    if not raw:
        return default_vocabulary()
    try:
        return normalize_vocabulary(json.loads(raw))
    except json.JSONDecodeError:
        return default_vocabulary()


def save(store, config: object) -> dict[str, list[str]]:
    vocabulary = validate_vocabulary(config)
    store.set_meta(CONFIG_KEY, json.dumps(vocabulary, ensure_ascii=False))
    return vocabulary
