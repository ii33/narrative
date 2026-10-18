import json

from .checks import check, digest
from .models import OBJECT_TABLES, ChangeSet, Event, Evidence, StateFact, TemporalScope, uid
from .store import Store


class Conflict(ValueError):
    pass


class Service:
    def __init__(self, store: Store):
        self.store = store

    def import_text(self, title: str, text: str, *, attribution: dict | None = None) -> str:
        if not title.strip() or not text.strip():
            raise ValueError("章节标题和原文不能为空")
        # Identity normalization: offsets index the exact decoded Unicode text.
        sha = digest(text)
        with self.store.connect(write=True) as db:
            prior = db.execute(
                "SELECT id FROM sources WHERE title=? AND hash=?", (title, sha)
            ).fetchone()
            if prior:
                if attribution is not None:
                    db.execute(
                        "INSERT OR IGNORE INTO meta(key,value) VALUES (?,?)",
                        (
                            f"source_attribution:{prior[0]}",
                            json.dumps(attribution, ensure_ascii=False),
                        ),
                    )
                return prior[0]
            source_id = uid()
            db.execute(
                "INSERT INTO sources(id,title,text,hash) VALUES (?,?,?,?)",
                (source_id, title, text, sha),
            )
            if attribution is not None:
                db.execute(
                    "INSERT INTO meta(key,value) VALUES (?,?)",
                    (
                        f"source_attribution:{source_id}",
                        json.dumps(attribution, ensure_ascii=False),
                    ),
                )
            revision = (
                int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]) + 1
            )
            db.execute("UPDATE meta SET value=? WHERE key='revision'", (str(revision),))
            db.execute(
                "INSERT INTO revisions(revision,data) VALUES (?,?)",
                (revision, json.dumps({"import_source": source_id})),
            )
        return source_id

    def locate(self, source_id: str, quote: str) -> list[Evidence]:
        if not quote:
            return []
        source = self.store.source(source_id)
        result, cursor = [], 0
        while (start := source["text"].find(quote, cursor)) >= 0:
            result.append(
                Evidence(
                    source_id=source_id,
                    source_hash=source["hash"],
                    start=start,
                    end=start + len(quote),
                    quote=quote,
                )
            )
            cursor = start + 1
        return result

    def propose(self, change: ChangeSet) -> str:
        with self.store.connect(write=True) as db:
            prior = db.execute("SELECT data FROM changes WHERE id=?", (change.id,)).fetchone()
            if prior:
                if ChangeSet.model_validate_json(prior[0]) != change:
                    raise Conflict("候选 ID 已存在且内容不同")
                return change.id
            db.execute(
                "INSERT INTO changes(id,data) VALUES (?,?)", (change.id, change.model_dump_json())
            )
        return change.id

    def _require_evidence(self, evidence: Evidence) -> None:
        try:
            source = self.store.source(evidence.source_id)
        except (KeyError, ValueError) as exc:
            raise ValueError("修订证据来源不存在") from exc
        if (
            evidence.source_hash != source["hash"]
            or source["text"][evidence.start : evidence.end] != evidence.quote
        ):
            raise ValueError("修订证据必须精确对应原文与来源哈希")

    def _propose_anchored_correction(self, operation: Event | StateFact) -> str:
        change = ChangeSet(
            expected_revision=self.store.revision,
            operations=[operation],
            source="correction",
            note="依据原文证据修订显式故事先后或状态锚点；须审核后采用。",
        )
        errors = [d.message for d in self.diagnostics(change) if d.severity == "error"]
        if errors:
            raise ValueError("；".join(errors))
        return self.propose(change)

    def propose_event_order(
        self, event_id: str, after_event_id: str, evidence: Evidence
    ) -> str:
        """Propose an evidenced predecessor; never infer story order from source offsets."""
        self._require_evidence(evidence)
        events = {event["id"]: event for event in self.store.objects("events")}
        saved, predecessor = events.get(event_id), events.get(after_event_id)
        if not saved or saved.get("status") != "accepted":
            raise ValueError("待修订事件不存在或不是已采用事件")
        if not predecessor or predecessor.get("status") != "accepted":
            raise ValueError("先行事件不存在或不是已采用事件")
        if event_id == after_event_id:
            raise ValueError("事件不能先于自身")
        if saved.get("time_kind") in {"point", "interval"}:
            raise ValueError("已有数值时间的事件不能改写为纯相对时间")
        revised = Event.model_validate(
            {
                **saved,
                "time_kind": "relative",
                "after_event_id": after_event_id,
                "evidence": [*saved.get("evidence", []), evidence.model_dump()],
            }
        )
        return self._propose_anchored_correction(revised)

    def propose_state_window(
        self,
        state_id: str,
        *,
        start_event_id: str | None = None,
        end_event_id: str | None = None,
        evidence: Evidence,
    ) -> str:
        """Propose explicit event anchors for an accepted state; absent args retain anchors."""
        if start_event_id is None and end_event_id is None:
            raise ValueError("状态修订须提供至少一个事件锚点")
        self._require_evidence(evidence)
        saved = next(
            (
                record
                for record in self.store.objects("records")
                if record["id"] == state_id and record["kind"] == "state"
            ),
            None,
        )
        if not saved or saved.get("status") != "accepted":
            raise ValueError("待修订状态不存在或不是已采用状态")
        current = TemporalScope.model_validate(saved.get("validity", {}))
        if (start_event_id and current.start is not None) or (
            end_event_id and current.end is not None
        ):
            raise ValueError("已有数值有效期的状态不能改写为事件锚点")
        events = {event["id"]: event for event in self.store.objects("events")}
        start = start_event_id or current.start_event_id
        end = end_event_id or current.end_event_id
        for anchor in (start, end):
            if anchor and (anchor not in events or events[anchor].get("status") != "accepted"):
                raise ValueError("状态锚点须引用已采用事件")
        if start and end:
            ancestor = events[end].get("after_event_id")
            visited: set[str] = set()
            while ancestor and ancestor != start and ancestor not in visited:
                visited.add(ancestor)
                ancestor = events.get(ancestor, {}).get("after_event_id")
            if ancestor != start:
                raise ValueError("状态有效期起止事件须有明确先后关系")
        validity = current.model_copy(
            update={"start_event_id": start, "end_event_id": end}
        )
        revised = StateFact.model_validate(
            {
                **saved,
                "validity": validity.model_dump(),
                "evidence": [*saved.get("evidence", []), evidence.model_dump()],
            }
        )
        return self._propose_anchored_correction(revised)

    def candidate(self, change_id: str) -> tuple[ChangeSet, dict]:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM changes WHERE id=?", (change_id,)).fetchone()
        if not row:
            raise ValueError("候选不存在")
        return ChangeSet.model_validate_json(row["data"]), dict(row)

    def edit(self, change: ChangeSet, expected_version: int) -> None:
        with self.store.connect(write=True) as db:
            result = db.execute(
                "UPDATE changes SET data=?,version=version+1 WHERE id=? AND version=? AND status='pending'",
                (change.model_dump_json(), change.id, expected_version),
            )
            if result.rowcount != 1:
                raise Conflict("候选已改变或已审核，请刷新")

    def diagnostics(self, change: ChangeSet):
        return check(
            change,
            self.store.revision,
            {e["id"]: e for e in self.store.objects("entities")},
            {s["id"]: s for s in self.store.rows("sources")},
            {e["id"]: e for e in self.store.objects("events")},
            {e["id"]: e for e in self.store.objects("records")},
            {e["id"]: e for e in self.store.objects("assertions")},
        )

    def select_operations(self, change_id: str, keep_ids: list[str], expected_version: int) -> dict:
        """Retain reviewed items and preserve excluded items as a rejected batch."""
        with self.store.connect(write=True) as db:
            row = db.execute("SELECT * FROM changes WHERE id=?", (change_id,)).fetchone()
            if not row or row["status"] != "pending" or row["version"] != expected_version:
                raise Conflict("候选已改变或已审核，请刷新")
            change = ChangeSet.model_validate_json(row["data"])
            available = {op.id for op in change.operations}
            if len(available) != len(change.operations):
                raise ValueError("候选含重复对象 ID，请先在结构化编辑中修正")
            if len(keep_ids) != len(set(keep_ids)) or set(keep_ids) - available:
                raise ValueError("保留条目必须来自当前候选，且不能重复")
            retained = [op for op in change.operations if op.id in keep_ids]
            excluded = [op for op in change.operations if op.id not in keep_ids]
            result = {
                "kept_count": len(retained),
                "rejected_count": len(excluded),
                "rejected_change_id": None,
                "status": "pending",
                "version": expected_version,
            }
            if not excluded:
                return result
            if not retained:
                db.execute("UPDATE changes SET status='rejected' WHERE id=?", (change_id,))
                return {**result, "status": "rejected", "rejected_change_id": change_id}
            rejected = change.model_copy(
                update={
                    "id": uid(),
                    "operations": excluded,
                    "note": f"从候选 {change.id} 第 {expected_version} 版逐条审核拒绝。\n{change.note}",
                }
            )
            change.operations = retained
            db.execute(
                "UPDATE changes SET data=?,version=version+1 WHERE id=?",
                (change.model_dump_json(), change_id),
            )
            db.execute(
                "INSERT INTO changes(id,data,status) VALUES (?,?,'rejected')",
                (rejected.id, rejected.model_dump_json()),
            )
            return {
                **result,
                "rejected_change_id": rejected.id,
                "version": expected_version + 1,
            }

    def diff(self, change: ChangeSet) -> list[dict]:
        current = {
            obj["id"]: obj
            for table in ("entities", "assertions", "events", "records")
            for obj in self.store.objects(table)
        }
        return [
            {"id": op.id, "before": current.get(op.id), "after": op.model_dump()}
            for op in change.operations
        ]

    def accept(self, change_id: str, expected_version: int) -> int:
        with self.store.connect(write=True) as db:
            row = db.execute("SELECT * FROM changes WHERE id=?", (change_id,)).fetchone()
            if not row:
                raise ValueError("候选不存在")
            if row["status"] == "accepted":
                return row["result_revision"]
            if row["status"] != "pending" or row["version"] != expected_version:
                raise Conflict("候选已改变或已拒绝，请刷新")
            change = ChangeSet.model_validate_json(row["data"])
            revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            entities = {
                r["id"]: json.loads(r["data"]) for r in db.execute("SELECT * FROM entities")
            }
            sources = {r["id"]: dict(r) for r in db.execute("SELECT * FROM sources")}
            events = {r["id"]: json.loads(r["data"]) for r in db.execute("SELECT * FROM events")}
            records = {r["id"]: json.loads(r["data"]) for r in db.execute("SELECT * FROM records")}
            assertions = {
                r["id"]: json.loads(r["data"]) for r in db.execute("SELECT * FROM assertions")
            }
            errors = [
                d.message
                for d in check(change, revision, entities, sources, events, records, assertions)
                if d.severity == "error"
            ]
            if errors:
                raise Conflict("；".join(errors))
            audit = []
            for op in change.operations:
                table = OBJECT_TABLES[op.kind]
                for other in {"entities", "assertions", "events", "records"} - {table}:
                    if db.execute(f"SELECT 1 FROM {other} WHERE id=?", (op.id,)).fetchone():
                        raise Conflict("对象 ID 不能跨类型复用")
                before = db.execute(f"SELECT data FROM {table} WHERE id=?", (op.id,)).fetchone()
                audit.append(
                    {
                        "id": op.id,
                        "before": json.loads(before[0]) if before else None,
                        "after": op.model_dump(),
                    }
                )
                db.execute(
                    f"INSERT INTO {table}(id,data) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                    (op.id, op.model_dump_json()),
                )
            revision += 1
            db.execute("UPDATE meta SET value=? WHERE key='revision'", (str(revision),))
            db.execute(
                "UPDATE changes SET status='accepted',result_revision=? WHERE id=?",
                (revision, change_id),
            )
            db.execute(
                "INSERT INTO revisions(revision,change_id,data) VALUES (?,?,?)",
                (revision, change_id, json.dumps(audit, ensure_ascii=False)),
            )
        return revision

    def reject(self, change_id: str, expected_version: int):
        with self.store.connect(write=True) as db:
            row = db.execute(
                "SELECT status,version FROM changes WHERE id=?", (change_id,)
            ).fetchone()
            if row and row["status"] == "rejected":
                return
            if not row or row["status"] != "pending" or row["version"] != expected_version:
                raise Conflict("候选已改变或已接受")
            db.execute("UPDATE changes SET status='rejected' WHERE id=?", (change_id,))
