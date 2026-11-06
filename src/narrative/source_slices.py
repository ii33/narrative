"""Short, stable source range IDs; pattern creation is one validated transaction."""
from __future__ import annotations

import json

from .graph_store import GraphStore


class SourceSlices:
    def __init__(self, store: GraphStore):
        self.store = store

    @staticmethod
    def _key(source_id: str) -> str:
        return f"source_slices:{source_id}"

    def list(self, source_id: str) -> list[dict]:
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone() is None:
                raise ValueError("原文不存在")
            row = db.execute("SELECT value FROM meta WHERE key=?", (self._key(source_id),)).fetchone()
        return json.loads(row[0]) if row else []

    def get(self, source_id: str, slice_id: str) -> dict:
        source = self.store.source(source_id)
        value = self.store.meta(self._key(source_id))
        for item in json.loads(value) if value else []:
            if item["id"] == slice_id:
                return item | {"text": source["text"][item["start"]:item["end"]]}
        raise ValueError("原文切片不存在")

    def _save(self, db, source_id: str, text: str, slices: list[dict]) -> list[dict]:
        if not isinstance(slices, list) or not slices:
            raise ValueError("每批须提供至少一个切片")
        key = self._key(source_id)
        saved = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        rows = json.loads(saved[0]) if saved else []
        known = {(row["title"], row["start"], row["end"]) for row in rows}
        prefix = f"{source_id}-SL"
        counter_key = f"source_slice_sequence:{source_id}"
        counter = db.execute("SELECT value FROM meta WHERE key=?", (counter_key,)).fetchone()
        sequence = max([int(counter[0]) if counter else 0, *[
            int(row["id"][len(prefix):]) for row in rows
            if row["id"].startswith(prefix) and row["id"][len(prefix):].isdigit()]])
        for item in slices:
            if not isinstance(item, dict):
                raise ValueError("切片必须是对象")
            title, start, end = item.get("title"), item.get("start"), item.get("end")
            if not isinstance(title, str) or not title.strip():
                raise ValueError("切片标题不能为空")
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
                raise ValueError("切片须使用有效字符范围：0 <= start < end <= 原文长度")
            identity = title.strip(), start, end
            if identity in known:
                continue
            sequence += 1
            rows.append({"id": f"{prefix}{sequence}", "source_id": source_id,
                         "title": title.strip(), "start": start, "end": end, "length": end-start})
            known.add(identity)
        rows.sort(key=lambda row: (row["start"], row["end"], row["title"], row["id"]))
        db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (key, json.dumps(rows, ensure_ascii=False)))
        db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (counter_key, str(sequence)))
        return rows

    def create(self, source_id: str, slices: list[dict]) -> list[dict]:
        with self.store.connect(write=True) as db:
            source = db.execute("SELECT text FROM sources WHERE id=?", (source_id,)).fetchone()
            if source is None:
                raise ValueError("原文不存在")
            return self._save(db, source_id, source[0], slices)

    def preview_pattern(self, source_id: str, pattern: str, start_index: int = 0,
                        limit: int | None = None) -> dict:
        from .pattern_slices import plan
        return plan(source_id, self.store.source(source_id)["text"], pattern, start_index, limit)

    def create_pattern(self, source_id: str, pattern: str, start_index: int = 0,
                       limit: int | None = None) -> dict:
        from .pattern_slices import plan
        with self.store.connect(write=True) as db:
            source = db.execute("SELECT text FROM sources WHERE id=?", (source_id,)).fetchone()
            if source is None:
                raise ValueError("原文不存在")
            current = plan(source_id, source[0], pattern, start_index, limit)
            saved = self._save(db, source_id, source[0], current["slices"])
            wanted = {(row["title"],row["start"],row["end"]) for row in current["slices"]}
            selected = [row for row in saved if (row["title"],row["start"],row["end"]) in wanted]
        return current | {"status": "created", "slices": selected, "saved_count": len(saved)}
