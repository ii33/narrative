"""Atomic independent screenplay tables in the project's graph connection."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from sqlite3 import Connection
from uuid import uuid4

from .adaptation_operations import _read as read_adaptation
from .screenplay_models import MODEL_BY_TABLE

TABLES = ("screenplays", "script_timelines", "adaptation_spans", "script_episodes", "script_acts",
          "script_structure_entries", "script_segments", "script_units", "script_blocks",
          "script_writing_bases", "script_entity_usages")
_COLUMNS = {
    "screenplays": ("timeline_id",), "script_timelines": ("screenplay_id", "source_timeline_id"), "adaptation_spans": ("timeline_id",),
    "script_episodes": ("screenplay_id", "position"), "script_acts": ("screenplay_id", "episode_id"),
    "script_structure_entries": ("screenplay_id", "parent_kind", "parent_id", "child_kind", "child_id", "position"),
    "script_segments": ("screenplay_id", "span_id"), "script_units": ("segment_id", "position"),
    "script_blocks": ("unit_id", "position"), "script_writing_bases": ("target_kind", "target_id", "stage"),
    "script_entity_usages": ("unit_id", "block_id", "entity_id"),
}


def now():
    return datetime.now(UTC).isoformat()


_id_context: ContextVar[tuple[Connection, dict, set[str]] | None] = ContextVar("screenplay_id_context", default=None)


def new_id(prefix):
    if prefix == "audit":
        return prefix + "_" + uuid4().hex
    if prefix == "preview":
        # Read-only previews are not stored objects and do not consume a number.
        return "preview-span"
    context = _id_context.get()
    if context is None:
        raise ValueError("剧本业务编号只能由写入事务内部分配")
    from .readable_ids import allocate_id
    db, state, existing = context
    existing.update(identifier for table in state.values() for identifier in table)
    identifier = allocate_id(db, "screenplay", prefix.replace("_", "-") + "-", existing)
    existing.add(identifier)
    return identifier


def object_fields(prefix, identifier=None):
    stamp = now()
    if identifier is None:
        identifier = new_id(prefix)
    else:
        context = _id_context.get()
        if context is None:
            raise ValueError("剧本业务编号只能由写入事务内部分配")
        db, _state, existing = context
        from .readable_ids import reserve_id
        # Custom IDs are not renamed. A numeric readable identity reserves its
        # actual family, even when an author used another type's prefix.
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*-)([1-9][0-9]*)", identifier)
        if match:
            reserve_id(db, "screenplay", match[1], identifier)
        existing.add(identifier)
    return {"id": identifier, "revision": 1, "created_at": stamp, "updated_at": stamp}


def expect_revision(obj, expected, field="revision"):
    if not isinstance(expected, int) or isinstance(expected, bool) or obj[field] != expected:
        raise ValueError(f"{field}已变化，请刷新后重试")


def timeline_id(db):
    row = db.execute("SELECT value FROM meta WHERE key='adaptation_timeline_id'").fetchone()
    if row:
        return row[0]
    # IDs are already scoped by project; no need to expose its private UUID.
    return "adaptation-main"


def empty_state():
    return {table: {} for table in TABLES}


def _quoted(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def inspect_storage(db):
    """Inspect only the fixed screenplay domain, without initializing anything."""
    objects = {row["name"].casefold(): dict(row) for row in db.execute(
        "SELECT name,type,sql FROM sqlite_master WHERE type IN ('table','view')")}
    present = [table for table in TABLES if table in objects]
    missing = [table for table in TABLES if table not in objects]
    counts = {table: 0 for table in TABLES}
    schemas, contents, diagnostics = [], [], []
    for table in present:
        columns = [dict(row) for row in db.execute(f"PRAGMA table_info({_quoted(table)})")]
        expected = {"id", "data", *_COLUMNS[table]}
        if objects[table]["type"] != "table" or {row["name"].casefold() for row in columns} != expected:
            diagnostics.append({"severity": "error", "code": "SCREENPLAY_TABLE_SHAPE", "table": table,
                                "message": "剧本表不符合当前结构，需显式重建而非迁移"})
        rows = list(db.execute(f"SELECT * FROM {_quoted(table)}"))
        counts[table] = len(rows)
        # The report exposes counts and a digest, never private screenplay rows.
        encoded = [json.dumps(list(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                              default=lambda value: {"bytes": value.hex()} if isinstance(value, bytes) else str(value)) for row in rows]
        contents.append({"table": table, "rows": sorted(encoded)})
        schemas.append({"table": table, "type": objects[table]["type"], "sql": objects[table]["sql"], "columns": columns})
    external = []
    for name, obj in objects.items():
        if name in TABLES or obj["type"] != "table" or name.startswith("sqlite_"):
            continue
        for row in db.execute(f"PRAGMA foreign_key_list({_quoted(name)})"):
            if row["table"].casefold() in TABLES:
                external.append({"table": name, "from": row["from"], "target_table": row["table"], "to": row["to"]})
    if external:
        diagnostics.append({"severity": "error", "code": "SCREENPLAY_EXTERNAL_REFERENCES",
                            "message": "域外数据表引用剧本对象，禁止自动重建或级联删除"})
    if not present:
        status = "uninitialized"
    elif missing or any(d["code"] == "SCREENPLAY_TABLE_SHAPE" for d in diagnostics):
        status = "incomplete"
        if missing:
            diagnostics.append({"severity": "error", "code": "SCREENPLAY_TABLES_MISSING",
                                "message": "剧本业务表不完整：" + "、".join(missing)})
    else:
        status = "ready"
    auxiliary_schema = [dict(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE type IN ('index','trigger') ORDER BY type,name")
        if row["tbl_name"].casefold() in TABLES]
    payload = {"schemas": schemas, "auxiliary_schema": auxiliary_schema, "contents": contents, "external_references": external}
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    total = sum(counts.values())
    return {"scope": "screenplay", "status": status, "present_tables": present, "missing_tables": missing,
            "row_counts": counts, "total_rows": total, "reset_required": status == "incomplete" and total > 0,
            "can_initialize_on_create": not external and (status != "incomplete" or total == 0),
            "expected_storage_fingerprint": fingerprint, "external_references": external, "diagnostics": diagnostics}


def _rebuild_tables(db, inspection):
    if inspection["external_references"]:
        raise ValueError("域外数据表引用剧本对象，不能重建剧本存储或级联删除")
    db.execute("PRAGMA defer_foreign_keys=ON")
    for table in reversed(TABLES):
        row = db.execute("SELECT name,type FROM sqlite_master WHERE name=? COLLATE NOCASE AND type IN ('table','view')", (table,)).fetchone()
        if row:
            kind = "VIEW" if row[1] == "view" else "TABLE"
            db.execute(f"DROP {kind} {_quoted(row[0])}")
    initialize_tables(db, persist_timeline_identity=False)


def inspect_screenplay_storage(ops):
    with ops._store.connect() as db:
        db.execute("BEGIN")
        return inspect_storage(db)


def reset_screenplay_storage(ops, reason, scope, expected_storage_fingerprint, confirm_discard):
    if scope != "screenplay" or confirm_discard is not True:
        raise ValueError("重建必须明确scope=screenplay且confirm_discard=true，仅丢弃剧本业务数据")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("重建剧本存储须提供原因")
    if not isinstance(expected_storage_fingerprint, str) or not expected_storage_fingerprint:
        raise ValueError("请先检查剧本存储并传入expected_storage_fingerprint")
    with ops._store.connect(write=True) as db:
        before = inspect_storage(db)
        if expected_storage_fingerprint != before["expected_storage_fingerprint"]:
            raise ValueError("剧本存储已变化，请重新检查行数与结构后再重建")
        _rebuild_tables(db, before)
        revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
        summary = {"scope": scope, "discarded_row_counts": before["row_counts"],
                   "discarded_total_rows": before["total_rows"], "previous_storage_fingerprint": expected_storage_fingerprint}
        db.execute("INSERT INTO actions(id,revision,action,reason,summary) VALUES(?,?,?,?,?)",
                   (new_id("audit"), revision, "screenplay_storage_reset", reason, json.dumps(summary, ensure_ascii=False)))
        return {"status": "reset", **summary, "storage": inspect_storage(db)}


def load_state(db):
    exists = {r[0].casefold() for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if set(TABLES) & exists and not set(TABLES) <= exists:
        raise ValueError("剧本业务表不完整；请先检查存储，非空旧剧本须显式重建后再新建，不会自动迁移或丢弃")
    state = empty_state()
    for table in TABLES:
        if table in exists:
            state[table] = {row[0]: json.loads(row[1]) for row in db.execute(f"SELECT id,data FROM {table}")}
    return state


def read_context(ops):
    with ops._store.connect() as db:
        db.execute("BEGIN")
        state, adaptation = load_state(db), read_adaptation(db)
        adaptation["timeline_id"] = timeline_id(db)
        from .prompt_settings import snapshot_from_db
        adaptation["_prompt_config"] = snapshot_from_db(db)
        from .project_style import read_style
        adaptation["_project_style"] = read_style(db)
        return state, adaptation


def initialize_tables(db, *, persist_timeline_identity=True):
    # executescript would implicitly commit the existing transaction.
    statements = [
        "CREATE TABLE IF NOT EXISTS screenplays(id TEXT PRIMARY KEY,timeline_id TEXT NOT NULL,data TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS script_timelines(id TEXT PRIMARY KEY,screenplay_id TEXT NOT NULL UNIQUE REFERENCES screenplays(id),source_timeline_id TEXT NOT NULL,data TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS adaptation_spans(id TEXT PRIMARY KEY,timeline_id TEXT NOT NULL REFERENCES script_timelines(id),data TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS script_episodes(id TEXT PRIMARY KEY,screenplay_id TEXT NOT NULL REFERENCES screenplays(id),position INTEGER NOT NULL,data TEXT NOT NULL,UNIQUE(screenplay_id,position))",
        "CREATE TABLE IF NOT EXISTS script_acts(id TEXT PRIMARY KEY,screenplay_id TEXT NOT NULL REFERENCES screenplays(id),episode_id TEXT REFERENCES script_episodes(id),data TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS script_structure_entries(id TEXT PRIMARY KEY,screenplay_id TEXT NOT NULL REFERENCES screenplays(id),parent_kind TEXT NOT NULL,parent_id TEXT NOT NULL,child_kind TEXT NOT NULL,child_id TEXT NOT NULL,position INTEGER NOT NULL,data TEXT NOT NULL,UNIQUE(parent_kind,parent_id,position),UNIQUE(child_kind,child_id))",
        "CREATE TABLE IF NOT EXISTS script_segments(id TEXT PRIMARY KEY,screenplay_id TEXT NOT NULL REFERENCES screenplays(id),span_id TEXT NOT NULL REFERENCES adaptation_spans(id),data TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS script_units(id TEXT PRIMARY KEY,segment_id TEXT NOT NULL REFERENCES script_segments(id),position INTEGER NOT NULL,data TEXT NOT NULL,UNIQUE(segment_id,position))",
        "CREATE TABLE IF NOT EXISTS script_blocks(id TEXT PRIMARY KEY,unit_id TEXT NOT NULL REFERENCES script_units(id),position INTEGER NOT NULL,data TEXT NOT NULL,UNIQUE(unit_id,position))",
        "CREATE TABLE IF NOT EXISTS script_writing_bases(id TEXT PRIMARY KEY,target_kind TEXT NOT NULL,target_id TEXT NOT NULL,stage TEXT NOT NULL,data TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS script_entity_usages(id TEXT PRIMARY KEY,unit_id TEXT NOT NULL REFERENCES script_units(id),block_id TEXT REFERENCES script_blocks(id),entity_id TEXT NOT NULL,data TEXT NOT NULL)",
    ]
    for statement in statements:
        db.execute(statement)
    if persist_timeline_identity:
        db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('adaptation_timeline_id',?)", (timeline_id(db),))


def validate_state(state, adaptation):
    from .screenplay_structure import validate_structure
    models = dict(MODEL_BY_TABLE)
    from .screenplay_writing_models import MODEL_BY_TABLE as writing_models
    models.update(writing_models)
    identities = set()
    for table, rows in state.items():
        if table not in models and rows:
            raise ValueError(f"尚未定义剧本表类型：{table}")
        for identifier, row in rows.items():
            if identifier != row.get("id"):
                raise ValueError("剧本对象身份不一致")
            if identifier in identities:
                raise ValueError("剧本对象ID重复")
            identities.add(identifier)
            rows[identifier] = models[table].model_validate(row).model_dump(mode="json")
    from .adaptation_operations import _issues
    for screenplay in state["screenplays"].values():
        local = state["script_timelines"].get(screenplay["timeline_id"])
        if not local or local["screenplay_id"] != screenplay["id"]:
            raise ValueError("剧本必须拥有自己的独立时间线")
    for local in state["script_timelines"].values():
        owner = state["screenplays"].get(local["screenplay_id"])
        if not owner or owner["timeline_id"] != local["id"] or local["source_timeline_id"] != adaptation["timeline_id"]:
            raise ValueError("剧本时间线归属或来源不正确")
        issues = _issues(local)
        if issues:
            raise ValueError(issues[0]["message"])
    for span in state["adaptation_spans"].values():
        if span["timeline_id"] not in state["script_timelines"]:
            raise ValueError("时间段必须绑定独立剧本时间线")
    for segment in state["script_segments"].values():
        screenplay = state["screenplays"].get(segment["screenplay_id"])
        span = state["adaptation_spans"].get(segment["span_id"])
        if not screenplay or not span or screenplay["timeline_id"] != span["timeline_id"]:
            raise ValueError("剧本段落的剧本或时间段引用无效")
    validate_structure(state)
    from .screenplay_writing import validate_writing
    validate_writing(state, adaptation)


def write_transaction(ops, reason, mutate, initialize=False):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("剧本写入须提供原因")
    with ops._store.connect(write=True) as db:
        if initialize:
            inspection = inspect_storage(db)
            if inspection["status"] == "incomplete":
                if inspection["external_references"]:
                    raise ValueError("域外数据表引用剧本对象，不能自动重建空旧域")
                if not inspection["can_initialize_on_create"]:
                    raise ValueError("剧本业务表不完整且含已有数据；请先检查行数，再显式重建剧本存储后新建，旧剧本未改动")
                _rebuild_tables(db, inspection)
        state, adaptation = load_state(db), read_adaptation(db)
        adaptation["timeline_id"] = timeline_id(db)
        from .prompt_settings import snapshot_from_db
        adaptation["_prompt_config"] = snapshot_from_db(db)
        from .project_style import read_style
        adaptation["_project_style"] = read_style(db)
        before = copy.deepcopy(state)
        token = _id_context.set((db, state, {identifier for table in state.values() for identifier in table}))
        try:
            result = mutate(state, adaptation, db)
        finally:
            _id_context.reset(token)
        validate_state(state, adaptation)
        if not initialize and not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='screenplays' COLLATE NOCASE").fetchone():
            raise ValueError("请先显式创建剧本或时间段")
        if initialize:
            initialize_tables(db)
        # Rewrite only the independent current-object domain, within this one
        # transaction. This permits position swaps without transient collisions.
        for table in reversed(TABLES):
            db.execute(f"DELETE FROM {table}")
        for table in TABLES:
            columns = ("id",) + _COLUMNS[table] + ("data",)
            placeholders = ",".join("?" for _ in columns)
            for row in state[table].values():
                values = [row.get(key) for key in columns[:-1]] + [json.dumps(row, ensure_ascii=False)]
                db.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES ({placeholders})", values)
        revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
        changed = {table: [key for key in set(before[table]) | set(state[table])
                           if before[table].get(key) != state[table].get(key)] for table in TABLES}
        db.execute("INSERT INTO actions(id,revision,action,reason,summary) VALUES(?,?,?,?,?)",
                   (new_id("audit"), revision, "screenplay", reason, json.dumps(changed, ensure_ascii=False)))
        return result
