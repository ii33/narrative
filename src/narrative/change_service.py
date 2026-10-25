"""Unified change candidates: graph objects and property records in one gate.

The storage stays split: graph objects live in ``objects``/``evidence_links``,
property records in ``property_records``. This service adds the missing upper
contract:

* one candidate can contain new nodes, events, assertions, evidence and
  property records;
* one precheck validates graph references and property references together,
  including nodes created in the same candidate;
* one accept writes both families in a single SQLite transaction and bumps the
  global revision once.

It deliberately does not merge the tables or rewrite the existing two-stage
APIs; those remain available for callers that only need one family.
"""

from __future__ import annotations

import json
from uuid import uuid4

from . import dependency_core
from .graph_service import GraphService
from .property_store import PropertyStore

CHANGE_SOURCES = {"manual", "model", "correction"}


class ChangeService:
    def __init__(self, graph: GraphService, properties: PropertyStore):
        self.graph = graph
        self.properties = properties
        self.store = graph.store

    def _has_table(self) -> bool:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='change_candidates'"
            ).fetchone()
        return row is not None

    def _ensure_table(self) -> None:
        """Create the table lazily on first write; opening a project stays read-only."""
        with self.store.connect(write=True) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS change_candidates (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL,
                    version INTEGER NOT NULL, expected_revision INTEGER NOT NULL,
                    result_revision INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS change_candidates_status "
                "ON change_candidates(status)"
            )

    # -- validation ----------------------------------------------------
    def _merged_snapshot(self, graph_objects: list[dict]) -> tuple[dict, list[dict]]:
        base = self.store.snapshot()
        normalized, issues = self.graph.normalize(graph_objects)
        if issues:
            return base, issues
        objects = {item["id"]: item for item in base["objects"]}
        for item in normalized:
            objects[item["id"]] = item
        groups: dict[str, list[dict]] = {
            key: [] for key in ("nodes", "contexts", "timelines", "source_orders")
        }
        for item in objects.values():
            groups.setdefault(item["kind"] + "s", []).append(item)
        return (
            base
            | {
                "objects": list(objects.values()),
                **groups,
            },
            [],
        )

    def precheck(
        self,
        graph_objects: list[dict] | None,
        property_records: list[dict] | None,
        expected_revision: int,
    ) -> list[dict]:
        graph_objects = graph_objects or []
        property_records = property_records or []
        if not graph_objects and not property_records:
            return [
                {
                    "family": "change",
                    "severity": "error",
                    "code": "EMPTY",
                    "message": "统一候选至少须包含图对象或属性记录",
                }
            ]
        diagnostics: list[dict] = []
        merged: dict | None = None
        if graph_objects:
            graph_diagnostics = self.graph.precheck(
                graph_objects, expected_revision, allow_cross_family=True
            )
            diagnostics.extend(dict(item, family="graph") for item in graph_diagnostics)
            merged, issues = self._merged_snapshot(graph_objects)
            if issues:
                diagnostics.extend(dict(item, family="graph") for item in issues)
                merged = None
        if property_records:
            snapshot = merged or self.store.snapshot()
            property_diagnostics = self.properties.diagnostics(
                property_records, expected_revision, snapshot
            )
            diagnostics.extend(dict(item, family="property") for item in property_diagnostics)
        elif merged is not None:
            accepted_properties = self.properties.accepted_records(expected_revision)
            dependency_diagnostics = dependency_core.validate_support(
                merged, accepted_properties
            )
            diagnostics.extend(
                dict(item, family="dependency") for item in dependency_diagnostics
            )
        return diagnostics

    # -- pending candidates --------------------------------------------
    def propose(
        self,
        graph_objects: list[dict] | None,
        property_records: list[dict] | None,
        expected_revision: int,
        *,
        source: str = "manual",
        note: str = "",
        run_id: str | None = None,
    ) -> str:
        if source not in CHANGE_SOURCES:
            raise ValueError("统一候选来源无效")
        graph_objects = graph_objects or []
        property_records = property_records or []
        self._ensure_table()
        diagnostics = self.precheck(graph_objects, property_records, expected_revision)
        errors = [item["message"] for item in diagnostics if item["severity"] == "error"]
        if errors:
            raise ValueError("；".join(errors))
        normalized_graph, graph_issues = (
            self.graph.normalize(graph_objects) if graph_objects else ([], [])
        )
        normalized_properties, property_issues = self.properties.normalize_records(
            property_records
        )
        issues = [*graph_issues, *property_issues]
        if issues:
            raise ValueError("；".join(item["message"] for item in issues))
        candidate_id = uuid4().hex
        payload = {
            "id": candidate_id,
            "graph_objects": normalized_graph,
            "property_records": normalized_properties,
            "source": source,
            "note": note,
            "run_id": run_id,
            "expected_revision": expected_revision,
            "diagnostics": diagnostics,
        }
        with self.store.connect(write=True) as db:
            db.execute(
                "INSERT INTO change_candidates"
                "(id,data,status,version,expected_revision) VALUES (?,?,?,?,?)",
                (
                    candidate_id,
                    json.dumps(payload, ensure_ascii=False),
                    "pending",
                    1,
                    expected_revision,
                ),
            )
        return candidate_id

    def candidates(self, status: str | None = None) -> list[dict]:
        if not self._has_table():
            return []
        query = "SELECT * FROM change_candidates"
        parameters: tuple = ()
        if status:
            query += " WHERE status=?"
            parameters = (status,)
        query += " ORDER BY created_at, id"
        with self.store.connect() as db:
            rows = list(db.execute(query, parameters))
        results = []
        for row in rows:
            payload = json.loads(row["data"])
            results.append(
                {
                    "id": row["id"],
                    "status": row["status"],
                    "version": row["version"],
                    "expected_revision": row["expected_revision"],
                    "result_revision": row["result_revision"],
                    "source": payload.get("source"),
                    "note": payload.get("note"),
                    "graph_count": len(payload.get("graph_objects") or []),
                    "property_count": len(payload.get("property_records") or []),
                    "created_at": row["created_at"],
                }
            )
        return results

    def candidate(self, candidate_id: str) -> dict:
        if not self._has_table():
            raise ValueError("统一候选不存在")
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM change_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
        if row is None:
            raise ValueError("统一候选不存在")
        payload = json.loads(row["data"])
        return payload | {
            "status": row["status"],
            "version": row["version"],
            "result_revision": row["result_revision"],
        }

    def accept(self, candidate_id: str, version: int) -> int:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM change_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise ValueError("统一候选不存在")
            if row["status"] != "pending" or row["version"] != version:
                raise ValueError("候选状态或版本已变化")
            change = json.loads(row["data"])
            revision = int(
                db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
            )
        if change["expected_revision"] != revision:
            raise ValueError("正式资料版本已变化，请重新建立统一候选")
        diagnostics = self.precheck(
            change.get("graph_objects"), change.get("property_records"), revision
        )
        errors = [item["message"] for item in diagnostics if item["severity"] == "error"]
        if errors:
            raise ValueError("；".join(errors))
        graph_objects = change.get("graph_objects") or []
        property_records = change.get("property_records") or []
        with self.store.connect(write=True) as db:
            current = int(
                db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
            )
            if current != change["expected_revision"]:
                raise ValueError("正式资料版本已变化，请重新建立统一候选")
            next_revision = current + 1
            audit = []
            for obj in graph_objects:
                before = db.execute(
                    "SELECT kind,data FROM objects WHERE id=?", (obj["id"],)
                ).fetchone()
                if before and change.get("source") != "correction":
                    raise ValueError("已有对象只能由显式修订候选更新")
                audit.append(
                    {"family": "graph", "id": obj["id"], "before": dict(before) if before else None, "after": obj}
                )
                db.execute(
                    "INSERT INTO objects VALUES (?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,"
                    "data=excluded.data,accepted_revision=excluded.accepted_revision",
                    (
                        obj["id"], obj["kind"],
                        json.dumps(obj, ensure_ascii=False), next_revision,
                    ),
                    )
            for record in property_records:
                audit.append({"family": "property", "id": record["id"], "before": None, "after": record})
                db.execute(
                    "INSERT INTO property_records"
                    "(id,kind,data,accepted_revision,candidate_id) VALUES (?,?,?,?,?)",
                    (
                        record["id"], record.get("kind", ""),
                        json.dumps(record, ensure_ascii=False),
                        next_revision, candidate_id,
                    ),
                )
            db.execute("UPDATE meta SET value=? WHERE key='revision'", (str(next_revision),))
            db.execute(
                "UPDATE change_candidates SET status='accepted',result_revision=? WHERE id=?",
                (next_revision, candidate_id),
            )
            db.execute(
                "INSERT INTO revisions(revision,candidate_id,data) VALUES (?,?,?)",
                (
                    next_revision,
                    candidate_id,
                    json.dumps(
                        {
                            "operations": graph_objects,
                            "property_operations": property_records,
                            "audit": audit,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        return next_revision

    def reject(self, candidate_id: str, version: int) -> None:
        with self.store.connect(write=True) as db:
            row = db.execute(
                "SELECT status,version FROM change_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise ValueError("统一候选不存在")
            if row["status"] != "pending" or row["version"] != version:
                raise ValueError("候选状态或版本已变化")
            db.execute(
                "UPDATE change_candidates SET status='rejected' WHERE id=?", (candidate_id,)
            )
