"""Independent visual assets, accessed exclusively through project Operations."""
from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .graph_store import GraphStore
from .node_material import ALLOWED_SUFFIXES, MAX_IMAGE_BYTES, _as_path, _position
from .readable_ids import allocate_id

CATEGORIES = {"appearance": "person", "item": "item", "location": "location", "material": None}
ASSET_PREFIXES = {"appearance": "appearance-", "item": "item-design-", "location": "location-design-", "material": "material-"}


class AssetOperationsMixin:
    _store: GraphStore

    def _asset_root(self) -> Path:
        from .project_package import layout
        return layout(self._store.path).material / "assets"

    @contextmanager
    def _asset_db(self, *, write=False):
        root = self._asset_root()
        root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(root / "assets.sqlite3", timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
                INSERT OR IGNORE INTO meta VALUES('revision',0);
                CREATE TABLE IF NOT EXISTS audit(
                    revision INTEGER PRIMARY KEY, action TEXT NOT NULL, asset_id TEXT NOT NULL,
                    summary TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            """)
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
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
            raise ValueError("资产不存在")
        return self._asset_card(json.loads(row[0])) | {"revision": revision}

    def list_assets(self, category: str, moment="", node_id="", domain="adaptation") -> dict:
        if category not in CATEGORIES:
            raise ValueError("未知资产类别")
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

    def save_asset(self, payload: dict, expected_revision=None) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("资产须为对象")
        with self._asset_db(write=True) as db:
            revision = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
            if expected_revision is not None and revision != expected_revision:
                raise ValueError("资产已改变，请刷新后重试")
            if payload.get("id"):
                asset_id = str(payload["id"])
            else:
                category = payload.get("category")
                if category not in CATEGORIES:
                    raise ValueError("未知资产类别")
                asset_id = allocate_id(db, "assets", ASSET_PREFIXES[category],
                                       (row[0] for row in db.execute("SELECT id FROM assets")))
            row = db.execute("SELECT data FROM assets WHERE id=?", (asset_id,)).fetchone()
            old = json.loads(row[0]) if row else {}
            if payload.get("id") and not row:
                raise ValueError("资产不存在")
            card = old | {key: value for key, value in payload.items() if key in
                          {"category", "node_id", "title", "detail", "domain", "valid_from", "valid_to", "prompt"}}
            card["id"] = asset_id
            for field in ("node_id", "title", "detail", "valid_from", "valid_to", "prompt"):
                card[field] = str(card.get(field) or "")
            card.setdefault("domain", "adaptation")
            category = card.get("category")
            if category not in CATEGORIES:
                raise ValueError("未知资产类别")
            card["title"] = card["title"].strip()
            if not card["title"]:
                raise ValueError("资产标题不能为空")
            snapshot = self._asset_snapshot(card["domain"])
            if category == "material":
                if card["valid_from"] or card["valid_to"] or card["node_id"]:
                    raise ValueError("材质不关联节点或时间点")
            else:
                node = next((node for node in snapshot["nodes"] if node["id"] == card["node_id"]
                             and not node.get("suppressed") and not node.get("retracted")), None)
                if node is None or node.get("node_type") != CATEGORIES[category]:
                    raise ValueError("资产须关联对应类别的有效节点")
            start = self._asset_position(card["valid_from"], snapshot, card["domain"])
            end = self._asset_position(card["valid_to"], snapshot, card["domain"])
            if start is not None and end is not None and start >= end:
                raise ValueError("失效时间须在生效时间之后")
            for field in ("images", "reference_images"):
                card[field] = self._asset_images(db, asset_id, payload.get(field), old.get(field, []))
            revision += 1
            db.execute("INSERT INTO assets VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                       (asset_id, json.dumps(card, ensure_ascii=False)))
            db.execute("UPDATE meta SET value=? WHERE key='revision'", (revision,))
            db.execute("INSERT INTO audit(revision,action,asset_id,summary) VALUES(?,?,?,?)",
                       (revision, "update" if row else "create", asset_id, card["title"]))
        return self._asset_card(card) | {"revision": revision}

    def _asset_images(self, db, asset_id, entries, old):
        if entries is None:
            return old
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise ValueError("图片须为对象数组")
        existing = {image["id"]: image for image in old}
        prepared = []
        # Validate every source before importing any files.
        for entry in entries:
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
            shutil.copyfile(source, target)
            result.append({"id": image_id, "file": target.relative_to(self._asset_root()).as_posix(), "caption": caption})
        return result

    def delete_asset(self, asset_id: str, expected_revision=None) -> dict:
        with self._asset_db(write=True) as db:
            revision = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
            if expected_revision is not None and revision != expected_revision:
                raise ValueError("资产已改变，请刷新后重试")
            if not db.execute("SELECT 1 FROM assets WHERE id=?", (asset_id,)).fetchone():
                raise ValueError("资产不存在")
            db.execute("DELETE FROM assets WHERE id=?", (asset_id,))
            revision += 1
            db.execute("UPDATE meta SET value=? WHERE key='revision'", (revision,))
            db.execute("INSERT INTO audit(revision,action,asset_id,summary) VALUES(?,?,?,?)",
                       (revision, "delete", asset_id, "删除资产设定"))
        return {"removed": asset_id, "revision": revision}
