"""Append-only storage adapter for v5 node property records.

The pure projection/validation rules live in :mod:`narrative.property_core`.
This adapter owns SQLite transactions, global graph revisions, as-of filtering,
and the human review gate. Property records are never updated in place: a
correction is another accepted ``property_retraction`` record.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import property_categories, property_labels
from .graph_store import GraphStore
from .order_ids import order_key

PROPERTY_CONTRACT_VERSION = "property-state/6"
PROPERTY_RECORD_KINDS = (
    "property_definition",
    "property_value",
    "property_categorization",
    "property_retraction",
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
    """Records that no longer count at this reading position, and why.

    A retraction with no event was a misreading: it never counted, so it applies
    at every position. One with an event says "from here this is no longer the
    state", so before that position the old value is still what the reader knows.
    ``cut=None`` means "as of the last thing disclosed".

    The answer is a fold over the disclosure chain, not a vote: each retraction
    takes a record out from its own position on. Nothing reaches backwards — the
    stretch where a value was gone stays gone, because a later state is built on
    the earlier one.
    """
    # 按披露位置排，不按写入顺序排：状态是从前一段叠出来的，
    # 写在后头的记录不能改写前面那一段。
    statements = [
        record for record in records
        if record.get("kind") == "property_retraction" and _applies(record, cut)
    ]
    statements.sort(key=_statement_position)

    result: dict[str, dict] = {}
    for record in statements:
        target = record.get("target_id")
        if isinstance(target, str):
            result[target] = record
    return result


def supersede_plan(
    accepted: list[dict], batch: list[dict] | tuple = ()
) -> list[dict]:
    """Older values that a later disclosure already replaced.

    One line per aspect, because that is what the reader sees: `cultivation` and
    `cultivation_stage` describe one subject. A new value for the same aspect of
    the same node (same scope, same holder) means the older one is no longer the
    state — but only from that disclosure on, so the plan carries the replacing
    event instead of deleting anything.

    A restatement (same value) supersedes nothing: it confirms. Multi-valued
    properties are left alone, because their values coexist by definition.
    """
    from .property_categories import aspect_of

    everything = list(accepted) + list(batch)
    # A renamed or merged name is the same aspect: superseding has to see through
    # the alias, or the first value after a rename would never replace the last
    # value before it.
    aliases = _core().alias_map(everything)
    definitions = _core().canonical_definitions(everything)
    # A record already marked invalid keeps that mark; a second one would say
    # nothing new and would double the reason in the history.
    marked = {
        record.get("target_id") for record in list(accepted) + list(batch)
        if record.get("kind") == "property_retraction"
    }
    groups: dict[tuple, list[dict]] = {}
    for record in everything:
        if record.get("kind") != "property_value" or record.get("id") in marked:
            continue
        name = record.get("property_id") or ""
        canonical = aliases.get(name, name)
        definition = definitions.get(canonical)
        if definition is not None and definition.get("cardinality") == "many":
            continue
        key = (
            record.get("node_id"),
            aspect_of(canonical),
            (record.get("scope") or "world"),
            record.get("holder_id"),
        )
        groups.setdefault(key, []).append(record)

    plan: list[dict] = []
    for group in groups.values():
        name = str(group[0].get("property_id") or "")
        definition = definitions.get(aliases.get(name, name))
        node_valued = bool(definition) and definition.get("value_type") in {"node", "either"}
        ordered = sorted(group, key=record_position)
        still_valid: list[dict] = []
        for record in ordered:
            for older in still_valid:
                if str(older.get("value")) == str(record.get("value")):
                    continue
                if record_position(record) <= record_position(older):
                    continue
                # 同一个事件里说了两件事（值不同）不互相取代：那是并列或矛盾，不是推进。
                # 例外是**换手**：一件东西在同一场里先给出去再被接过，位置更后的那条接手。
                same_event = _statement_event(record) == _statement_event(older)
                if same_event and not node_valued:
                    continue
                item = {
                    "target_id": older.get("id"),
                    "event_id": record.get("event_id"),
                    "reason": "被后一条披露取代",
                    "node_id": older.get("node_id"),
                    "property_id": older.get("property_id"),
                }
                disclosure = record.get("disclosure")
                if isinstance(disclosure, dict) and isinstance(
                    disclosure.get("source_id"), str
                ):
                    item["disclosure"] = {
                        "source_id": disclosure["source_id"],
                        "start": record_position(record)[1],
                    }
                plan.append(item)
            still_valid = [record]
    unique: dict[str, dict] = {}
    for item in plan:
        unique.setdefault(str(item["target_id"]), item)
    return list(unique.values())


def supersede_records(accepted: list[dict], batch: list[dict]) -> list[dict]:
    """Retraction records for the older values this batch replaces.

    The plan says which records and from where; this turns each into the record
    that says so. Ids are derived from the pair, so re-running the same batch
    produces the same ids instead of piling up duplicates.
    """
    import hashlib

    out: list[dict] = []
    for item in supersede_plan(accepted, batch):
        digest = hashlib.sha1(
            f"{item['target_id']}:{item['event_id']}".encode()
        ).hexdigest()[:12]
        record = {
            "kind": "property_retraction",
            "id": f"retract_{digest}",
            "target_id": item["target_id"],
            "reason": item["reason"],
            "event_id": item["event_id"],
            "basis": "audit",
        }
        if isinstance(item.get("disclosure"), dict):
            record["disclosure"] = item["disclosure"]
        out.append(record)
    return out


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


def _alias_map(records: list[dict]) -> dict[str, str]:
    """Alias → canonical name, following chains (a merge and a rename alike)."""
    return _core().alias_map(records)


def _canonical(records: list[dict]) -> list[dict]:
    """The same records read under canonical names; definitions move with them."""
    return _core().canonical_records(records)


class PropertyStore:
    """Persist reviewed property records against the graph's global revision."""

    def __init__(self, store: GraphStore | str | Path, *, create: bool = False):
        self.store = store if isinstance(store, GraphStore) else GraphStore(store, create=create)

    # -- derived reads -------------------------------------------------
    @staticmethod
    def _records_from_db(db, as_of_revision: int | None = None) -> list[dict]:
        if as_of_revision is None:
            rows = db.execute(
                "SELECT data FROM property_records ORDER BY accepted_revision,rowid"
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT data FROM property_records WHERE accepted_revision<=? "
                "ORDER BY accepted_revision,rowid",
                (as_of_revision,),
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def accepted_records(
        self, as_of_revision: int | None = None, *, include_retracted: bool = False
    ) -> list[dict]:
        """Return immutable authoritative records visible at one review revision.

        Only a retraction *without* an event removes a record here: that one says
        the record was misread, so it never counted. A retraction with an event
        says the record stops being the state from that point on — before it the
        record is still what the reader knows, so removing it here would rewrite
        the reading order. Those are applied where a reading position exists
        (``node_properties``, ``_value_state``).
        """
        with self.store.connect() as db:
            records = self._records_from_db(db, as_of_revision)
        if include_retracted:
            return records
        retracted = {
            item.get("target_id")
            for item in records
            if item.get("kind") == "property_retraction"
            and not isinstance(item.get("event_id"), str)
        }
        return [item for item in records if item.get("id") not in retracted]

    # -- validation ----------------------------------------------------
    def _validation_snapshot(self, snapshot: dict) -> dict:
        """Attach exact source text for pure validation only.

        ``GraphStore.snapshot()`` deliberately omits source bodies so callers can
        never read or persist them.  The pure core still needs the exact text to
        prove that a property ``evidence.quote`` is a unique substring, so this
        adapter injects the text into a private copy that is passed only to
        ``property_core.validate_records``.  It is never stored in a candidate and
        never returned from a snapshot-facing read.
        """
        sources = snapshot.get("sources")
        if not isinstance(sources, list) or not sources:
            return snapshot
        enriched: list[dict] = []
        changed = False
        for source in sources:
            if not isinstance(source, dict) or isinstance(source.get("text"), str):
                enriched.append(source)
                continue
            source_id = source.get("id")
            if not isinstance(source_id, str):
                enriched.append(source)
                continue
            try:
                body = self.store.source(source_id)["text"]
            except ValueError:
                enriched.append(source)
                continue
            enriched.append(source | {"text": body})
            changed = True
        return snapshot | {"sources": enriched} if changed else snapshot

    @staticmethod
    def _format_records(records: object) -> tuple[list[dict], list[dict]]:
        if not isinstance(records, list):
            return [], [_issue("FORMAT", "属性记录须为 JSON 数组")]
        normalized = [dict(record) for record in records if isinstance(record, dict)]
        if len(normalized) != len(records):
            return normalized, [_issue("FORMAT", "每条属性记录须为 JSON 对象")]
        try:
            json.dumps(normalized, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            return normalized, [_issue("FORMAT", "属性记录须可序列化为 JSON")]
        return normalized, []

    def normalize_records(self, records: object) -> tuple[list[dict], list[dict]]:
        """Normalize property records without writing anything."""
        return self._format_records(records)

    def _diagnostics(
        self,
        records: object,
        expected_revision: int,
        snapshot: dict,
        accepted_records: list[dict],
    ) -> list[dict]:
        normalized, diagnostics = self._format_records(records)
        if not normalized and not diagnostics:
            diagnostics.append(_issue("EMPTY", "候选至少须包含一条属性记录"))
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision != snapshot["revision"]
        ):
            diagnostics.append(_issue("REVISION", "正式资料版本已变化，请刷新后重新预检"))
        try:
            core_diagnostics = _core().validate_records(
                normalized, self._validation_snapshot(snapshot), accepted_records
            )
        except Exception as exc:
            diagnostics.append(_issue("RECORD", str(exc)))
        else:
            diagnostics.extend(core_diagnostics or [])
        ids = [record.get("id") for record in normalized]
        valid_ids = [record_id for record_id in ids if isinstance(record_id, str) and record_id]
        if len(valid_ids) != len(ids):
            diagnostics.append(_issue("ID", "每条属性记录须有稳定 id"))
        if len(valid_ids) != len(set(valid_ids)):
            diagnostics.append(_issue("DUPLICATE_ID", "同一候选内属性记录 ID 不得重复"))
        existing = {
            item["id"]
            for item in accepted_records
            if isinstance(item.get("id"), str)
        }
        if existing.intersection(valid_ids):
            diagnostics.append(
                _issue("APPEND_ONLY", "属性记录只可追加一次；已有 ID 不可覆盖或重放")
            )
        return diagnostics

    def diagnostics(
        self,
        records: list[dict],
        expected_revision: int,
        snapshot: dict | None = None,
    ) -> list[dict]:
        """Validate records against a supplied snapshot (used by unified changes)."""
        snapshot = snapshot or self.store.snapshot()
        # 撤回过的记录也要在场，否则指向它们的失效/撤销/更正会报「目标不是属性记录」。
        return self._diagnostics(
            records,
            expected_revision,
            snapshot,
            self.accepted_records(snapshot["revision"], include_retracted=True),
        )

    def precheck(self, records: list[dict], expected_revision: int) -> list[dict]:
        snapshot = self.store.snapshot()
        # 撤回过的记录也要在场：指向它们的失效、撤销失效、更正都得能验通过。
        # 只看「还站得住的」会让第二次撤回报「目标不是属性记录」。
        return self._diagnostics(
            records,
            expected_revision,
            snapshot,
            self.accepted_records(snapshot["revision"], include_retracted=True),
        )

    # -- pending candidates -------------------------------------------
    def propose(
        self,
        records: list[dict],
        expected_revision: int,
        *,
        source: str = "manual",
        note: str = "",
        run_id: str | None = None,
    ) -> str:
        if source not in {"manual", "model", "correction", "audit"}:
            raise ValueError("属性候选来源无效")
        snapshot = self.store.snapshot()
        diagnostics = self._diagnostics(
            records,
            expected_revision,
            snapshot,
            self.accepted_records(snapshot["revision"], include_retracted=True),
        )
        errors = [item["message"] for item in diagnostics if item["severity"] == "error"]
        if errors:
            raise ValueError("；".join(errors))
        from uuid import uuid4

        candidate_id = uuid4().hex
        normalized, _ = self._format_records(records)
        change = {
            "id": candidate_id,
            "records": normalized,
            "source": source,
            "note": note,
            "run_id": run_id,
            "expected_revision": expected_revision,
            "diagnostics": diagnostics,
        }
        with self.store.connect(write=True) as db:
            db.execute(
                "INSERT INTO property_candidates"
                "(id,data,status,version,expected_revision) VALUES (?,?,?,?,?)",
                (
                    candidate_id,
                    json.dumps(change, ensure_ascii=False),
                    "pending",
                    1,
                    expected_revision,
                ),
            )
        return candidate_id

    @staticmethod
    def _candidate_row(row) -> dict:
        return json.loads(row["data"]) | {
            "status": row["status"],
            "version": row["version"],
            "expected_revision": row["expected_revision"],
            "result_revision": row["result_revision"],
            "created_at": row["created_at"],
        }

    def candidates(self, status: str | None = None) -> list[dict]:
        if status is not None and status not in {"pending", "accepted", "rejected"}:
            raise ValueError("候选状态无效")
        sql = "SELECT * FROM property_candidates"
        if status is not None:
            sql += " WHERE status=?"
        sql += " ORDER BY created_at DESC,rowid DESC"
        with self.store.connect() as db:
            rows = db.execute(sql, (status,) if status else ()).fetchall()
        return [self._candidate_row(row) for row in rows]

    def candidate(self, candidate_id: str) -> dict:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM property_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
        if row is None:
            raise ValueError("属性候选不存在")
        return self._candidate_row(row)

    def accept(self, candidate_id: str, version: int) -> int:
        """Validate and append every record in one transaction, or append none."""
        with self.store.connect(write=True) as db:
            row = db.execute(
                "SELECT * FROM property_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise ValueError("属性候选不存在")
            if row["status"] != "pending" or row["version"] != version:
                raise ValueError("候选状态或版本已变化")
            change = json.loads(row["data"])
            revision = int(
                db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
            )
            if change["expected_revision"] != revision:
                raise ValueError("正式资料版本已变化，请重新建立属性候选")
            snapshot = self.store.snapshot()
            accepted_records = self._records_from_db(db, revision)
            diagnostics = self._diagnostics(
                change["records"], revision, snapshot, accepted_records
            )
            records, _ = self._format_records(change["records"])
            errors = [item["message"] for item in diagnostics if item["severity"] == "error"]
            if errors:
                raise ValueError("；".join(errors))
            next_revision = revision + 1
            # 新披露覆盖旧值：同节点、同方面、同作用域、同持有人的单值属性，
            # 更早的那条从这次披露起不再算数。它和这次披露同属一个版本，
            # 不需要人再去审核页点一次「应用建议」。
            records = list(records) + supersede_records(accepted_records, records)
            for record in records:
                db.execute(
                    "INSERT INTO property_records(id,kind,data,accepted_revision,candidate_id) "
                    "VALUES (?,?,?,?,?)",
                    (
                        record["id"],
                        record.get("kind", ""),
                        json.dumps(record, ensure_ascii=False),
                        next_revision,
                        candidate_id,
                    ),
                )
            db.execute("UPDATE meta SET value=? WHERE key='revision'", (str(next_revision),))
            db.execute(
                "UPDATE property_candidates SET status='accepted',result_revision=? WHERE id=?",
                (next_revision, candidate_id),
            )
            db.execute(
                "INSERT INTO revisions(revision,candidate_id,data) VALUES (?,?,?)",
                (
                    next_revision,
                    candidate_id,
                    json.dumps(
                        {
                            "operations": [],
                            "candidate_id": candidate_id,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        return next_revision

    def reject(self, candidate_id: str, version: int) -> None:
        with self.store.connect(write=True) as db:
            row = db.execute(
                "SELECT status,version FROM property_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise ValueError("属性候选不存在")
            if row["status"] != "pending" or row["version"] != version:
                raise ValueError("候选状态或版本已变化")
            db.execute(
                "UPDATE property_candidates SET status='rejected' WHERE id=?", (candidate_id,)
            )

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
            if record.get("kind") in {"property_definition", "property_retraction"}:
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
        as_of_revision: int | None = None,
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
        snapshot = self.store.snapshot(as_of_revision)
        records = self.accepted_records(snapshot["revision"])
        visible, disclosure = self._visible_records(records, snapshot, disclosure_cut)
        # `at` is a disclosure checkpoint (an event id) or nothing at all: the
        # property has no value outside a reading position.
        result = self._value_state(
            node_id, property_id, self._cut_position(at),
            scope=scope, holder_id=holder_id,
        )
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
        return self._value_state(
            node_id, property_id, self._cut_position(disclosure_cut),
            scope=scope, holder_id=holder_id,
        )

    def _cut_position(self, cut: object) -> tuple | None:
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

    def _value_state(
        self,
        node_id: str,
        property_id: str,
        cut: tuple[int, ...] | None,
        *,
        scope: str = "world",
        holder_id: str | None = None,
    ) -> dict:
        """The value visible at a reading cut, read from the value sequence.

        The cut is an event id, and an id's number *is* its reading position, so
        no separate ordering table is involved.
        """
        cut_position = cut

        # A value stops counting when a retraction says so, and a retraction
        # anchored at a later disclosure does not apply yet at this position.
        all_records = self.accepted_records(include_retracted=True)
        invalid = invalid_at(all_records, cut_position)
        records = [item for item in all_records if item.get("id") not in invalid]
        # A rename that was taken back stops applying here too, so the alias map
        # is built from what still stands rather than from every record ever.
        aliases = _alias_map(records)
        wanted = aliases.get(property_id, property_id)

        def name_of(record: dict) -> str:
            name = record.get("property_id")
            return aliases.get(name, name) if isinstance(name, str) else ""

        values = [
            record for record in records
            if record.get("kind") == "property_value"
            and record.get("node_id") == node_id
            and name_of(record) == wanted
            and (record.get("scope") or "world") == scope
            and (scope == "world" or record.get("holder_id") == holder_id)
        ]

        applicable = [
            record for record in values
            if cut_position is None or record_position(record) <= cut_position
        ]
        if not applicable:
            return {"state": {"kind": "unrecorded"}, "record_ids": []}
        # Later disclosure wins; when positions tie, the later record wins.
        best = max(
            enumerate(applicable), key=lambda pair: (record_position(pair[1]), pair[0])
        )[1]
        return {
            "state": {"kind": "known", "value": best.get("value")},
            "record_ids": [best.get("id")],
        }

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
        kwargs = (
            {"scope": scope, "holder_id": holder_id}
            if scope != "world" or holder_id is not None
            else {}
        )
        # History is the audit trail: retracted entries belong in it.
        records = self.accepted_records(include_retracted=True)
        records = _canonical(records)
        return _core().history(records, node_id, property_id, **kwargs)

    def audit(
        self,
        node_ids: set[str] | None = None,
        as_of_revision: int | None = None,
    ) -> dict:
        """Deterministic audit of accepted records; nothing is modified."""
        from .property_audit import audit_properties

        snapshot = self.store.snapshot(as_of_revision)
        # Retracted rows are read too: judging a retraction needs the record it
        # points at, and a target the audit never saw looks like a broken pointer.
        records = self.accepted_records(
            snapshot["revision"], include_retracted=True
        )
        # An alias that was merged is no longer a separate name: auditing it as
        # one would keep reporting the split the human already resolved.
        records = _canonical(records)
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
        rename lost data. Records are moved by appending categorization records,
        exactly as a manual move does.
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
        """Append one categorization record per pair, in a single revision."""
        from uuid import uuid4

        current = _categorization_overrides(self.accepted_records())
        records = [
            {
                "kind": "property_categorization",
                "id": f"cat_{uuid4().hex[:12]}",
                "target_id": target,
                "category": category,
                **({"note": note} if note else {}),
            }
            for target, category in pairs
            if current.get(target) != category
        ]
        if not records:
            return {"revision": self.store.revision, "moved": [], "category": ""}
        revision = self.store.revision
        candidate = self.propose(
            records, revision, source="correction", note=note or "移动分类"
        )
        return {
            "revision": self.accept(candidate, 1),
            "moved": [record["target_id"] for record in records],
            "category": records[0]["category"],
        }

    def categorize(
        self, targets: list[str], category: str, note: str = ""
    ) -> dict:
        """Move disclosures to a category.

        Moving is a human editorial act, so it is applied immediately — but it is
        still an append-only record, not a rewrite: the earlier grouping stays in
        the history and the value itself is untouched.
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

    def merge(self, alias_id: str, target_id: str, note: str = "") -> dict:
        """Read `alias_id` as `target_id` from now on.

        Nothing is rewritten: records keep the name they were captured under and
        the alias stays a real definition, so its records still validate.
        Retracting this record splits the names again.
        """
        from uuid import uuid4

        alias_id = (alias_id or "").strip()
        target_id = (target_id or "").strip()
        if not alias_id or not target_id:
            raise ValueError("请提供两个属性名")
        if alias_id == target_id:
            raise ValueError("合并的两个名字不能相同")
        known = {
            record["id"] for record in self.accepted_records()
            if record.get("kind") == "property_definition"
        }
        for name in (alias_id, target_id):
            if name not in known:
                raise ValueError(f"属性定义不存在：{name}")
        record = {
            "kind": "property_merge",
            "id": f"merge_{uuid4().hex[:12]}",
            "alias_id": alias_id,
            "target_id": target_id,
            **({"note": note} if note else {}),
        }
        revision = self.store.revision
        candidate = self.propose(
            [record], revision, source="correction",
            note=note or f"合并属性：{alias_id} → {target_id}",
        )
        return {
            "revision": self.accept(candidate, 1),
            "alias_id": alias_id,
            "target_id": target_id,
        }

    def rename(self, names: object, to_id: str, note: str = "") -> dict:
        """Give one aspect a new name.

        A rename is a read-time alias like a merge, but it says something else:
        not "these two names are one aspect" but "this aspect is called that
        now". So the new name does not have to exist yet — creating it is the
        point — and the definition moves with it, keeping the shape the values
        were validated against. Nothing is rewritten: records keep the name they
        were captured under, and retracting this record restores the old name.

        ``names`` is the whole row: an aspect may be spread over two names
        (`cultivation`, `cultivation_stage`), and renaming the line has to move
        all of them, otherwise the next disclosure reintroduces the old spelling.
        Renaming onto another aspect's name is refused — that is a merge, and a
        silent one would hide which aspect just absorbed which.
        """
        from uuid import uuid4

        if isinstance(names, str):
            names = [names]
        if not isinstance(names, (list, tuple)):
            raise ValueError("请提供要改的属性名")
        sources = [item.strip() for item in names if isinstance(item, str) and item.strip()]
        to_id = (to_id or "").strip()
        if not sources:
            raise ValueError("请提供要改的属性名")
        if not to_id:
            raise ValueError("请填写新的属性名")
        if len(to_id) > 40:
            raise ValueError("属性名最长 40 个字符")

        records = self.accepted_records(include_retracted=True)
        invalid = invalid_at(records, None)
        live = [record for record in records if record.get("id") not in invalid]
        aliases = _alias_map(live)
        definitions = {
            record["id"] for record in live
            if record.get("kind") == "property_definition"
        }
        known = definitions | set(aliases) | set(aliases.values())
        unknown = [name for name in sources if name not in known]
        if unknown:
            raise ValueError(f"属性名不存在：{'、'.join(unknown[:3])}")

        canonical = {aliases.get(name, name) for name in sources}
        target = aliases.get(to_id, to_id)
        if target in canonical:
            # The new name is one this aspect already answers to: point the rest
            # at it instead of inventing a third spelling.
            to_id = target
        else:
            taken = (definitions | set(aliases.values())) - canonical
            if to_id in taken:
                raise ValueError(
                    f"「{to_id}」已经是另一个属性的名字；要合成一个方面请用「合并」"
                )
        pending = [
            {
                "kind": "property_rename",
                "id": f"rename_{uuid4().hex[:12]}",
                "from_id": name,
                "to_id": to_id,
                **({"note": note} if note else {}),
            }
            for name in dict.fromkeys(sources)
            if name != to_id
        ]
        if not pending:
            raise ValueError("新旧名字相同")
        revision = self.store.revision
        candidate = self.propose(
            pending, revision, source="correction",
            note=note or f"改名：{'、'.join(sources)} → {to_id}",
        )
        return {
            "revision": self.accept(candidate, 1),
            "renamed": [record["from_id"] for record in pending],
            "to_id": to_id,
        }

    def aliases(self) -> dict[str, str]:
        """Alias → canonical name for the whole project (merges and renames).

        Read once by anything that has to speak the current names: the extraction
        prompt lists them so the next chapter does not reinvent a name that was
        just renamed away.
        """
        records = self.accepted_records(include_retracted=True)
        invalid = invalid_at(records, None)
        return _alias_map([item for item in records if item.get("id") not in invalid])

    def retract(
        self,
        targets: list[str],
        reason: str,
        *,
        event_id: str | None = None,
        basis: str = "human",
    ) -> dict:
        """Invalidate records. The row stays for audit; readers stop seeing it.

        Retraction is the only way to take something back, because a value is
        never edited in place: a wrong record is retracted, and a corrected one
        is appended as a new disclosure.

        ``event_id`` separates the two reasons it can happen:

        - absent — the record was misread. It never counted, at any position.
        - present — the record was true, but from this reading position it is no
          longer part of the state. Before that position it still counts.
        """
        from uuid import uuid4

        reason = (reason or "").strip()
        if not reason:
            raise ValueError("撤回需要写清原因")
        if not isinstance(targets, list) or not targets:
            raise ValueError("请提供要撤回的记录")
        records = self.accepted_records(include_retracted=True)
        by_id = {
            record["id"]: record for record in records if isinstance(record.get("id"), str)
        }
        # 已经有过的撤回。同一个说法不用说第二遍，但「从某处起不算」和「从来不算」
        # 是两句话——站在更早的阅读位置上撤一条已经被取代的记录，说的是后者；
        # 不写进去，读者在那个位置怎么点都撤不动。
        live = [
            record for record in records
            if record.get("kind") == "property_retraction"
        ]
        pending: list[dict] = []
        for target in dict.fromkeys(targets):
            record = by_id.get(target)
            if record is None:
                raise ValueError(f"记录不存在：{target}")
            if record.get("kind") not in {
                "property_value", "property_categorization", "property_merge",
                "property_rename",
            }:
                raise ValueError("只有取值、分类、归并或改名记录可以撤回")
            existing = [
                record for record in live if record.get("target_id") == target
            ]
            # 只在「比现有的更早」时才写：已经有一条落在更早位置的失效（含「从来不算」，
            # 它的位置排在最前），这次就没有新信息。反过来，站在更早的阅读位置上撤
            # 一条后来才被取代的记录，是在说「从这里起也不算」——那是新说法，要写进去，
            # 否则读者在那个位置上怎么点都撤不动。
            new_event = _statement_event({"event_id": event_id})
            if any(_statement_event(record) <= new_event for record in existing):
                continue
            item: dict = {
                "kind": "property_retraction",
                "id": f"retract_{uuid4().hex[:12]}",
                "target_id": target,
                "reason": reason,
                "basis": basis,
            }
            if event_id:
                item["event_id"] = event_id
                # The position inside that event is the target's own disclosure:
                # the old value stops counting exactly where the new one appears.
                disclosure = (record.get("disclosure") or {})
                if isinstance(disclosure.get("source_id"), str):
                    item["disclosure"] = {
                        "source_id": disclosure["source_id"],
                        "start": record_position(record)[1],
                    }
            pending.append(item)
        if not pending:
            return {"revision": self.store.revision, "retracted": [], "reason": reason}
        revision = self.store.revision
        candidate = self.propose(
            pending, revision, source="correction", note=f"撤回：{reason}"
        )
        return {
            "revision": self.accept(candidate, 1),
            "retracted": [record["target_id"] for record in pending],
            "reason": reason,
        }

    def supersede(self, pairs: list[dict], note: str = "") -> dict:
        """Mark older values as no longer the state, from a later disclosure on.

        Each pair is ``{target_id, event_id, reason}``: the target is invalid from
        ``event_id`` on. This is what the audit round proposes when a new value
        replaces an older one — the newer disclosure says the older one is no
        longer what holds, and nothing is rewritten or deleted.
        """
        from uuid import uuid4

        if not isinstance(pairs, list) or not pairs:
            raise ValueError("请提供要失效的记录")
        records = self.accepted_records(include_retracted=True)
        by_id = {
            record["id"]: record for record in records if isinstance(record.get("id"), str)
        }
        already = {
            record.get("target_id") for record in records
            if record.get("kind") == "property_retraction"
        }
        pending: list[dict] = []
        for pair in pairs:
            target = str(pair.get("target_id") or "").strip()
            event_id = str(pair.get("event_id") or "").strip()
            reason = str(pair.get("reason") or "").strip() or "被后一条披露取代"
            record = by_id.get(target)
            if record is None or record.get("kind") != "property_value":
                raise ValueError(f"记录不存在或不是取值记录：{target}")
            if not event_id:
                raise ValueError("失效需要一个时间点（事件）")
            if target in already:
                continue
            item: dict = {
                "kind": "property_retraction",
                "id": f"retract_{uuid4().hex[:12]}",
                "target_id": target,
                "reason": reason,
                "event_id": event_id,
                "basis": "audit",
            }
            disclosure = record.get("disclosure") or {}
            if isinstance(disclosure.get("source_id"), str):
                item["disclosure"] = {
                    "source_id": disclosure["source_id"],
                    "start": record_position(record)[1],
                }
            pending.append(item)
        if not pending:
            return {"revision": self.store.revision, "superseded": []}
        revision = self.store.revision
        candidate = self.propose(
            pending, revision, source="audit", note=note or "审核轮：旧值失效"
        )
        return {
            "revision": self.accept(candidate, 1),
            "superseded": [record["target_id"] for record in pending],
        }

    def node_properties(
        self,
        node_id: str,
        as_of_revision: int | None = None,
        cut: object = None,
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
        snapshot = self.store.snapshot(as_of_revision)
        # Read the whole history even when the caller only wants what stands: a
        # record that stopped counting is still needed to know that the aspect
        # lost a value, and a retraction has to be resolvable to its target.
        all_records = self.accepted_records(snapshot["revision"], include_retracted=True)
        # A reading cut turns the pane into "what was known at this point". It is
        # also what decides whether a retraction applies: one anchored at a later
        # disclosure has not happened for the reader yet.
        cut_position = self._cut_position(cut) if cut else None
        # Validity is explicit, not computed: a record counts until a retraction
        # says otherwise. Nothing here derives a window from the next disclosure.
        invalid = invalid_at(all_records, cut_position)
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
        aliases = _alias_map(editorial)

        def node_ref(value: object) -> dict | None:
            """A node-valued property stores an id; the reader wants the name."""
            if not isinstance(value, str):
                return None
            name = names.get(value)
            return {"id": value, "name": name} if name else None

        def position(record: dict) -> tuple:
            return record_position(record)

        # One line per aspect and category. The aspect is the first token of the
        # property name, so `cultivation` and `cultivation_stage` share a line:
        # they describe the same subject, and splitting them is what made three
        # realms look like three simultaneous truths. Scope is a label on each
        # record rather than a reason to split the line — a claim about the realm
        # and the realm itself are the same subject, told differently.
        lines: dict[tuple[str, str], list[dict]] = {}
        for record in all_records:
            # Only values sit on a line. A retraction has no node or aspect of its
            # own — it points at a value and is applied through `invalid_at`.
            if record.get("kind") != "property_value":
                continue
            if record.get("node_id") != node_id:
                continue
            category, category_source = property_categories.resolve(
                record, node_type, config, overrides
            )
            raw_name = record.get("property_id")
            name = aliases.get(raw_name, raw_name) if isinstance(raw_name, str) else ""
            aspect = property_categories.aspect_of(name)
            lines.setdefault((aspect, category), []).append({
                "record": record,
                "property_id": name,
                "category_source": category_source,
            })
        # A slice with nothing left is not shown at all, so it must not be
        # advertised either: "也在「其他」中" has to point at a row that exists.
        shown_categories: dict[str, set[str]] = {}

        items: list[dict] = []
        hidden_total = 0
        # One aspect has one state, even when its records were classified into two
        # categories. Which record holds it is decided here, across slices, so the
        # other slice cannot claim a current value of its own.
        holding: dict[str, tuple[tuple, str]] = {}
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

        for (aspect, category), line in lines.items():
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
                current and current_at == holding.get(aspect, (None, ""))[0]
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
                    shown_categories.get(aspect, set()) - {category}
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

        # 物品必须回答「在谁手里」。原文没写就补一行「未知」——**不造假记录**，
        # 这一行是算出来的（derived），展开没有披露可看。
        if node_type == "item" and not any(
            "持有" in item["aspect"] or "归属" in item["aspect"] for item in items
        ):
            items.append({
                "property_id": "持有者", "property_ids": ["持有者"],
                "aspect": "持有者", "label": "持有者",
                "category": "归属与持有", "category_source": "inferred",
                "other_categories": [],
                "value_node": None, "scope": "world", "scopes": ["world"],
                "holder_id": None, "holder_name": "",
                "value_type": "either", "value": "未知",
                "event_id": None, "start": None, "disclosed_at": "",
                "is_current": True, "record_count": 0, "later_count": 0,
                "records": [], "derived": True,
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
            name = aliases.get(raw_name, raw_name) if isinstance(raw_name, str) else ""
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
        inbound.sort(key=lambda item: (item["category"], item["node_name"], item["property_id"]))

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
            "cut": cut,
            "later_count": hidden_total,
        }

    @staticmethod
    def _pairs(records: list[dict]) -> set[tuple[str, str]]:
        by_id = {
            record["id"]: record
            for record in records
            if isinstance(record.get("id"), str)
        }
        pairs: set[tuple[str, str]] = set()

        def collect(record: dict, seen: set[str]) -> None:
            kind = record.get("kind")
            if kind == "property_value":
                node_id, property_id = record.get("node_id"), record.get("property_id")
                if isinstance(node_id, str) and isinstance(property_id, str):
                    pairs.add((node_id, property_id))
            elif kind == "property_retraction":
                target_id = record.get("target_id")
                if isinstance(target_id, str) and target_id not in seen:
                    target = by_id.get(target_id)
                    if target is not None:
                        seen.add(target_id)
                        collect(target, seen)

        for record in records:
            collect(record, set())
        return pairs

    def world_state(self, at=None, as_of_revision: int | None = None) -> dict:
        """Build a node-local world projection by calling the core for each pair."""
        snapshot = self.store.snapshot(as_of_revision)
        revision = snapshot["revision"]
        records = self.accepted_records(revision)
        world_records = [
            record for record in records
            if record.get("kind") in {"property_definition", "property_retraction"}
            or (record.get("scope") or "world") == "world"
        ]
        cut = self._cut_position(at)
        nodes: dict[str, dict[str, dict]] = {}
        flat: list[dict] = []
        for node_id, property_id in sorted(self._pairs(world_records)):
            result = self._value_state(node_id, property_id, cut)
            nodes.setdefault(node_id, {})[property_id] = result
            flat.append(
                {"node_id": node_id, "property_id": property_id, **result}
            )
        return {
            "at": at,
            "as_of_revision": revision,
            "nodes": nodes,
            "properties": flat,
        }

    # Explicit property_* aliases keep the adapter discoverable beside Operations.
    property_precheck = precheck
    property_propose = propose
    property_accept = accept
    property_reject = reject
    property_state = state
    property_disclosure_state = disclosure_state
    property_history = history
    property_world_state = world_state
    property_categorize = categorize
