"""Current property values with intrinsic disclosure intervals and direct corrections."""

from __future__ import annotations

import json
from pathlib import Path

from . import property_categories, property_labels
from .graph_store import GraphStore
from .order_ids import order_key

PROPERTY_CONTRACT_VERSION = "property-state/14"
PROPERTY_RECORD_KINDS = (
    "property_definition",
    "property_value",
)
PROPERTY_EFFECT_OPS = ("set", "add", "remove", "unknown", "absent")
PROPERTY_STATE_CELL_KINDS = ("known", "unknown", "absent", "conflict", "unrecorded")


def _core():
    """Load the pure core lazily so storage does not import it at module load."""
    from . import property_core

    return property_core


def _issue(code: str, message: str, object_id: str | None = None, severity: str = "error") -> dict:
    return {"code": code, "severity": severity, "object_id": object_id, "message": message}


# Sorts after every real offset inside one event.
_AFTER_EVENT = 10**12


def record_position(record: dict) -> tuple:
    """Where a record sits on the disclosure chain.

    Event number first, then the record's own offset inside that event. One
    anchor can hold several disclosures (a retelling, a correction), and their
    order is the order of the source text, not the write order.
    """
    anchor = record.get("event_id")
    key = order_key(anchor) if isinstance(anchor, str) else None
    disclosure = record.get("disclosure")
    offset = disclosure.get("start") if isinstance(disclosure, dict) else None
    return (key or (999,), offset if isinstance(offset, int) else 0)


def _statement_position(record: dict) -> tuple:
    """一条失效/恢复记录落在披露链的哪里。

    没有 ``event_id`` 的撤回是「读错了，从来不算」，所以排在任何披露之前；
    其余按 (事件编号, 该事件内的位置) 排。
    """
    if not isinstance(record.get("event_id"), str):
        return ((0,), 0)
    return record_position(record)


def _statement_event(record: dict) -> tuple:
    """一条失效/恢复落在哪个事件上（不算事件内的偏移）。

    判「这句话是不是已经说过了」只看事件：同一事件里的先后只影响记录自己的排序。
    没有 ``event_id`` 的撤回是「从来不算」，排在最前。
    """
    anchor = record.get("event_id")
    if not isinstance(anchor, str):
        return (0,)
    return order_key(anchor) or (0,)


def _applies(record: dict, cut: tuple | None) -> bool:
    """这条记录在当前位置算不算数：有位置的就看位置，没位置的（读错了）到处都算。"""
    anchor = record.get("event_id")
    if isinstance(anchor, str) and cut is not None:
        return record_position(record) <= cut
    return True


def invalid_at(records: list[dict], cut: tuple | None) -> dict[str, dict]:
    """Ended value intervals at the requested disclosure cut."""
    result = {}
    for row in records:
        end = row.get("valid_to")
        if row.get("kind") != "property_value" or not isinstance(end, dict):
            continue
        boundary = {"event_id": end.get("event_id"), "disclosure": end}
        if cut is None or record_position(boundary) <= cut:
            result[row["id"]] = {"kind": "interval_end", "boundary": end}
    return result


def relation_target(
    record: dict, nodes: dict[str, dict] | None = None,
    definitions: dict[str, dict] | None = None,
) -> str | None:
    """A relationship has an independent value sequence for each counterpart.

    Scalar node references such as an item's holder remain single-valued.
    """
    value = record.get("value")
    if not isinstance(value, str):
        return None
    definition = (definitions or {}).get(str(record.get("property_id") or "")) or {}
    value_type = record.get("value_type") or definition.get("value_type")
    if nodes and nodes.get(str(record.get("node_id") or ""), {}).get("node_type") == "person" \
            and nodes.get(value, {}).get("node_type") == "person":
        return value if value_type in {"node", "either"} else None
    if record.get("category") == "关系" and value_type in {"node", "either"}:
        return value
    return None


def _categorization_overrides(records: list[dict]) -> dict[str, str]:
    """Current category of each moved record: the last accepted move wins."""
    overrides: dict[str, str] = {}
    for record in records:
        if record.get("kind") != "property_categorization":
            continue
        target, category = record.get("target_id"), record.get("category")
        if isinstance(target, str) and isinstance(category, str) and category.strip():
            overrides[target] = category.strip()
    return overrides


class PropertyStore:
    """Current value intervals and their read projections."""

    def __init__(self, store: GraphStore | str | Path, *, create: bool = False):
        self.store = store if isinstance(store, GraphStore) else GraphStore(store, create=create)

    # -- derived reads -------------------------------------------------
    @staticmethod
    def _records_from_db(db) -> list[dict]:
        return [json.loads(row["data"]) for row in db.execute(
            "SELECT data FROM property_records ORDER BY rowid")]

    def accepted_records(self) -> list[dict]:
        """Current definitions plus intrinsic value intervals; no revision replay."""
        with self.store.connect() as db:
            return self._records_from_db(db)

    # -- validation ----------------------------------------------------





    def precheck(self, records: list[dict], expected_revision: int) -> list[dict]:
        from .batch_write import capture, prepare
        snapshot = capture(self.store)
        return prepare(snapshot, [], records, expected_revision).diagnostics







    # -- projections ---------------------------------------------------
    @staticmethod
    def _disclosure_status(record: dict, cut: dict | None, source_order: dict[str, int]) -> str:
        if cut is None:
            return "not_evaluated"
        disclosure = record.get("disclosure")
        if disclosure is None:
            return "unknown"
        source_id = disclosure.get("source_id")
        if not isinstance(source_id, str):
            return "unknown"
        left = source_order.get(source_id)
        cut_source_id = cut.get("source_id")
        if not isinstance(cut_source_id, str):
            return "unknown"
        right = source_order.get(cut_source_id)
        if left is None or right is None:
            return "unknown"
        if left < right or left == right and disclosure.get("start", 0) <= cut.get("start", 0):
            return "revealed"
        return "hidden"

    def _visible_records(
        self,
        records: list[dict],
        snapshot: dict,
        disclosure_cut: dict | None,
    ) -> tuple[list[dict], dict]:
        if disclosure_cut is None:
            return records, {"cut": None, "hidden": [], "unknown": []}
        source_order = {
            row["source_id"]: row["ordinal"]
            for row in snapshot.get("source_orders", [])
            if isinstance(row, dict) and "source_id" in row and "ordinal" in row
        }
        visible: list[dict] = []
        hidden: list[str] = []
        unknown: list[str] = []
        for record in records:
            if record.get("kind") in {"property_definition"}:
                visible.append(record)
                continue
            status = self._disclosure_status(record, disclosure_cut, source_order)
            if status == "revealed":
                visible.append(record)
            elif status == "hidden":
                if isinstance(record.get("id"), str):
                    hidden.append(record["id"])
            else:
                if isinstance(record.get("id"), str):
                    unknown.append(record["id"])
        return visible, {"cut": disclosure_cut, "hidden": hidden, "unknown": unknown}

    def state(
        self,
        node_id: str,
        property_id: str,
        at=None,
        *,
        scope: str = "world",
        holder_id: str | None = None,
        disclosure_cut: dict | None = None,
        include_diagnostics: bool = False,
    ) -> dict:
        if scope not in {"world", "cognition", "claim"}:
            raise ValueError("scope 须为 world、cognition 或 claim")
        if scope == "world" and holder_id is not None:
            raise ValueError("world 范围不能带认知持有者")
        if scope != "world" and not holder_id:
            raise ValueError("认知查询须提供 holder_id")
        snapshot = self.store.snapshot()
        records = self.accepted_records()
        visible, disclosure = self._visible_records(records, snapshot, disclosure_cut)
        # `at` is a disclosure checkpoint (an event id) or nothing at all: the
        # property has no value outside a reading position.
        from .temporal_state import interval_state
        result = interval_state(self.store, node_id, property_id, at if at is not None else disclosure_cut,
                                scope=scope, holder_id=holder_id)
        if not include_diagnostics and disclosure_cut is not None:
            disclosure = {
                "cut": disclosure["cut"],
                "restricted": True,
            }
        return result | {"disclosure": disclosure}

    def disclosure_state(
        self,
        node_id: str,
        property_id: str,
        disclosure_cut: dict,
        *,
        scope: str = "world",
        holder_id: str | None = None,
    ) -> dict:
        """Project a state from records revealed up to a source position."""
        if scope not in {"world", "cognition", "claim"}:
            raise ValueError("scope 须为 world、cognition 或 claim")
        if scope == "world" and holder_id is not None:
            raise ValueError("world 范围不能带认知持有者")
        if scope != "world" and not holder_id:
            raise ValueError("认知查询须提供 holder_id")
        from .temporal_state import interval_state
        return interval_state(self.store, node_id, property_id, disclosure_cut, scope=scope, holder_id=holder_id)

    def _cut_position(self, cut: object, revision: int | None = None) -> tuple | None:
        """Turn a reading cut into a position on the chain.

        An event id means "after reading that event", so everything anchored there
        counts. A raw source offset is finer: it also excludes disclosures that sit
        later in the same event.
        """
        if isinstance(cut, str):
            key = order_key(cut)
            return (key, _AFTER_EVENT) if key else None
        if not isinstance(cut, dict) or not isinstance(cut.get("start"), int):
            return None
        offset = cut["start"]
        source_id = cut.get("source_id")
        best: tuple | None = None
        for node in self.store.snapshot().get("nodes", []):
            disclosure = node.get("disclosure")
            if not isinstance(disclosure, dict) or disclosure.get("source_id") != source_id:
                continue
            start = disclosure.get("start")
            if not isinstance(start, int) or start > offset:
                continue
            key = order_key(node["id"])
            if key and (best is None or key > best[0]):
                best = (key, offset)
        return best


    def history(
        self,
        node_id: str,
        property_id: str,
        *,
        scope: str = "world",
        holder_id: str | None = None,
    ) -> list[dict]:
        if scope not in {"world", "cognition", "claim"}:
            raise ValueError("scope 须为 world、cognition 或 claim")
        if scope == "world" and holder_id is not None:
            raise ValueError("world 范围不能带认知持有者")
        if scope != "world" and not holder_id:
            raise ValueError("认知查询须提供 holder_id")
        # Value history consists of the surviving intrinsic intervals.
        kwargs = (
            {"scope": scope, "holder_id": holder_id}
            if scope != "world" or holder_id is not None
            else {}
        )
        records = self.accepted_records()
        return _core().history(records, node_id, property_id, **kwargs)

    def audit(
        self,
        node_ids: set[str] | None = None,
    ) -> dict:
        """Deterministic audit of accepted records; nothing is modified."""
        from .property_audit import audit_properties

        snapshot = self.store.snapshot()
        # Audit current value intervals; ended rows are retained as time history.
        records = self.accepted_records()
        # An alias that was merged is no longer a separate name: auditing it as
        # one would keep reporting the split the human already resolved.
        return audit_properties(
            records,
            snapshot.get("nodes", []),
            node_ids=node_ids,
            categories=property_categories.load(self.store),
        )

    # -- display categories --------------------------------------------
    def categories(self) -> dict[str, list[str]]:
        """The project's category vocabulary, one list per node type."""
        return property_categories.load(self.store)

    def set_categories(self, config: object) -> dict:
        """Replace the vocabulary.

        A rename is migrated: without it, every record whose category was derived
        from the old name would quietly drop into 其他, which looks like the
        rename lost data. Selected value categories are edited directly, just as a manual move does.
        """
        previous = property_categories.load(self.store)
        vocabulary = property_categories.save(self.store, config)
        renamed = property_categories.renames(previous, vocabulary)
        moved: list[str] = []
        if renamed:
            records = self.accepted_records()
            nodes = {node["id"]: node for node in self.store.snapshot().get("nodes", [])}
            overrides = _categorization_overrides(records)
            pairs = []
            for record in records:
                if record.get("kind") != "property_value":
                    continue
                node_type = (nodes.get(record.get("node_id")) or {}).get("node_type")
                if not isinstance(node_type, str):
                    continue
                category, _ = property_categories.resolve(record, node_type, previous, overrides)
                target = renamed.get((node_type, category))
                if target:
                    pairs.append((str(record.get("id")), target))
            if pairs:
                moved = self._apply_categorizations(pairs, "分类改名迁移")["moved"]
        return {"vocabulary": vocabulary, "moved": moved}

    def _apply_categorizations(self, pairs: list[tuple[str, str]], note: str) -> dict:
        from .temporal_state import persist
        snapshot = self.store.snapshot()
        records = self.accepted_records()
        categories = dict(pairs)
        for row in records:
            if row["id"] in categories and row.get("kind") == "property_value":
                row["category"] = categories[row["id"]]
        revision = persist(self.store, snapshot, records, expected_revision=snapshot["revision"],
                           action="categorize", reason=note)
        return {"revision": revision, "moved": list(categories),
                "category": next(iter(categories.values()), "")}

    def categorize(
        self, targets: list[str], category: str, note: str = ""
    ) -> dict:
        """Move disclosures to a category.

        Moving is a human editorial act, so it is applied immediately — but it is
        a direct edit of the value category; no editorial record is appended.
        """
        category = (category or "").strip()
        if not category:
            raise ValueError("分类不能为空")
        if len(category) > 20:
            raise ValueError("分类名不能超过 20 字")
        if not isinstance(targets, list) or not targets:
            raise ValueError("请提供要移动的记录")
        accepted = {
            record["id"]: record
            for record in self.accepted_records()
            if isinstance(record.get("id"), str)
        }
        unknown = [target for target in targets if target not in accepted]
        if unknown:
            raise ValueError(f"记录不存在：{'、'.join(unknown[:3])}")
        wrong = [
            target for target in targets
            if accepted[target].get("kind") != "property_value"
        ]
        if wrong:
            raise ValueError("只有属性取值记录可以移动分类")
        result = self._apply_categorizations(
            [(target, category) for target in dict.fromkeys(targets)],
            note or f"移动分类：{category}",
        )
        return result | {"category": category}






    def _container_progress(
        self,
        node_id: str,
        snapshot: dict,
        all_records: list[dict],
        cut_position: tuple | None,
    ) -> list[dict]:
        """父级读取直属成员的进展，不复制持久记录或跨层借用。"""
        nodes = {node["id"]: node for node in snapshot.get("nodes", [])}
        story = nodes.get(node_id) or {}
        level = {"storyline": ("big_event", property_labels.STORYLINE_PROGRESS, "主要故事"),
                 "big_event": ("scene", "big_event_progress", "大事件")}.get(str(story.get("node_type") or ""))
        if not level:
            return []
        child_kind, progress_property, category = level
        scenes = {
            child for child in story.get("events") or []
            if child in nodes and nodes[child].get("node_type") == child_kind and not nodes[child].get("retracted")
        }
        if not scenes:
            return []
        from .temporal_state import point, position
        expired = {row['id'] for row in all_records if row.get('valid_to') and
                   (cut_position is None or (position(row['valid_to'], snapshot) or (float('inf'), 0)) <= cut_position)}
        def key_of(row):
            return position(point(row), snapshot) or (-1, -1)
        candidates = [
            record for record in all_records
            if record.get("kind") == "property_value"
            and record.get("node_id") in scenes
            and str(record.get("property_id") or "") == progress_property
            and record.get("id") not in expired
            and (cut_position is None or key_of(record) <= cut_position)
        ]
        if not candidates:
            return []
        latest = max(candidates, key=key_of)
        return [{
            "id": f"derived:{latest.get('node_id')}",
            "kind": "property_value",
            "node_id": node_id,
            "property_id": progress_property,
            "value": latest.get("value"),
            "value_type": latest.get("value_type") or "string",
            "event_id": latest.get("event_id"),
            "evidence": latest.get("evidence") or {},
            "disclosure": latest.get("disclosure") or {},
            "source": latest.get("source") or "narration",
            "scope": "world",
            "category": category,
            "derived": True,
            "derived_from": latest.get("node_id"),
        }]

    def node_properties(
        self,
        node_id: str,
        cut: object = None,
        include_derived: bool = False,
    ) -> dict:
        """Every property a node carries, grouped for reading.

        One record is one disclosure, so a property can hold several — a
        restatement, a correction, a contradiction. Records are ordered by
        (event number, own source position) and the last one is what the reader
        saw last, which is not the same as the last one written.

        Rows are one aspect inside one display category. The aspect is the first
        token of the property name, so `cultivation` and `cultivation_stage` share
        a line and their three realms read as one sequence with one current value
        instead of three simultaneous truths. Category is resolved per record: a
        human move wins, then what the extractor wrote, then a name/value hint,
        then 其他; a record whose category differs from its neighbours is shown
        under both headings, each carrying only its own records.

        **一条记录只有一件事要回答：还算不算数。** 失效的既不删除也不标记，
        直接不出现——`valid` 是唯一的那个字段，行上不再有失效条数、失效于哪次披露、
        失效原因这些派生的说法。
        """
        snapshot = self.store.snapshot()
        # Read value intervals, then apply their explicit source boundaries.
        all_records = self.accepted_records()
        # Reading cuts select which intrinsic intervals hold at this point.
        from .temporal_state import boundary_at, point
        from .temporal_state import position as source_position
        cut_position = source_position(boundary_at(snapshot, cut), snapshot) if cut else None
        # 大事件自己没有属性记录：它此刻的样子是它下面最后一个**读过的**场景说的
        # 那句话。借一条记录过来显示，行就有了正常的 records / 披露 / 当前值，
        # 编辑器不请求派生行，所以不会有人去改一条不存在的记录。
        if include_derived:
            all_records = all_records + self._container_progress(
                node_id, snapshot, all_records, cut_position
            )
        # Validity lives on each value as valid_from/valid_to.
        from .temporal_state import ended_at
        invalid = ended_at(all_records, snapshot, cut)
        records = all_records
        config = property_categories.load(self.store)
        # A move, a merge or a rename that has been invalidated stops applying:
        # these are editorial records too, and taking one back must restore what
        # came before. So names are resolved from what still stands.
        editorial = [item for item in records if item.get("id") not in invalid]
        # Definitions are keyed by the name the reader sees, so a renamed aspect
        # keeps its shape (a node-valued property stays node-valued) and a merged
        # alias no longer counts as a definition of its own.
        definitions = _core().canonical_definitions(editorial)
        # Names are resolved here so a reader never sees a bare id: the event a
        # disclosure hangs on, and every node the record points at.
        nodes = {node["id"]: node for node in snapshot.get("nodes", [])}
        names = {node_id_: node.get("name") or node_id_ for node_id_, node in nodes.items()}
        # The position alone is ambiguous once a property spans chapters: 3611 in
        # chapter two and 3479 in chapter three are not comparable numbers.
        source_titles = {
            source["id"]: source.get("title") or source["id"]
            for source in snapshot.get("sources", [])
            if isinstance(source, dict) and isinstance(source.get("id"), str)
        }
        node = nodes.get(node_id, {})
        node_type = node.get("node_type")
        # A move is one record pointing at one disclosure; the last accepted one
        # is the current grouping, so moving twice keeps the second answer.
        overrides = _categorization_overrides(editorial)
        # A merged alias reads as its canonical name everywhere: one aspect, one
        # line, one label, without rewriting the records that used the old name.

        def node_ref(value: object) -> dict | None:
            """A node-valued property stores an id; the reader wants the name."""
            if not isinstance(value, str):
                return None
            name = names.get(value)
            return {"id": value, "name": name} if name else None

        def position(record: dict) -> tuple:
            return source_position(point(record), snapshot) or (-1, -1)

        # One line per aspect and category. The aspect is the first token of the
        # property name, so `cultivation` and `cultivation_stage` share a line:
        # they describe the same subject, and splitting them is what made three
        # realms look like three simultaneous truths. Scope is a label on each
        # record rather than a reason to split the line — a claim about the realm
        # and the realm itself are the same subject, told differently.
        lines: dict[tuple, list[dict]] = {}
        for record in all_records:
            # Only actual value intervals sit on a line; metadata has no aspect of its
            # own — it points at a value and is applied through `invalid_at`.
            if record.get("kind") != "property_value":
                continue
            if record.get("node_id") != node_id:
                continue
            # 「老周说他到了灵寂期」不是他的属性，是老周的说法：记在被说的节点上，
            # 但显示在说话的人那里（见下面的 claims）。他自己对自己的认知留着。
            holder = record.get("holder_id")
            if (record.get("scope") or "world") != "world" and holder and holder != node_id:
                continue
            category, category_source = property_categories.resolve(
                record, node_type, config, overrides
            )
            raw_name = record.get("property_id")
            name = raw_name if isinstance(raw_name, str) else ""
            aspect = property_categories.aspect_of(name)
            target = relation_target(record | {"category": category, "property_id": name}, nodes, definitions)
            sequence = (aspect, target, record.get("scope") or "world", holder) if target else aspect
            lines.setdefault((sequence, category), []).append({
                "record": record,
                "property_id": name,
                "category_source": category_source,
            })
        # A slice with nothing left is not shown at all, so it must not be
        # advertised either: "也在「其他」中" has to point at a row that exists.
        shown_categories: dict[object, set[str]] = {}

        items: list[dict] = []
        hidden_total = 0
        # One aspect has one state, even when its records were classified into two
        # categories. Which record holds it is decided here, across slices, so the
        # other slice cannot claim a current value of its own.
        holding: dict[object, tuple[tuple, str]] = {}
        for (aspect_key, category_key), entries in lines.items():
            for entry in entries:
                record_id = entry["record"].get("id")
                key = position(entry["record"])
                # Only the disclosures the reader has already reached count.
                if cut_position is not None and key > cut_position:
                    continue
                if record_id not in invalid:
                    if aspect_key not in holding or key > holding[aspect_key][0]:
                        holding[aspect_key] = (key, record_id)
                    shown_categories.setdefault(aspect_key, set()).add(category_key)

        for (sequence, category), line in lines.items():
            aspect = sequence[0] if isinstance(sequence, tuple) else sequence
            ordered = sorted(line, key=lambda entry: position(entry["record"]))
            later = 0
            if cut_position is not None:
                visible = [
                    entry for entry in ordered
                    if position(entry["record"]) <= cut_position
                ]
                later = len(ordered) - len(visible)
                hidden_total += later
                ordered = visible
                if not ordered:
                    continue
            # 失效的不显示：它还在库里（撤回不删行），只是这里不出现。
            kept = [
                entry for entry in ordered
                if entry["record"].get("id") not in invalid
            ]
            if not kept:
                continue
            ordered = kept
            property_ids = sorted({entry["property_id"] for entry in ordered})
            definition = definitions.get(ordered[-1]["property_id"]) or {}
            node_valued = definition.get("value_type") in {"node", "either"}
            decorated: list[dict] = []
            for entry in ordered:
                row = entry["record"]
                decorated.append({
                    "id": row.get("id"),
                    "kind": row.get("kind"),
                    "value": row.get("value"),
                    "value_node": node_ref(row.get("value")) if node_valued else None,
                    "event_id": row.get("event_id"),
                    "event_name": names.get(row.get("event_id") or ""),
                    "disclosure": row.get("disclosure"),
                    "source_title": source_titles.get(
                        (row.get("disclosure") or {}).get("source_id") or ""
                    ),
                    "source": row.get("source"),
                    "anchor_basis": row.get("anchor_basis"),
                    "scope": (row.get("scope") or "world"),
                    "holder_id": row.get("holder_id"),
                    "holder_name": names.get(row.get("holder_id") or ""),
                    "related": [
                        {"id": related_id, "name": names.get(related_id) or related_id}
                        for related_id in (row.get("related_nodes") or [])
                    ],
                    "evidence": row.get("evidence"),
                    "property_id": entry["property_id"],
                    "property_label": property_labels.label_for(entry["property_id"]),
                    "category": category,
                    "category_source": entry["category_source"],
                    # 唯一的有效性字段。走到这里的记录都还算数。
                    "valid": True,
                    # 大事件那一行是从场景借来的：界面上要能说清出处。
                    **(
                        {"derived": True, "derived_from": row.get("derived_from")}
                        if row.get("derived")
                        else {}
                    ),
                })
            # 一条线只有一个「最新披露」：更早的仍然算数（他确实这么说过、这么想过），
            # 只是不再是当下状态。界面上据此把旧的压暗，而不是当成另一个当前值。
            latest = decorated[-1]
            for record in decorated:
                record["is_latest"] = record["id"] == latest["id"]
            current = latest
            headline = latest
            # Only the slice holding the aspect's state is current; the other one is
            # the same aspect seen through another category, so it says it is
            # history. Two values disclosed at the same position are both current —
            # that is one sentence saying two things, not a progression.
            current_at = next(
                (position(entry["record"]) for entry in ordered
                 if current and entry["record"].get("id") == current["id"]),
                None,
            )
            is_current = bool(
                current and current_at == holding.get(sequence, (None, ""))[0]
            )
            # The label names the aspect, not the newest spelling of it: a line
            # holding `cultivation` and `cultivation_stage` is 修为.
            label_id = aspect if aspect in property_ids else latest["property_id"]
            items.append({
                "property_id": latest["property_id"],
                "property_ids": property_ids,
                "aspect": aspect,
                "label": property_labels.label_for(label_id),
                "category": category,
                "category_source": latest["category_source"],
                # The same aspect may sit in another category too; the reader is
                # told rather than shown a silently partial list.
                "other_categories": sorted(
                    shown_categories.get(sequence, set()) - {category}
                ),
                "value_node": headline["value_node"],
                "scope": headline["scope"],
                "scopes": sorted({row["scope"] for row in decorated}),
                "holder_id": headline["holder_id"],
                "holder_name": headline["holder_name"],
                "value_type": definition.get("value_type"),
                "value": headline["value"],
                "event_id": headline["event_id"],
                "start": (headline["disclosure"] or {}).get("start"),
                "disclosed_at": headline["event_name"],
                "is_current": is_current,
                "record_count": len(decorated),
                # Disclosures after the cut: the pane says how many it is not
                # showing instead of pretending this is everything.
                "later_count": later,
                "records": decorated,
            })
        ordered_categories = property_categories.order_categories(
            sorted({item["category"] for item in items}), node_type, config
        )
        items.sort(key=lambda item: (
            ordered_categories.index(item["category"]), item["label"], item["aspect"],
        ))

        from .item_possession import possession_rows

        holder_property, world_holders, _ = possession_rows([row for row in all_records if row.get("id") not in invalid], cut_position,
                                                                         position_key=position)
        if node_type == "item" and node_id not in world_holders:
            # A missing record is not a world fact saying "未知". At an earlier
            # reading cut it is undisclosed, and no future owner is exposed.
            items.append({
                "property_id": holder_property, "property_ids": [holder_property],
                "aspect": holder_property, "label": property_labels.label_for(holder_property),
                "category": "归属与持有", "category_source": "inferred",
                "other_categories": [], "value_node": None, "scope": "world", "scopes": ["world"],
                "holder_id": None, "holder_name": "", "value_type": "either",
                "value": "未披露" if cut_position is not None else "未记录",
                "event_id": None, "start": None, "disclosed_at": "",
                "is_current": False, "record_count": 0, "later_count": 0, "records": [],
                "derived": True, "state": "unrecorded",
            })

        # The other end of a relation: a record stored on another node whose
        # value points here. Same record, second entrance — nothing is copied.
        inbound: list[dict] = []
        for record in records:
            if record.get("kind") != "property_value" or record.get("node_id") == node_id:
                continue
            # 失效的记录不再是「谁持有它」：换手之后旧持有者不该还挂在反向上。
            if record.get("id") in invalid:
                continue
            # The other end of a relation follows the same reading position: a
            # holder the reader has not met yet must not appear on this page.
            if cut_position is not None and position(record) > cut_position:
                continue
            value = record.get("value")
            hit = value == node_id or (
                isinstance(value, list) and node_id in value
            )
            if not hit or not record.get("node_id"):
                continue
            owner_type = nodes.get(record["node_id"], {}).get("node_type")
            category, category_source = property_categories.resolve(
                record, owner_type, config, overrides
            )
            raw_name = record.get("property_id")
            name = raw_name if isinstance(raw_name, str) else ""
            inbound.append({
                "record_id": record.get("id"),
                "property_id": name,
                "property_label": property_labels.label_for(name),
                "category": property_categories.inverse_category(category, node_type, config),
                "source_category": category,
                "category_source": category_source,
                "node_id": record["node_id"],
                "node_name": names.get(record["node_id"]) or record["node_id"],
                "node_type": owner_type,
                "value": value,
                "event_id": record.get("event_id"),
                "event_name": names.get(record.get("event_id") or ""),
                "start": (record.get("disclosure") or {}).get("start"),
                "scope": (record.get("scope") or "world"),
                "holder_id": record.get("holder_id"),
                "holder_name": names.get(record.get("holder_id") or ""),
                "source": record.get("source"),
                "evidence": record.get("evidence"),
            })
        # 同一件东西可能有好几条持有者记录（重申、换手后又换回）：只列最新的那条，
        # 否则「反向引用」里会出现同一个名字三遍。
        deduped: dict[tuple, dict] = {}
        for row in inbound:
            key = (row["node_id"], row["property_id"])
            deduped[key] = row
        inbound = list(deduped.values())
        inbound.sort(key=lambda item: (item["category"], item["node_name"], item["property_id"]))

        # 反过来：这个人是**认知者**。他说的、他以为的关于别人的事，记在别人节点上，
        # 显示在他这里——被说的那个人的页面上不该出现。
        claims: list[dict] = []
        for record in records:
            if record.get("kind") != "property_value" or record.get("id") in invalid:
                continue
            # 读者还没读到这一步，就不该知道他说过什么。
            if cut_position is not None and position(record) > cut_position:
                continue
            if str(record.get("holder_id") or "") != node_id:
                continue
            subject = str(record.get("node_id") or "")
            if not subject or subject == node_id:
                continue
            scope = record.get("scope") or "world"
            if scope == "world":
                continue
            raw_name = record.get("property_id")
            name = raw_name if isinstance(raw_name, str) else ""
            claims.append({
                "record_id": record.get("id"),
                "node_id": subject,
                "node_name": names.get(subject) or subject,
                "property_id": name,
                "property_label": property_labels.label_for(name),
                "value": record.get("value"),
                "scope": scope,
                "scope_label": "声称" if scope == "claim" else "认知",
                "event_id": record.get("event_id"),
                "event_name": names.get(record.get("event_id") or ""),
                "evidence": record.get("evidence"),
            })
        claims.sort(key=lambda item: (item["node_name"], item["property_id"]))

        # 别人说的关于本节点的话：不进「属性」（那不是它自己的状态），但要在这一页
        # 看得见——否则「纳芥手镯 · 功能 = 储物」会凭空消失。谁说的写在行上。
        heard: list[dict] = []
        for record in records:
            if record.get("kind") != "property_value" or record.get("id") in invalid:
                continue
            if str(record.get("node_id") or "") != node_id:
                continue
            scope = record.get("scope") or "world"
            holder = str(record.get("holder_id") or "")
            if scope == "world" or not holder or holder == node_id:
                continue
            if cut_position is not None and position(record) > cut_position:
                continue
            raw_name = record.get("property_id")
            name = raw_name if isinstance(raw_name, str) else ""
            heard.append({
                "record_id": record.get("id"),
                "holder_id": holder,
                "holder_name": names.get(holder) or holder,
                "property_id": name,
                "property_label": property_labels.label_for(name),
                "value": record.get("value"),
                "scope": scope,
                "scope_label": "声称" if scope == "claim" else "认知",
                "event_id": record.get("event_id"),
                "event_name": names.get(record.get("event_id") or ""),
            })
        heard.sort(key=lambda item: (item["holder_name"], item["property_id"]))

        # 时间点可以往下钻：大事件下面是场景，场景下面是事件。都按阅读位置排，
        # 而且只列读者已经读到的那部分。
        children: list[dict] = []
        owner = nodes.get(node_id) or {}
        for child_id in owner.get("events") or []:
            child = nodes.get(child_id)
            if not child:
                continue
            if cut_position is not None and position(child) > cut_position:
                continue
            children.append({
                "id": child_id,
                "name": child.get("name", child_id),
                "label_id": child.get("label_id", child_id),
                "node_type": child.get("node_type"),
                "type_label": {"scene": "场景", "event": "事件", "action": "动作"}.get(child.get("node_type"), "节点"),
                "start": (child.get("disclosure") or {}).get("start"),
            })
        children.sort(key=lambda item: (item["start"] or 0, item["id"]))

        from .node_involvement import involvement

        return {
            "node_id": node_id,
            "node_type": node_type,
            "revision": snapshot["revision"],
            "vocabulary": property_categories.vocabulary_for(node_type, config),
            "categories": [
                {
                    "key": category,
                    "count": sum(1 for item in items if item["category"] == category),
                    "record_count": sum(
                        item["record_count"] for item in items if item["category"] == category
                    ),
                    "items": [item for item in items if item["category"] == category],
                }
                for category in ordered_categories
            ],
            "properties": items,
            "inbound": inbound,
            "claims": claims,
            "heard": heard,
            "children": children,
            "involvement": involvement(snapshot, records, node_id, cut),
            "cut": cut,
            "later_count": hidden_total,
        }

    @staticmethod
    def _pairs(records: list[dict]) -> set[tuple[str, str]]:
        return {(row["node_id"], row["property_id"]) for row in records
                if row.get("kind") == "property_value"
                and isinstance(row.get("node_id"), str) and isinstance(row.get("property_id"), str)}

    def world_state(self, at=None) -> dict:
        """Build a node-local world projection by calling the core for each pair."""
        snapshot = self.store.snapshot()
        revision = snapshot["revision"]
        records = self.accepted_records()
        world_records = [
            record for record in records
            if record.get("kind") in {"property_definition"}
            or (record.get("scope") or "world") == "world"
        ]
        nodes: dict[str, dict[str, dict]] = {}
        flat: list[dict] = []
        for node_id, property_id in sorted(self._pairs(world_records)):
            from .temporal_state import interval_state
            result = interval_state(self.store, node_id, property_id, at)
            nodes.setdefault(node_id, {})[property_id] = result
            flat.append(
                {"node_id": node_id, "property_id": property_id, **result}
            )
        return {
            "at": at,
            "revision": revision,
            "nodes": nodes,
            "properties": flat,
        }
