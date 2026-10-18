"""Packaged public-domain source texts, independent of UI and test artefacts."""

import hashlib
import json
from importlib.resources import files


def catalog() -> list[dict]:
    return json.loads(files("narrative").joinpath("texts", "catalog.json").read_text("utf-8"))


def load(text_id: str) -> tuple[dict, str]:
    entry = next((item for item in catalog() if item["id"] == text_id), None)
    if entry is None:
        raise ValueError("内置文本不存在，请从列表中选择")
    text = files("narrative").joinpath("texts", entry["file"]).read_bytes().decode("utf-8")
    if (
        len(text) != entry["characters"]
        or hashlib.sha256(text.encode("utf-8")).hexdigest() != entry["sha256"]
    ):
        raise ValueError("内置原文校验失败，请恢复安装包中的原文文件")
    return entry, text
