"""角色 wiki：一个角色一页，页面在图的旁边，不在图里。

圣经、形象设定、概念图/三视图/细节图/表情图/动作图都是**编辑素材**，不是原文披露：
它们没有引文、没有阅读位置，也不该混进只认证据的属性记录里。所以它们存在工程旁边的
一个目录里，按节点分开，一个角色一个 JSON，图片原样拷进来，工程删了素材也跟着走。

    <工程>_wiki/<节点编号>.json
    <工程>_wiki/<节点编号>/<形象编号>/<图片文件>

图里那部分（按时间点的状态）仍然从属性记录读——wiki 只是给它配一层「这个角色长什么样」。
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

# 图片的五种用途。名字是给人看的，键是存下来的。
KINDS = {
    "concept": "概念图",
    "turnaround": "三视图",
    "detail": "细节图",
    "expression": "表情图",
    "action": "动作图",
}
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _empty(node_id: str) -> dict:
    return {"node_id": node_id, "bible": "", "appearances": [], "updated_at": ""}


class WikiStore:
    """一个工程的 wiki 目录。没有缓存：读一次是一次的真实内容。"""

    def __init__(self, project: Path):
        self.project = Path(project)
        self.root = self.project.with_name(self.project.stem + "_wiki")

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
        data.setdefault("bible", "")
        appearances = data.get("appearances")
        data["appearances"] = appearances if isinstance(appearances, list) else []
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
        alive = {str(item.get("id") or "") for item in data.get("appearances") or []}
        keep = {
            str(image.get("file") or "").replace("\\", "/")
            for item in data.get("appearances") or []
            for image in item.get("images") or []
        }
        # 文件在形象的目录里，所以要看整棵树，不能只看第一层。
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            if str(path.relative_to(self.root)).replace("\\", "/") in keep:
                continue
            try:
                path.unlink()
            except OSError:
                pass
        # 形象已经删掉的空壳目录也不留；非空说明还有删不掉的文件，留着下次再说。
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

    def nodes_with_wiki(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(path.stem for path in self.root.glob("*.json"))

    # -- 角色圣经 ------------------------------------------------------

    def set_bible(self, node_id: str, text: str) -> dict:
        data = self.load(node_id)
        data["bible"] = str(text or "")
        return self._save(node_id, data)

    # -- 形象 ----------------------------------------------------------

    def _appearance(self, data: dict, appearance_id: str) -> dict:
        for item in data["appearances"]:
            if item.get("id") == appearance_id:
                return item
        raise ValueError(f"形象不存在：{appearance_id}")

    def add_appearance(
        self, node_id: str, name: str, note: str = "", event_id: str = ""
    ) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("形象需要一个名字")
        data = self.load(node_id)
        item = {
            "id": f"ap_{uuid4().hex[:8]}",
            "name": name,
            "note": str(note or ""),
            # 这个形象从哪个时间点起成立；空 = 不指定。
            "event_id": str(event_id or ""),
            "images": [],
            "created_at": _now(),
        }
        data["appearances"].append(item)
        return self._save(node_id, data)

    def update_appearance(
        self,
        node_id: str,
        appearance_id: str,
        *,
        name: str | None = None,
        note: str | None = None,
        event_id: str | None = None,
    ) -> dict:
        data = self.load(node_id)
        item = self._appearance(data, appearance_id)
        if name is not None:
            if not name.strip():
                raise ValueError("形象名不能为空")
            item["name"] = name.strip()
        if note is not None:
            item["note"] = str(note)
        if event_id is not None:
            item["event_id"] = str(event_id)
        return self._save(node_id, data)

    def remove_appearance(self, node_id: str, appearance_id: str) -> dict:
        data = self.load(node_id)
        item = self._appearance(data, appearance_id)
        data["appearances"] = [
            other for other in data["appearances"] if other.get("id") != appearance_id
        ]
        self._save(node_id, data)
        # 素材跟着形象一起删：留在磁盘上的孤儿图片没人认领。
        folder = self.root / node_id / appearance_id
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
        self._sweep(node_id, data)
        return {"removed": appearance_id, "name": item.get("name")}

    # -- 图片 ----------------------------------------------------------

    def add_image(
        self,
        node_id: str,
        appearance_id: str,
        kind: str,
        source: Path,
        caption: str = "",
    ) -> dict:
        if kind not in KINDS:
            raise ValueError("图片用途只能是：" + "、".join(KINDS))
        source = Path(source)
        if not source.is_file():
            raise ValueError(f"找不到图片：{source}")
        if source.suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError("图片格式只收 " + "、".join(sorted(ALLOWED_SUFFIXES)))
        if source.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError(f"图片超过 {MAX_IMAGE_BYTES // 1024 // 1024}MB")

        data = self.load(node_id)
        item = self._appearance(data, appearance_id)
        folder = self.root / node_id / appearance_id
        folder.mkdir(parents=True, exist_ok=True)
        image_id = f"img_{uuid4().hex[:8]}"
        target = folder / f"{kind}-{image_id}{source.suffix.lower()}"
        shutil.copyfile(source, target)

        record = {
            "id": image_id,
            "kind": kind,
            "file": str(target.relative_to(self.root)).replace("\\", "/"),
            "caption": str(caption or ""),
            "added_at": _now(),
        }
        item.setdefault("images", []).append(record)
        self._save(node_id, data)
        return record | {"path": str(target)}

    def remove_image(self, node_id: str, appearance_id: str, image_id: str) -> dict:
        data = self.load(node_id)
        item = self._appearance(data, appearance_id)
        images = item.get("images") or []
        record = next((image for image in images if image.get("id") == image_id), None)
        if record is None:
            raise ValueError(f"图片不存在：{image_id}")
        item["images"] = [image for image in images if image.get("id") != image_id]
        self._save(node_id, data)
        target = self.root / str(record.get("file") or "")
        deleted = False
        if target.is_file():
            try:
                target.unlink()
                deleted = True
            except OSError:
                # 页面上还显示着这张图，Windows 不放文件。记录已经没了，
                # 下一次读这一页时 `_sweep` 会把它清掉。
                pass
        return {"removed": image_id, "file_deleted": deleted}

    # -- 给界面用的一份投影 --------------------------------------------

    def view(self, node_id: str) -> dict:
        """Wiki 内容 + 每种用途的中文名 + 图片的绝对路径。"""
        data = self.load(node_id)
        self._sweep(node_id, data)
        appearances = []
        for item in data["appearances"]:
            images = []
            for image in item.get("images") or []:
                relative = str(image.get("file") or "")
                images.append(
                    image
                    | {
                        "path": str(self.root / relative) if relative else "",
                        "kind_label": KINDS.get(image.get("kind"), image.get("kind")),
                    }
                )
            appearances.append(item | {"images": images})
        return {
            "node_id": node_id,
            "bible": data["bible"],
            "appearances": appearances,
            "kinds": [{"key": key, "label": label} for key, label in KINDS.items()],
            "root": str(self.root),
            "updated_at": data.get("updated_at", ""),
        }
