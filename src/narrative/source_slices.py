"""Agent-authored source ranges, stored with the project and outside graph claims."""

from __future__ import annotations

import hashlib
import json

from .graph_store import GraphStore


class SourceSlices:
    def __init__(self, store: GraphStore):
        self.store = store

    @staticmethod
    def _key(source_id: str) -> str:
        return f"source_slices:{source_id}"

    def list(self, source_id: str) -> list[dict]:
        self.store.source(source_id)
        value = self.store.meta(self._key(source_id))
        return json.loads(value) if value else []

    def get(self, source_id: str, slice_id: str) -> dict:
        source = self.store.source(source_id)
        for item in self.list(source_id):
            if item["id"] == slice_id:
                return item | {"text": source["text"][item["start"]:item["end"]]}
        raise ValueError("原文切片不存在")

    def create(self, source_id: str, slices: list[dict]) -> list[dict]:
        if not isinstance(slices, list) or not slices or len(slices) > 1000:
            raise ValueError("每批须提供 1–1000 个切片")
        # Validate every item before writing, and serialize concurrent agent batches.
        with self.store.connect(write=True) as db:
            source = db.execute("SELECT text FROM sources WHERE id=?", (source_id,)).fetchone()
            if source is None:
                raise ValueError("原文不存在")
            text = source["text"]
            key = self._key(source_id)
            saved = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            rows = json.loads(saved["value"]) if saved else []
            known = {row["id"] for row in rows}
            for item in slices:
                if not isinstance(item, dict):
                    raise ValueError("切片必须是对象")
                title, start, end = item.get("title"), item.get("start"), item.get("end")
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("切片标题不能为空")
                if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
                    raise ValueError("切片须使用有效字符范围：0 <= start < end <= 原文长度")
                token = json.dumps([source_id, title.strip(), start, end], ensure_ascii=False)
                slice_id = hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
                if slice_id not in known:
                    rows.append({"id": slice_id, "source_id": source_id, "title": title.strip(),
                                 "start": start, "end": end, "length": end - start})
                    known.add(slice_id)
            rows.sort(key=lambda row: (row["start"], row["end"], row["title"], row["id"]))
            db.execute("INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, json.dumps(rows, ensure_ascii=False)))
        return rows
