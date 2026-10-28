"""节点资料：一张张资料卡，跟着阅读位置出现和消失。

一份资料 = 一张卡：标题、详情、若干张带说明的图片，加上**生效时间**和**失效时间**
（各是一个场景或事件的编号，空表示不限制）。它和属性是同一类东西——
「从这个事件起成立，到那个事件起不成立」——只是没有原文引文，所以不进属性记录，
存在工程旁边：

    <工程>_material/<节点编号>.json
    <工程>_material/<节点编号>/<资料编号>/<图片文件>

因此浏览时和属性一样只显示**当前阅读位置上有效的**那几张；别的时间点上的只报个数，
免得让人以为资料丢了。生效/失效是编者明确指定的两个时刻，不是从内容里猜出来的。
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

from .order_ids import order_key

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _as_path(value: object) -> Path:
    """QML 的 FileDialog 给的是 `file:///C:/…`，Python 要的是路径。"""
    text = str(value or "")
    if text.startswith("file:"):
        path = unquote(urlparse(text).path)
        # Windows 上 URL 是 `/C:/…`，去掉开头那个斜杠才是盘符路径。
        if len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return Path(path)
    return Path(text)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _empty(node_id: str) -> dict:
    return {"node_id": node_id, "cards": [], "updated_at": ""}


def _moment(value: object) -> tuple[int, ...] | None:
    """生效/失效时刻：空串是「不限制」，否则必须是个场景或事件的编号。"""
    text = str(value or "").strip()
    if not text:
        return None
    return order_key(text)


def _key(card: dict) -> tuple:
    """先按生效时刻排（从头开始的最前），同一时刻按创建顺序。"""
    start = _moment(card.get("valid_from"))
    return (start if start is not None else (0,), str(card.get("created_at") or ""))


def holds_at(card: dict, cut: str = "") -> bool:
    """这张卡在阅读位置 `cut` 上算不算数。

    空 cut 表示「最新」：那是全书读完的位置，所以只在没有失效时刻的卡还算数。
    """
    start = _moment(card.get("valid_from"))
    end = _moment(card.get("valid_to"))
    if not cut:
        return end is None
    position = order_key(cut)
    if position is None:
        return end is None
    if start is not None and start > position:
        return False
    if end is not None and end <= position:
        return False
    return True


class MaterialStore:
    """一个工程的节点资料目录。没有缓存：读一次是一次的真实内容。"""

    def __init__(self, project: Path):
        from .project_package import layout

        self.project = Path(project)
        # 包和裸工程都问同一处：资料在包里就是 <包>/material。
        self.root = layout(self.project).material

    # -- 读写 ----------------------------------------------------------

    def path_for(self, node_id: str) -> Path:
        return self.root / f"{node_id}.json"

    def load(self, node_id: str) -> dict:
        path = self.path_for(node_id)
        if not path.is_file():
            return _empty(node_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return _empty(node_id)
        if not isinstance(data, dict):
            return _empty(node_id)
        data.setdefault("node_id", node_id)
        cards = data.get("cards")
        data["cards"] = cards if isinstance(cards, list) else []
        return data

    def _save(self, node_id: str, data: dict) -> dict:
        data["node_id"] = node_id
        data["updated_at"] = _now()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path_for(node_id).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return data

    def _sweep(self, node_id: str, data: dict) -> None:
        """删掉没人认领的素材。

        Windows 上删不掉正在显示的文件（页面的 Image 还开着它），所以删除只是尽力而为：
        记录先走，文件留到下一次读这一页时再清——那时没人引用它了。
        """
        folder = self.root / node_id
        if not folder.is_dir():
            return
        alive = {str(card.get("id") or "") for card in data.get("cards") or []}
        keep = {
            str(image.get("file") or "").replace("\\", "/")
            for card in data.get("cards") or []
            for image in card.get("images") or []
        }
        # 文件在资料的目录里，所以要看整棵树，不能只看第一层。
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            if str(path.relative_to(self.root)).replace("\\", "/") in keep:
                continue
            try:
                path.unlink()
            except OSError:
                pass
        # 资料已经删掉的空壳目录也不留；非空说明还有删不掉的文件，留着下次再说。
        for path in sorted(
            (item for item in folder.rglob("*") if item.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            if path.parent == folder and path.name in alive:
                continue
            try:
                path.rmdir()
            except OSError:
                pass

    def nodes_with_material(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(path.stem for path in self.root.glob("*.json"))

    # -- 资料卡 --------------------------------------------------------

    def _card(self, data: dict, card_id: str) -> dict:
        for card in data["cards"]:
            if card.get("id") == card_id:
                return card
        raise ValueError(f"资料不存在：{card_id}")

    def add_card(
        self,
        node_id: str,
        title: str,
        detail: str = "",
        valid_from: str = "",
        valid_to: str = "",
        images: list[dict] | None = None,
    ) -> dict:
        title = (title or "").strip()
        if not title:
            raise ValueError("资料需要一个标题")
        self._check_window(valid_from, valid_to)
        data = self.load(node_id)
        card = {
            "id": f"card_{uuid4().hex[:8]}",
            "title": title,
            "detail": str(detail or ""),
            # 生效/失效各是一个场景或事件的编号；空 = 不限制。
            "valid_from": str(valid_from or ""),
            "valid_to": str(valid_to or ""),
            "images": [],
            "created_at": _now(),
        }
        data["cards"].append(card)
        self._sync_images(node_id, card, images)
        self._save(node_id, data)
        return card

    @staticmethod
    def _check_window(valid_from: object, valid_to: object) -> None:
        start, end = _moment(valid_from), _moment(valid_to)
        if start is None or end is None:
            return
        if start >= end:
            raise ValueError("失效时间要在生效时间之后")

    def update_card(
        self,
        node_id: str,
        card_id: str,
        *,
        title: str | None = None,
        detail: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        images: list[dict] | None = None,
    ) -> dict:
        data = self.load(node_id)
        card = self._card(data, card_id)
        if title is not None:
            if not title.strip():
                raise ValueError("资料标题不能为空")
            card["title"] = title.strip()
        if detail is not None:
            card["detail"] = str(detail)
        if valid_from is not None:
            card["valid_from"] = str(valid_from)
        if valid_to is not None:
            card["valid_to"] = str(valid_to)
        self._check_window(card["valid_from"], card["valid_to"])
        self._sync_images(node_id, card, images)
        self._save(node_id, data)
        return card

    def remove_card(self, node_id: str, card_id: str) -> dict:
        data = self.load(node_id)
        card = self._card(data, card_id)
        data["cards"] = [other for other in data["cards"] if other.get("id") != card_id]
        self._save(node_id, data)
        # 图片跟着资料一起删：留在磁盘上的孤儿文件没人认领。
        folder = self.root / node_id / card_id
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
        self._sweep(node_id, data)
        return {"removed": card_id, "title": card.get("title")}

    # -- 图片 ----------------------------------------------------------

    def _copy_image(
        self, node_id: str, card_id: str, source: Path | str, caption: str = ""
    ) -> dict:
        """拷一张图进这张资料的目录，只返回记录——写不写回由调用方决定。"""
        source = _as_path(source)
        if not source.is_file():
            raise ValueError(f"找不到图片：{source}")
        if source.suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError("图片格式只收 " + "、".join(sorted(ALLOWED_SUFFIXES)))
        if source.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError(f"图片超过 {MAX_IMAGE_BYTES // 1024 // 1024}MB")
        folder = self.root / node_id / card_id
        folder.mkdir(parents=True, exist_ok=True)
        image_id = f"img_{uuid4().hex[:8]}"
        target = folder / f"{image_id}{source.suffix.lower()}"
        shutil.copyfile(source, target)
        return {
            "id": image_id,
            "file": str(target.relative_to(self.root)).replace("\\", "/"),
            "caption": str(caption or ""),
            "added_at": _now(),
        }

    def _drop_image_file(self, record: dict) -> bool:
        """删掉图片文件。页面上还开着它时 Windows 不放，所以只是尽力而为。"""
        target = self.root / str(record.get("file") or "")
        if not target.is_file():
            return False
        try:
            target.unlink()
            return True
        except OSError:
            return False

    def _sync_images(self, node_id: str, card: dict, entries: list[dict] | None) -> None:
        """把这张资料的图片改成 `entries` 说的样子。

        一条 entry 带 `id` 就是已有的那张（只改说明），带 `path` 是新加的源文件。
        列表里没有的旧图连文件一起删——编辑器里「移除」就是这个意思。
        """
        if entries is None:
            return
        existing = {str(image.get("id")): image for image in card.get("images") or []}
        kept: list[dict] = []
        for entry in entries:
            image_id = str(entry.get("id") or "")
            caption = str(entry.get("caption") or "")
            if image_id and image_id in existing:
                image = existing[image_id]
                image["caption"] = caption
                kept.append(image)
                continue
            source = str(entry.get("path") or "")
            if not source:
                continue
            # 不要先转成 Path：QML 给的是 `file:///C:/…`，Path 会把它拆坏。
            kept.append(self._copy_image(node_id, str(card["id"]), source, caption))
        for image_id, image in existing.items():
            if all(item.get("id") != image_id for item in kept):
                self._drop_image_file(image)
        card["images"] = kept

    def add_image(
        self, node_id: str, card_id: str, source: Path, caption: str = ""
    ) -> dict:
        data = self.load(node_id)
        card = self._card(data, card_id)
        record = self._copy_image(node_id, card_id, source, caption)
        card.setdefault("images", []).append(record)
        self._save(node_id, data)
        return record | {"path": str(self.root / str(record["file"]))}

    def update_image_caption(
        self, node_id: str, card_id: str, image_id: str, caption: str
    ) -> dict:
        data = self.load(node_id)
        card = self._card(data, card_id)
        image = next(
            (item for item in card.get("images") or [] if item.get("id") == image_id), None
        )
        if image is None:
            raise ValueError(f"图片不存在：{image_id}")
        image["caption"] = str(caption or "")
        self._save(node_id, data)
        return {"id": image_id, "caption": image["caption"]}

    def remove_image(self, node_id: str, card_id: str, image_id: str) -> dict:
        data = self.load(node_id)
        card = self._card(data, card_id)
        images = card.get("images") or []
        record = next((image for image in images if image.get("id") == image_id), None)
        if record is None:
            raise ValueError(f"图片不存在：{image_id}")
        card["images"] = [image for image in images if image.get("id") != image_id]
        self._save(node_id, data)
        # 页面上还显示着这张图时删不掉文件，记录已经没了，`_sweep` 下次再清。
        return {"removed": image_id, "file_deleted": self._drop_image_file(record)}

    # -- 给界面用的一份投影 --------------------------------------------

    def view(self, node_id: str, cut: str = "") -> dict:
        """这个阅读位置上有效的资料卡；别的时间点的只报个数。"""
        data = self.load(node_id)
        self._sweep(node_id, data)
        shown: list[dict] = []
        hidden = 0
        for card in sorted(data["cards"], key=_key):
            if not holds_at(card, cut):
                hidden += 1
                continue
            images = []
            for image in card.get("images") or []:
                relative = str(image.get("file") or "")
                images.append(image | {"path": str(self.root / relative) if relative else ""})
            shown.append(card | {"images": images})
        return {
            "node_id": node_id,
            "cut": cut,
            "cards": shown,
            "hidden_count": hidden,
            "root": str(self.root),
            "updated_at": data.get("updated_at", ""),
        }
