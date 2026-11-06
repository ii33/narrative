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
import os
import shutil
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse
from uuid import uuid4

from .content_errors import ContentValidationError

if TYPE_CHECKING:
    from .graph_store import GraphStore

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
    return {"node_id": node_id, "cards": [], "updated_at": "",
            "next_card_number": 1, "next_image_number": 1}


def _serialized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        from .leases import ProjectLease
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            lease = ProjectLease(self.root / ".write.lock")
        except OSError as exc:
            raise ValueError("节点资料正在被其他操作写入，请稍后重试；未覆盖现有资料") from exc
        try:
            from .material_submission import validate_base
            validate_base(self, args[0] if args else kwargs['node_id'])
            return method(self, *args, **kwargs)
        finally:
            lease.release()
    return locked


def _position(moment: str, snapshot: dict) -> tuple | None:
    if not moment:
        return None
    from .browse_view import anchor_event
    from .temporal_state import boundary_at, position
    node = next((node for node in snapshot["nodes"] if node["id"] == moment and not node.get("retracted")), None)
    if node is None:
        raise ValueError(f"资料卡时间点不存在：{moment}")
    result = position(boundary_at(snapshot, anchor_event(snapshot, node)["id"]), snapshot)
    if result is None:
        raise ValueError(f"资料卡时间点没有原文披露坐标，无法排序：{moment}")
    return result


def holds_at(card: dict, cut: str, *, snapshot: dict) -> bool:
    start = _position(str(card.get("valid_from") or ""), snapshot)
    end = _position(str(card.get("valid_to") or ""), snapshot)
    if not cut:
        return end is None
    current = _position(cut, snapshot)
    return current is not None and (start is None or start <= current) and (end is None or current < end)


class MaterialStore:
    """一个工程的节点资料目录。没有缓存：读一次是一次的真实内容。"""

    def __init__(self, project: Path):
        from .project_package import layout

        self.project = Path(project)
        # 包和裸工程都问同一处：资料在包里就是 <包>/material。
        self.root = layout(self.project).material

    # -- 读写 ----------------------------------------------------------

    def path_for(self, node_id: str) -> Path:
        self._component(node_id)
        return self.root / f"{node_id}.json"

    @staticmethod
    def _component(identifier):
        if not isinstance(identifier, str) or not identifier or identifier in {".", ".."} or any(
            char in identifier for char in '<>:"/\\|?*\0') or identifier.rstrip(" .") != identifier:
            raise ValueError("资料节点或卡片编号不能包含路径字符")
        return identifier

    def load(self, node_id: str) -> dict:
        path = self.path_for(node_id)
        if not path.is_file():
            return _empty(node_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("节点资料文件损坏，拒绝以空资料覆盖") from exc
        if not isinstance(data, dict):
            raise ValueError("节点资料文件不是对象，拒绝覆盖")
        data.setdefault("node_id", node_id)
        cards = data.get("cards")
        if not isinstance(cards, list):
            raise ValueError("节点资料卡列表损坏，拒绝覆盖")
        return data

    def _save(self, node_id: str, data: dict, *, _business_result=None) -> dict:
        from .material_submission import commit_receipt
        commit_receipt(node_id, data, _business_result)
        data["node_id"] = node_id
        data["updated_at"] = _now()
        self.root.mkdir(parents=True, exist_ok=True)
        self._atomic_json(self.path_for(node_id), data)
        return data

    @_serialized
    def stage_content_submission(self, node_id, row, expected=None):
        data = self.load(node_id)
        prior = data.get('content_submissions', {}).get(row['submission_id'])
        if expected is not None and (prior is None or prior['submission_revision'] != expected or prior['submission_status'] != 'staged'):
            raise ValueError('资料内容暂存已变化或已完成')
        from copy import deepcopy
        data.setdefault('content_submissions', {})[row['submission_id']] = deepcopy(row)
        self._save(node_id, data)

    @staticmethod
    def _atomic_json(target: Path, data: dict) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(data, ensure_ascii=False, indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    def _next_number(self, node_id, data, key, prefix, existing):
        # Reserve independently before copying media/publishing a card. A failed
        # later JSON replacement burns the number rather than reusing an ID.
        path = self.root / ".sequences" / f"{node_id}.json"
        try:
            sequence = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except json.JSONDecodeError as exc:
            raise ValueError("资料编号计数文件损坏，拒绝分配重复编号") from exc
        if not isinstance(sequence, dict):
            raise ValueError("资料编号计数文件损坏，拒绝分配重复编号")
        numbers = (data.get(key, 1), sequence.get(key, 1))
        if any(type(value) is not int or value < 1 for value in numbers):
            raise ValueError("资料编号计数损坏，拒绝分配重复编号")
        number = max(numbers)
        known = [int(value[len(prefix):]) for value in existing
                 if isinstance(value, str) and value.startswith(prefix) and value[len(prefix):].isdigit()]
        number = max([number, *(value + 1 for value in known)])
        sequence[key] = data[key] = number + 1
        self._atomic_json(path, sequence)
        return number

    def _sweep(self, node_id: str, data: dict) -> None:
        """删掉没人认领的素材。

        Windows 上删不掉正在显示的文件（页面的 Image 还开着它），所以删除只是尽力而为：
        记录先走，文件留到下一次读这一页时再清——那时没人引用它了。
        """
        folder = self.root / node_id
        if not folder.resolve().is_relative_to(self.root.resolve()):
            return
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

    @_serialized
    def add_card(
        self,
        node_id: str,
        title: str,
        detail: str = "",
        valid_from: str = "",
        valid_to: str = "",
        images: list[dict] | None = None,
        *, snapshot: dict,
    ) -> dict:
        title = (title or "").strip()
        if not title:
            raise ContentValidationError("资料需要一个标题")
        self._check_window(valid_from, valid_to, snapshot)
        data = self.load(node_id)
        prefix = f"{node_id}-card-"
        number = self._next_number(node_id, data, "next_card_number", prefix, [row["id"] for row in data["cards"]])
        while (self.root / node_id / f"{prefix}{number}").exists():
            number = self._next_number(node_id, data, "next_card_number", prefix, [])
        card = {
            "id": f"{prefix}{number}",
            "title": title,
            "detail": str(detail or ""),
            # 生效/失效各是一个场景或事件的编号；空 = 不限制。
            "valid_from": str(valid_from or ""),
            "valid_to": str(valid_to or ""),
            "images": [],
            "created_at": _now(),
        }
        data["cards"].append(card)
        self._sync_images(node_id, card, images, data)
        self._save(node_id, data, _business_result=card)
        self._sweep(node_id, data)
        return card

    @staticmethod
    def _check_window(valid_from: object, valid_to: object, snapshot: dict) -> None:
        start = _position(str(valid_from or ""), snapshot)
        end = _position(str(valid_to or ""), snapshot)
        if start is None or end is None:
            return
        if start >= end:
            raise ContentValidationError("失效时间要在生效时间之后")

    @_serialized
    def update_card(
        self,
        node_id: str,
        card_id: str,
        *,
        snapshot: dict,
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
                raise ContentValidationError("资料标题不能为空")
            card["title"] = title.strip()
        if detail is not None:
            card["detail"] = str(detail)
        if valid_from is not None:
            card["valid_from"] = str(valid_from)
        if valid_to is not None:
            card["valid_to"] = str(valid_to)
        self._check_window(card["valid_from"], card["valid_to"], snapshot)
        self._sync_images(node_id, card, images, data)
        self._save(node_id, data, _business_result=card)
        self._sweep(node_id, data)
        return card

    @_serialized
    def remove_card(self, node_id: str, card_id: str) -> dict:
        self._component(card_id)
        data = self.load(node_id)
        card = self._card(data, card_id)
        data["cards"] = [other for other in data["cards"] if other.get("id") != card_id]
        self._save(node_id, data)
        # 图片跟着资料一起删：留在磁盘上的孤儿文件没人认领。
        folder = self.root / node_id / card_id
        if not folder.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("资料图片目录不能指向资料域外")
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
        self._sweep(node_id, data)
        return {"removed": card_id, "title": card.get("title")}

    # -- 图片 ----------------------------------------------------------

    def _copy_image(
        self, node_id: str, card_id: str, source: Path | str, caption: str = "", *, data: dict
    ) -> dict:
        """拷一张图进这张资料的目录，只返回记录——写不写回由调用方决定。"""
        source = _as_path(source)
        self._component(node_id)
        self._component(card_id)
        if not source.is_file():
            raise ValueError(f"找不到图片：{source}")
        if source.suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError("图片格式只收 " + "、".join(sorted(ALLOWED_SUFFIXES)))
        if source.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError(f"图片超过 {MAX_IMAGE_BYTES // 1024 // 1024}MB")
        folder = self.root / node_id / card_id
        if not folder.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("资料图片目录不能指向资料域外")
        folder.mkdir(parents=True, exist_ok=True)
        prefix = f"{card_id}-image-"
        number = self._next_number(node_id, data, "next_image_number", prefix,
            [image["id"] for card in data["cards"] for image in card.get("images", [])])
        while (folder / f"{prefix}{number}{source.suffix.lower()}").exists():
            number = self._next_number(node_id, data, "next_image_number", prefix, [])
        image_id = f"{prefix}{number}"
        target = folder / f"{image_id}{source.suffix.lower()}"
        with source.open("rb") as incoming, target.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        return {
            "id": image_id,
            "file": str(target.relative_to(self.root)).replace("\\", "/"),
            "caption": str(caption or ""),
            "added_at": _now(),
        }

    def _drop_image_file(self, record: dict) -> bool:
        """删掉图片文件。页面上还开着它时 Windows 不放，所以只是尽力而为。"""
        target = self.root / str(record.get("file") or "")
        if not target.resolve().is_relative_to(self.root.resolve()):
            return False
        if not target.is_file():
            return False
        try:
            target.unlink()
            return True
        except OSError:
            return False

    def _sync_images(self, node_id: str, card: dict, entries: list[dict] | None, data: dict) -> None:
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
            kept.append(self._copy_image(node_id, str(card["id"]), source, caption, data=data))
        card["images"] = kept

    @_serialized
    def add_image(
        self, node_id: str, card_id: str, source: Path, caption: str = ""
    ) -> dict:
        data = self.load(node_id)
        card = self._card(data, card_id)
        record = self._copy_image(node_id, card_id, source, caption, data=data)
        card.setdefault("images", []).append(record)
        self._save(node_id, data)
        self._sweep(node_id, data)
        return record | {"path": str(self.root / str(record["file"]))}

    @_serialized
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
        self._save(node_id, data, _business_result={"id": image_id, "caption": image["caption"]})
        return {"id": image_id, "caption": image["caption"]}

    @_serialized
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

    def view(self, node_id: str, cut: str = "", *, snapshot: dict) -> dict:
        """这个阅读位置上有效的资料卡；别的时间点的只报个数。"""
        data = self.load(node_id)
        shown: list[dict] = []
        hidden = 0
        diagnostics, unpositioned = [], []
        positioned = []
        for card in data["cards"]:
            try:
                start = _position(str(card.get("valid_from") or ""), snapshot)
                visible = holds_at(card, cut, snapshot=snapshot)
            except ValueError as exc:
                diagnostics.append({"code": "MATERIAL_POSITION", "message": str(exc), "card_id": card["id"]})
                unpositioned.append(card)
                continue
            positioned.append((start or (-1, -1), str(card.get("created_at") or ""), card, visible))
        for _start, _created, card, visible in sorted(positioned, key=lambda item: item[:2]):
            if not visible:
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
            "diagnostics": diagnostics,
            "unpositioned_cards": unpositioned,
            "root": str(self.root),
            "updated_at": data.get("updated_at", ""),
        }


class MaterialOperationsMixin:
    """Shared Operations boundary for original-node material cards."""

    _store: GraphStore

    def _node_material(self, node_id):
        method = "graph_snapshot"
        snapshot = getattr(self, method)()
        if not any(node["id"] == node_id and not node.get("retracted") for node in snapshot["nodes"]):
            raise ValueError("资料节点不存在")
        return MaterialStore(self._store.path), snapshot

    def material_view(self, node_id, cut=""):
        store, snapshot = self._node_material(node_id)
        return store.view(node_id, cut=cut, snapshot=snapshot)

    def material_add_card(self, node_id, title, detail="", valid_from="", valid_to="", images=None):
        store, snapshot = self._node_material(node_id)
        return store.add_card(node_id, title, detail, valid_from, valid_to, images, snapshot=snapshot)

    def material_update_card(self, node_id, card_id, **changes):
        store, snapshot = self._node_material(node_id)
        return store.update_card(node_id, card_id, snapshot=snapshot, **changes)

    def material_remove_card(self, node_id, card_id):
        store, _snapshot = self._node_material(node_id)
        return store.remove_card(node_id, card_id)

    def material_add_image(self, node_id, card_id, source, caption=""):
        store, _snapshot = self._node_material(node_id)
        return store.add_image(node_id, card_id, source, caption)

    def material_update_image_caption(self, node_id, card_id, image_id, caption):
        store, _snapshot = self._node_material(node_id)
        return store.update_image_caption(node_id, card_id, image_id, caption)

    def material_remove_image(self, node_id, card_id, image_id):
        store, _snapshot = self._node_material(node_id)
        return store.remove_image(node_id, card_id, image_id)
