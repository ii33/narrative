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


def _merge_map(records: list[dict]) -> dict[str, str]:
    """Alias → canonical property name, following chains.

    A merge is a read-time alias: the records keep the name they were captured
    under, so nothing has to be rewritten and a wrong merge is undone by
    retracting one record.
    """
    direct = {
        record["alias_id"]: record["target_id"]
        for record in records
        if record.get("kind") == "property_merge"
        and isinstance(record.get("alias_id"), str)
        and isinstance(record.get("target_id"), str)
    }
    resolved: dict[str, str] = {}
    for alias in direct:
        seen = {alias}
        cursor = direct[alias]
        while cursor in direct and cursor not in seen:
            seen.add(cursor)
            cursor = direct[cursor]
        resolved[alias] = cursor
    return resolved


def _with_canonical_names(records: list[dict], merges: dict[str, str]) -> list[dict]:
    """Read merged value records under their canonical name.

    Copies only the records whose name was merged; everything else is passed
    through, so callers keep working with the same objects they were given.
    """
    if not merges:
        return records
    result: list[dict] = []
    for record in records:
        if record.get("kind") == "property_value":
            name = record.get("property_id")
            if isinstance(name, str) and name in merges:
                record = record | {"property_id": merges[name]}
        result.append(record)
    return result


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

        A retraction keeps the original row for audit but removes it from every
        projection, so the default read drops retracted records. Pass
        ``include_retracted=True`` only when auditing the history.
        """
        with self.store.connect() as db:
            records = self._records_from_db(db, as_of_revision)
        if include_retracted:
            return records
        retracted = {
            item.get("target_id")
            for item in records
            if item.get("kind") == "property_retraction"
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
        return self._diagnostics(
            records,
            expected_revision,
            snapshot,
            self.accepted_records(snapshot["revision"]),
        )

    def precheck(self, records: list[dict], expected_revision: int) -> list[dict]:
        snapshot = self.store.snapshot()
        return self._diagnostics(
            records, expected_revision, snapshot, self.accepted_records(snapshot["revision"])
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
        if source not in {"manual", "model", "correction"}:
            raise ValueError("属性候选来源无效")
        snapshot = self.store.snapshot()
        diagnostics = self._diagnostics(
            records, expected_revision, snapshot, self.accepted_records(snapshot["revision"])
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

        records = self.accepted_records()
        merges = _merge_map(records)
        wanted = merges.get(property_id, property_id)

        def name_of(record: dict) -> str:
            name = record.get("property_id")
            return merges.get(name, name) if isinstance(name, str) else ""

        values = [
            record for record in records
            if record.get("kind") == "property_value"
            and record.get("node_id") == node_id
            and name_of(record) == wanted
            and record.get("scope", "world") == scope
            and (scope == "world" or record.get("holder_id") == holder_id)
        ]

        def position(record: dict) -> tuple:
            """Event number first, then the record's own offset inside that event.

            One anchor can hold several disclosures (a retelling, a correction),
            and their order is the order of the source text, not the write order.
            """
            anchor = record.get("event_id")
            key = order_key(anchor) if isinstance(anchor, str) else None
            disclosure = record.get("disclosure")
            offset = disclosure.get("start") if isinstance(disclosure, dict) else None
            return (key or (999,), offset if isinstance(offset, int) else 0)

        applicable = [
            record for record in values
            if cut_position is None or position(record) <= cut_position
        ]
        if not applicable:
            return {"state": {"kind": "unrecorded"}, "record_ids": []}
        # Later disclosure wins; when positions tie, the later record wins.
        best = max(
            enumerate(applicable), key=lambda pair: (position(pair[1]), pair[0])
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
        records = _with_canonical_names(records, _merge_map(records))
        return _core().history(records, node_id, property_id, **kwargs)

    def audit(
        self,
        node_ids: set[str] | None = None,
        as_of_revision: int | None = None,
    ) -> dict:
        """Deterministic audit of accepted records; nothing is modified."""
        from .property_audit import audit_properties

        snapshot = self.store.snapshot(as_of_revision)
        records = self.accepted_records(snapshot["revision"])
        # An alias that was merged is no longer a separate name: auditing it as
        # one would keep reporting the split the human already resolved.
        records = _with_canonical_names(records, _merge_map(records))
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
        """
        snapshot = self.store.snapshot(as_of_revision)
        records = self.accepted_records(snapshot["revision"])
        config = property_categories.load(self.store)
        definitions = {
            record["id"]: record for record in records
            if record.get("kind") == "property_definition"
        }
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
        overrides = _categorization_overrides(records)
        # A merged alias reads as its canonical name everywhere: one aspect, one
        # line, one label, without rewriting the records that used the old name.
        merges = _merge_map(records)

        def node_ref(value: object) -> dict | None:
            """A node-valued property stores an id; the reader wants the name."""
            if not isinstance(value, str):
                return None
            name = names.get(value)
            return {"id": value, "name": name} if name else None

        def position(record: dict) -> tuple:
            anchor = record.get("event_id")
            key = order_key(anchor) if isinstance(anchor, str) else None
            disclosure = record.get("disclosure")
            offset = disclosure.get("start") if isinstance(disclosure, dict) else None
            return (key or (999,), offset if isinstance(offset, int) else 0)

        # One line per aspect and category. The aspect is the first token of the
        # property name, so `cultivation` and `cultivation_stage` share a line:
        # they describe the same subject, and splitting them is what made three
        # realms look like three simultaneous truths. Scope is a label on each
        # record rather than a reason to split the line — a claim about the realm
        # and the realm itself are the same subject, told differently.
        lines: dict[tuple[str, str], list[dict]] = {}
        for record in records:
            if record.get("kind") not in {"property_value", "property_retraction"}:
                continue
            if record.get("node_id") != node_id:
                continue
            category, category_source = property_categories.resolve(
                record, node_type, config, overrides
            )
            raw_name = record.get("property_id")
            name = merges.get(raw_name, raw_name) if isinstance(raw_name, str) else ""
            aspect = property_categories.aspect_of(name)
            lines.setdefault((aspect, category), []).append({
                "record": record,
                "property_id": name,
                "category_source": category_source,
            })
        aspect_categories: dict[str, set[str]] = {}
        for aspect, category in lines:
            aspect_categories.setdefault(aspect, set()).add(category)

        # A reading cut turns the pane into "what was known at this point": the
        # later disclosures are not wrong, they simply have not happened for the
        # reader yet, so they are counted rather than shown.
        cut_position = self._cut_position(cut) if cut else None

        items: list[dict] = []
        hidden_total = 0
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
                visible_ids = {entry["record"].get("id") for entry in visible}
                # A retraction whose target is not known yet says nothing.
                ordered = [
                    entry for entry in visible
                    if entry["record"].get("kind") != "property_retraction"
                    or entry["record"].get("target_id") in visible_ids
                ]
                if not ordered:
                    continue
            property_ids = sorted({entry["property_id"] for entry in ordered})
            definition = definitions.get(ordered[-1]["property_id"]) or {}
            node_valued = definition.get("value_type") in {"node", "either"}
            decorated: list[dict] = []
            for index, entry in enumerate(ordered):
                row = entry["record"]
                decorated.append({
                    "id": row.get("id"),
                    "kind": row.get("kind"),
                    "value": row.get("value"),
                    "value_node": node_ref(row.get("value")) if node_valued else None,
                    "event_id": row.get("event_id"),
                    "event_name": names.get(row.get("event_id") or ""),
                    # From this disclosure up to the next one in the same line;
                    # the last entry is the current value and stays open.
                    "is_current": index == len(ordered) - 1
                    and row.get("kind") != "property_retraction",
                    "valid_to_event": names.get(
                        ordered[index + 1]["record"].get("event_id") or ""
                    ) if index + 1 < len(ordered) else None,
                    "disclosure": row.get("disclosure"),
                    "source_title": source_titles.get(
                        (row.get("disclosure") or {}).get("source_id") or ""
                    ),
                    "source": row.get("source"),
                    "anchor_basis": row.get("anchor_basis"),
                    "scope": row.get("scope", "world"),
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
                })
            latest = decorated[-1]
            values_only = [entry for entry in decorated if entry.get("kind") == "property_value"]
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
                    name for name in aspect_categories[aspect] if name != category
                ),
                "current_from_event": names.get(latest.get("event_id") or ""),
                "current_from_start": (latest.get("disclosure") or {}).get("start"),
                "current_retracted": latest.get("kind") == "property_retraction",
                "history_count": max(0, len(values_only) - 1),
                "value_node": node_ref(latest.get("value")) if node_valued else None,
                "scope": latest.get("scope"),
                "scopes": sorted({entry["scope"] for entry in decorated}),
                "holder_id": latest.get("holder_id"),
                "holder_name": names.get(latest.get("holder_id") or ""),
                "value_type": definition.get("value_type"),
                "value": latest.get("value"),
                "event_id": latest.get("event_id"),
                "start": (latest.get("disclosure") or {}).get("start"),
                "retracted": latest.get("kind") == "property_retraction",
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
        ordered_categories = property_categories.order_categories(
            sorted({item["category"] for item in items}), node_type, config
        )
        items.sort(key=lambda item: (
            ordered_categories.index(item["category"]),
            item["property_id"], item["scope"], item["holder_id"] or "",
        ))

        # The other end of a relation: a record stored on another node whose
        # value points here. Same record, second entrance — nothing is copied.
        inbound: list[dict] = []
        for record in records:
            if record.get("kind") != "property_value" or record.get("node_id") == node_id:
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
            inbound.append({
                "record_id": record.get("id"),
                "property_id": record.get("property_id"),
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
                "scope": record.get("scope", "world"),
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
            or record.get("scope", "world") == "world"
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
