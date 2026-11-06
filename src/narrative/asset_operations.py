"""Independent visual assets, accessed exclusively through project Operations."""
from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import cast

from .content_errors import (
    ContentConflictError,
    ContentGuardError,
    ContentValidationError,
    content_item,
    content_write,
)
from .graph_store import GraphStore, active_connection, on_rollback
from .node_material import ALLOWED_SUFFIXES, MAX_IMAGE_BYTES, _as_path, _position
from .readable_ids import allocate_id

CATEGORIES = {"appearance": "person", "item": "item", "location": "location", "material": None}
ASSET_PREFIXES = {"appearance": "appearance-", "item": "item-design-", "location": "location-design-", "material": "material-"}


class _AttachedAssetDb:
    """A narrow SQL interface: only fixed asset statements reach the attached DB."""
    _queries = {
        'SELECT data FROM assets WHERE id=?': 'SELECT data FROM narrative_assets.assets WHERE id=?',
        "SELECT value FROM meta WHERE key='revision'": "SELECT value FROM narrative_assets.meta WHERE key='revision'",
        'SELECT data FROM assets ORDER BY rowid': 'SELECT data FROM narrative_assets.assets ORDER BY rowid',
        'SELECT id FROM assets': 'SELECT id FROM narrative_assets.assets',
        'SELECT data FROM assets': 'SELECT data FROM narrative_assets.assets',
        'SELECT 1 FROM assets WHERE id=?': 'SELECT 1 FROM narrative_assets.assets WHERE id=?',
        'DELETE FROM assets WHERE id=?': 'DELETE FROM narrative_assets.assets WHERE id=?',
        "UPDATE meta SET value=? WHERE key='revision'": "UPDATE narrative_assets.meta SET value=? WHERE key='revision'",
        'INSERT INTO assets VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data': 'INSERT INTO narrative_assets.assets VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
        'INSERT INTO audit(revision,action,asset_id,summary) VALUES(?,?,?,?)': 'INSERT INTO narrative_assets.audit(revision,action,asset_id,summary) VALUES(?,?,?,?)',
        'SELECT value FROM meta WHERE key=?': 'SELECT value FROM narrative_assets.meta WHERE key=?',
        'INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value': 'INSERT INTO narrative_assets.meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
    }

    def __init__(self, connection):
        self.connection = connection

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def execute(self, sql, parameters=()):
        statement = self._queries.get(' '.join(sql.split()))
        if statement is None:
            raise RuntimeError('资产事务只允许明确限定资产库的已注册SQL')
        return self.connection.execute(statement, parameters)


def _initialize_asset_tables(db, schema):
    if schema not in {'main', 'narrative_assets'}:
        raise RuntimeError('未知资产数据库域')
    db.execute(f'CREATE TABLE IF NOT EXISTS {schema}.assets(id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    db.execute(f'CREATE TABLE IF NOT EXISTS {schema}.meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
    db.execute(f"INSERT OR IGNORE INTO {schema}.meta VALUES('revision',0)")
    db.execute(f'CREATE TABLE IF NOT EXISTS {schema}.audit(revision INTEGER PRIMARY KEY, action TEXT NOT NULL, asset_id TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')


class AssetOperationsMixin:
    _store: GraphStore

    def _asset_root(self) -> Path:
        from .project_package import layout
        return layout(self._store.path).material / "assets"

    @contextmanager
    def _asset_db(self, *, write=False):
        root = self._asset_root()
        root.mkdir(parents=True, exist_ok=True)
        path = (root / 'assets.sqlite3').resolve()
        shared = active_connection(self._store.path)
        if shared is not None:
            attached = {row[1]: Path(row[2]).resolve() for row in shared.execute('PRAGMA database_list') if row[2]}
            if 'narrative_assets' in attached and attached['narrative_assets'] != path:
                raise ContentGuardError('共享事务的资产数据库归属不匹配')
            if 'narrative_assets' not in attached:
                shared.execute('ATTACH DATABASE ? AS narrative_assets', (str(path),))
            _initialize_asset_tables(shared, 'narrative_assets')
            yield cast(sqlite3.Connection, _AttachedAssetDb(shared))
            return
        db = sqlite3.connect(path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            _initialize_asset_tables(db, 'main')
            db.commit()
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _asset_snapshot(self, domain):
        method = {"adaptation": "adaptation_snapshot", "extraction": "graph_snapshot"}.get(domain)
        if method is None:
            raise ValueError("资产数据域须为 adaptation 或 extraction")
        return getattr(self, method)()

    def asset_moments(self, domain="adaptation") -> list[dict]:
        snapshot = self._asset_snapshot(domain)
        nodes = [node for node in snapshot["nodes"] if node.get("node_type") in
                 {"storyline", "big_event", "scene", "event", "action"}
                 and not node.get("suppressed") and not node.get("retracted")]
        if domain == "adaptation":
            return sorted(nodes, key=lambda node: node["sort_id"])
        return sorted(nodes, key=lambda node: _position(node["id"], snapshot) or (-1, -1))

    def _asset_position(self, moment, snapshot, domain):
        if not moment:
            return None
        node = next((node for node in snapshot["nodes"] if node["id"] == moment
                     and not node.get("suppressed") and not node.get("retracted")), None)
        if node is None or node.get("node_type") not in {"storyline", "big_event", "scene", "event", "action"}:
            raise ValueError(f"资产时间点不存在：{moment}")
        return node["sort_id"] if domain == "adaptation" else _position(moment, snapshot)

    def _asset_card(self, card):
        root = self._asset_root()
        result = dict(card)
        for field in ("images", "reference_images"):
            result[field] = [image | {"path": str(root / image["file"])}
                             for image in card.get(field, [])]
        return result

    def get_asset(self, asset_id: str) -> dict:
        with self._asset_db() as db:
            row = db.execute("SELECT data FROM assets WHERE id=?", (asset_id,)).fetchone()
            revision = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
        if not row:
            raise ContentGuardError("资产不存在")
        return self._asset_card(json.loads(row[0])) | {"revision": revision}

    def list_assets(self, category: str, moment="", node_id="", domain="adaptation") -> dict:
        if category not in CATEGORIES:
            raise ContentGuardError("未知资产类别，无法确定资产路由")
        snapshot = self._asset_snapshot(domain)
        current = self._asset_position(moment, snapshot, domain) if category != "material" else None
        with self._asset_db() as db:
            cards = [json.loads(row[0]) for row in db.execute("SELECT data FROM assets ORDER BY rowid")]
            revision = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
        shown, diagnostics = [], []
        for card in cards:
            if card["category"] != category or (category != "material" and card["domain"] != domain):
                continue
            if node_id and card["node_id"] != node_id:
                continue
            try:
                start = self._asset_position(card["valid_from"], snapshot, domain)
                end = self._asset_position(card["valid_to"], snapshot, domain)
                if start is not None and end is not None and start >= end:
                    raise ValueError("资产时间区间倒置，请调整生效或失效时间")
                if category != "material" and ((current is None and end is not None) or
                    (current is not None and ((start is not None and current < start) or
                                             (end is not None and current >= end)))):
                    continue
            except ValueError as exc:
                diagnostics.append({"asset_id": card["id"], "message": str(exc)})
                continue
            shown.append(self._asset_card(card))
        return {"assets": shown, "revision": revision, "category": category,
                "moment": moment, "node_id": node_id, "domain": domain, "diagnostics": diagnostics}

    @content_write
    def save_asset(self, payload: dict, expected_revision=None) -> dict:
        if not isinstance(payload, dict):
            raise ContentGuardError("资产路由须为明确对象")
        with ExitStack() as rollback:
            with self._asset_db(write=True) as db:
                revision = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
                if expected_revision is not None and revision != expected_revision:
                    raise ContentConflictError("资产已改变，请刷新后重试")
                if payload.get("id"):
                    asset_id = str(payload["id"])
                else:
                    category = payload.get("category")
                    if category not in CATEGORIES:
                        raise ContentGuardError("未知资产类别，无法确定资产路由")
                    asset_id = allocate_id(db, "assets", ASSET_PREFIXES[category],
                                           (row[0] for row in db.execute("SELECT id FROM assets")))
                row = db.execute("SELECT data FROM assets WHERE id=?", (asset_id,)).fetchone()
                old = json.loads(row[0]) if row else {}
                if payload.get("id") and not row:
                    raise ContentGuardError("资产不存在")
                card = old | {key: value for key, value in payload.items() if key in
                              {"category", "node_id", "title", "detail", "domain", "valid_from", "valid_to", "prompt"}}
                card["id"] = asset_id
                for field in ("node_id", "title", "detail", "valid_from", "valid_to", "prompt"):
                    card[field] = str(card.get(field) or "")
                card.setdefault("domain", "adaptation")
                category = card.get("category")
                if category not in CATEGORIES:
                    raise ContentGuardError("未知资产类别，无法确定资产路由")
                card["title"] = card["title"].strip()
                if card['domain'] not in ('adaptation', 'extraction'):
                    raise ContentGuardError('资产域须明确为adaptation或extraction')
                snapshot = self._asset_snapshot(card["domain"])
                if category == "material":
                    if card['node_id']:
                        raise ContentGuardError('材质资产不关联节点，无法按此路由保存')
                    if card['valid_from'] or card['valid_to']:
                        raise ValueError('材质不关联时间点')
                else:
                    node = next((node for node in snapshot["nodes"] if node["id"] == card["node_id"]
                                 and not node.get("suppressed") and not node.get("retracted")), None)
                    if node is None or node.get("node_type") != CATEGORIES[category]:
                        raise ContentGuardError("资产须关联对应类别及所选域中的有效节点")
                if not card["title"]:
                    raise ContentValidationError('资产标题不能为空', problems=[{'code': 'CONTENT_VALIDATION', 'message': '资产标题不能为空', 'path': ['payload', 'title']}])
                start = self._asset_position(card["valid_from"], snapshot, card["domain"])
                end = self._asset_position(card["valid_to"], snapshot, card["domain"])
                if start is not None and end is not None and start >= end:
                    raise ValueError("失效时间须在生效时间之后")
                prepared = {field: self._prepare_asset_images(payload.get(field), old.get(field, []), field)
                            for field in ('images', 'reference_images')}
                for field in ('images', 'reference_images'):
                    card[field] = self._asset_images(db, asset_id, prepared[field], rollback)
                revision += 1
                db.execute("INSERT INTO assets VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                           (asset_id, json.dumps(card, ensure_ascii=False)))
                db.execute("UPDATE meta SET value=? WHERE key='revision'", (revision,))
                db.execute("INSERT INTO audit(revision,action,asset_id,summary) VALUES(?,?,?,?)",
                           (revision, "update" if row else "create", asset_id, card["title"]))
                result = self._asset_card(card) | {'revision': revision}
            rollback.pop_all()
            return result

    def _prepare_asset_images(self, entries, old, field):
        if entries is None:
            return old
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise ValueError("图片须为对象数组")
        existing = {image["id"]: image for image in old}
        prepared = []
        # Validate every source before importing any files.
        for index, entry in enumerate(entries):
            with content_item(['payload', field, index], entry):
                image_id = str(entry.get("id") or "")
                if image_id in existing:
                    prepared.append(existing[image_id] | {"caption": str(entry.get("caption") or "")})
                else:
                    source = _as_path(entry.get("path"))
                    if not source.is_file() or source.suffix.lower() not in ALLOWED_SUFFIXES:
                        raise ValueError("参考图或图片须为有效的本地图片文件")
                    if source.stat().st_size > MAX_IMAGE_BYTES:
                        raise ValueError("图片超过20MB")
                    prepared.append((source, str(entry.get("caption") or "")))
        return prepared

    def _asset_images(self, db, asset_id, prepared, rollback):
        result = []
        for image in prepared:
            if isinstance(image, dict):
                result.append(image)
                continue
            source, caption = image
            existing_ids = [item["id"] for row in db.execute("SELECT data FROM assets")
                            for field in ("images", "reference_images") for item in json.loads(row[0]).get(field, [])]
            image_id = allocate_id(db, "assets", "asset-image-", existing_ids)
            target = self._asset_root() / asset_id / (image_id + source.suffix.lower())
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as output:
                rollback.callback(target.unlink, missing_ok=True)
                on_rollback(self._store.path, lambda path=target: path.unlink(missing_ok=True))
                with source.open('rb') as original:
                    shutil.copyfileobj(original, output)
            result.append({"id": image_id, "file": target.relative_to(self._asset_root()).as_posix(), "caption": caption})
        return result

    def delete_asset(self, asset_id: str, expected_revision=None) -> dict:
        with self._store.connect(write=True) as project_db, self._asset_db(write=True) as db:
            for row in project_db.execute("SELECT value FROM meta WHERE key LIKE 'storyboard:%'"):
                board = json.loads(row[0])
                references = [*board.get('objects', []), *[element for shot in board.get('shots', []) for element in shot.get('visual_elements', [])]]
                if any(element.get('asset_id') == asset_id for element in references):
                    raise ValueError('资产仍被分镜引用，请先解除绑定')
            revision = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
            if expected_revision is not None and revision != expected_revision:
                raise ContentConflictError("资产已改变，请刷新后重试")
            if not db.execute("SELECT 1 FROM assets WHERE id=?", (asset_id,)).fetchone():
                raise ContentGuardError("资产不存在")
            db.execute("DELETE FROM assets WHERE id=?", (asset_id,))
            revision += 1
            db.execute("UPDATE meta SET value=? WHERE key='revision'", (revision,))
            db.execute("INSERT INTO audit(revision,action,asset_id,summary) VALUES(?,?,?,?)",
                       (revision, "delete", asset_id, "删除资产设定"))
        return {"removed": asset_id, "revision": revision}
