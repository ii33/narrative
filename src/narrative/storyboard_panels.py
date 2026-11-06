"""Independent shot panels; images are copied by the shared atomic board writer."""

import math
import tempfile
from copy import deepcopy
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


def panel_paths(board):
    for shot in board["shots"]:
        for panel in shot.get("panels", []):
            if panel.get("base", {}).get("path"):
                yield panel["base"], "path"
            for candidate in panel.get("candidates", []):
                yield candidate, "path"


def apply_panel(board, change, allocate, image_path):
    shot = next((row for row in board["shots"] if row["id"] == change["shot_id"]), None)
    if not shot:
        raise ValueError("画格镜头不属于本场")
    panels = shot.setdefault("panels", [])
    op = change["op"]
    panel = next((row for row in panels if row["id"] == change.get("panel_id")), None)
    if op == "create_panel":
        panel = {
            "id": allocate("panel-"),
            "intent": "",
            "offset_ms": 0,
            "frame_id": None,
            "base": {"kind": "blank"},
            "candidates": [],
            "adopted_candidate_id": None,
            "marks": [],
        }
        panels.append(panel)
    elif panel is None:
        raise ValueError("画格不属于此镜头")
    if op == "delete_panel":
        panels.remove(panel)
        return
    if op == "add_panel_candidate":
        panel["candidates"].append(
            {"id": allocate("panel-candidate-"), "path": image_path(change["image_path"])}
        )
    elif op == "adopt_panel_candidate":
        panel["adopted_candidate_id"] = change["candidate_id"]
    else:
        for field in ("intent", "offset_ms", "frame_id", "base", "marks"):
            if field in change:
                panel[field] = deepcopy(change[field])
        if "marks" in change and isinstance(panel["marks"], list):
            for mark in panel["marks"]:
                if isinstance(mark, dict) and not mark.get("id"):
                    mark["id"] = allocate("panel-mark-")
        base = panel["base"]
        if not isinstance(base, dict):
            raise ValueError("画格底图须为对象")
        if base.get("kind") == "image":
            base["path"] = image_path(base.get("path"))
        elif base.get("kind") == "panel":
            source = next(
                (row for row in panels if row["id"] == base.get("panel_id") and row is not panel),
                None,
            )
            if source is None:
                raise ValueError("底图画格不存在")
            # Snapshot the source, so subsequent edits never affect this panel.
            import base64
            import io

            output = io.BytesIO()
            render_panel(board, shot, source).save(output, format="PNG")
            panel["base"] = {
                "kind": "snapshot",
                "png": base64.b64encode(output.getvalue()).decode("ascii"),
            }


def validate_panels(board):
    for shot in board["shots"]:
        frames = {row["id"] for row in shot["keyframes"]}
        for panel in shot.get("panels", []):
            if (
                type(panel["offset_ms"]) is not int
                or not 0 <= panel["offset_ms"] < shot["duration_ms"]
            ):
                raise ValueError("画格位置须在镜头时长内")
            if (
                not isinstance(panel["intent"], str)
                or panel["frame_id"] is not None
                and panel["frame_id"] not in frames
            ):
                raise ValueError("画格意图或绑定帧无效")
            if not isinstance(panel["base"], dict):
                raise ValueError("画格底图须为对象")
            if panel["base"].get("kind") not in ("blank", "image", "camera", "snapshot"):
                raise ValueError("画格底图类型无效")
            if panel["base"]["kind"] == "snapshot":
                import base64
                import io

                try:
                    with Image.open(
                        io.BytesIO(base64.b64decode(panel["base"].get("png", ""), validate=True))
                    ) as image:
                        image.verify()
                except Exception as error:
                    raise ValueError("底图快照无效") from error
            if panel.get("adopted_candidate_id") is not None and panel[
                "adopted_candidate_id"
            ] not in {row["id"] for row in panel["candidates"]}:
                raise ValueError("采用的候选图不属于此格")
            if not isinstance(panel["marks"], list):
                raise ValueError("画格标注须为列表")
            if len({mark.get("id") for mark in panel["marks"] if isinstance(mark, dict)}) != len(
                panel["marks"]
            ):
                raise ValueError("画格标注编号须唯一")
            for mark in panel["marks"]:
                if (
                    not isinstance(mark, dict)
                    or set(mark) - {"id", "kind", "points", "text"}
                    or mark.get("kind") not in ("stroke", "arrow", "text")
                ):
                    raise ValueError("画格标注结构无效")
                if not isinstance(mark.get("text", ""), str):
                    raise ValueError("文字标注须为文本")
                points = mark.get("points", [])
                if (
                    not isinstance(points, list)
                    or not points
                    or any(
                        not isinstance(point, list)
                        or len(point) != 2
                        or any(
                            type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
                            for v in point
                        )
                        for point in points
                    )
                ):
                    raise ValueError("标注坐标须归一化到0至1")


def panel_generation_basis(basis, shot, panel):
    """Visible scene state, excluding annotation and unchanged held-time metadata."""
    import hashlib

    current = deepcopy(basis)
    for key in (
        "body_revision",
        "offset_ms",
        "action_ms",
        "named_moments",
        "active_sound_cues",
        "sound_notes",
        "sound_intents",
    ):
        current.pop(key, None)
    for key in ("offset_ms", "action_ms"):
        current.get("camera_view", {}).pop(key, None)
    base = deepcopy(panel.get("base", {"kind": "blank"}))
    if base.get("path"):
        base["sha256"] = hashlib.sha256(Path(base.pop("path")).read_bytes()).hexdigest()
    current.update(shot_title=shot["title"], panel_intent=panel["intent"], panel_base=base)
    return current


def render_panel(board, shot, panel, annotated=False, size=(960, 540)):
    candidate = next(
        (
            row
            for row in panel.get("candidates", [])
            if row["id"] == panel.get("adopted_candidate_id")
        ),
        None,
    )
    frame = next((row for row in shot["keyframes"] if row["id"] == panel.get("frame_id")), None)
    path = (
        candidate["path"]
        if candidate
        else panel.get("base", {}).get("path") or (frame or {}).get("sketch_path")
    )
    if path:
        with Image.open(path) as source:
            canvas = ImageOps.pad(source.convert("RGB"), size, color="white")
    elif panel.get("base", {}).get("kind") == "snapshot":
        import base64
        import io

        with Image.open(io.BytesIO(base64.b64decode(panel["base"]["png"]))) as source:
            canvas = ImageOps.pad(source.convert("RGB"), size, color="white")
    elif panel.get("base", {}).get("kind") == "camera":
        from .storyboard_art import render_camera_guide
        from .storyboard_camera import camera_view
        from .storyboard_timing import object_states

        states = object_states(board, shot.get("action_start_ms", 0) + panel["offset_ms"])
        canvas = (
            render_camera_guide({}, camera_view(board, shot, panel["offset_ms"], states), states)
            .convert("RGB")
            .resize(size)
        )
    else:
        canvas = Image.new("RGB", size, "white")
    if annotated:
        draw = ImageDraw.Draw(canvas)
        for mark in panel.get("marks", []):
            points = [(x * size[0], y * size[1]) for x, y in mark["points"]]
            if mark["kind"] == "text":
                draw.text(points[0], mark.get("text", ""), fill="red", font=_mark_font())
            elif len(points) > 1:
                draw.line(points, fill="red", width=3)
                if mark["kind"] == "arrow":
                    x, y = points[-1]
                    px, py = points[-2]
                    angle = math.atan2(y - py, x - px)
                    draw.polygon(
                        [
                            (x, y),
                            (x - 14 * math.cos(angle - 0.5), y - 14 * math.sin(angle - 0.5)),
                            (x - 14 * math.cos(angle + 0.5), y - 14 * math.sin(angle + 0.5)),
                        ],
                        fill="red",
                    )
    return canvas


def _mark_font():
    from PIL import ImageFont

    for path in (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(path).is_file():
            return ImageFont.truetype(path, 22)
    return ImageFont.load_default()


class StoryboardPanelsMixin:
    def generate_storyboard_panels(
        self, unit_id, shot_id, expected_revision, missing_only=True, prompt=""
    ):
        if type(missing_only) is not bool:
            raise ValueError("补缺选项须为布尔值")
        board = self.read_storyboard(unit_id)
        if board["revision"] != expected_revision:
            raise ValueError("分镜已修改，请刷新")
        shot = next((row for row in board["shots"] if row["id"] == shot_id), None)
        if shot is None:
            raise ValueError("镜头不存在")
        identifiers = [
            panel["id"]
            for panel in shot.get("panels", [])
            if not missing_only
            or (
                not panel.get("candidates")
                and panel.get("base", {}).get("kind") not in ("image", "snapshot")
                and not panel.get("frame_id")
            )
        ]
        for identifier in identifiers:
            board = self.generate_storyboard_panel(
                unit_id, shot_id, identifier, board["revision"], prompt
            )
        return board

    def export_storyboard_panel(self, unit_id, shot_id, panel_id, output_path, annotated=False):
        if (
            type(annotated) is not bool
            or not Path(output_path).is_absolute()
            or Path(output_path).suffix.lower() != ".png"
        ):
            raise ValueError("画格导出需要完整PNG路径和布尔标注选项")
        board = self.read_storyboard(unit_id)
        shot = next((row for row in board["shots"] if row["id"] == shot_id), None)
        panel = next((row for row in (shot or {}).get("panels", []) if row["id"] == panel_id), None)
        if panel is None:
            raise ValueError("画格不存在")
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        image = render_panel(board, shot, panel, annotated)
        with output.open("xb") as destination:
            image.save(destination, format="PNG")
        return {"output_path": str(output), "panel_id": panel_id, "annotated": annotated}

    def generate_storyboard_panel(self, unit_id, shot_id, panel_id, expected_revision, prompt=""):
        from .storyboard_style_provider import render_sketch_image

        if not isinstance(prompt, str) or type(expected_revision) is not int:
            raise ValueError("画格提示须为文本，修订号须为整数")
        board = self.read_storyboard(unit_id)
        if board["revision"] != expected_revision or board["approved"]:
            raise ValueError("请刷新并解除定案后绘制画格")
        shot = next((row for row in board["shots"] if row["id"] == shot_id), None)
        panel = next((row for row in (shot or {}).get("panels", []) if row["id"] == panel_id), None)
        if panel is None:
            raise ValueError("画格不存在")
        basis, references = self._storyboard_sketch_basis(
            board, shot, panel["offset_ms"], self.read_script_unit_context(unit_id)
        )
        import json

        text = (
            "绘制影视分镜描述型黑白素描，不添加文字、箭头或界面。仅表现当前时刻。"
            + shot["title"]
            + "\n画格意图："
            + panel["intent"]
            + "\n正文与调度："
            + json.dumps(basis, ensure_ascii=False)
            + "\n要求："
            + prompt
        )
        with tempfile.TemporaryDirectory(prefix="storyboard-panel-") as temporary:
            guide = Path(temporary) / "guide.png"
            output = Path(temporary) / "output.png"
            render_panel(board, shot, panel).save(guide)
            receipt = render_sketch_image(guide, text, output, reference_images=references)
            return self._write_storyboard(
                unit_id,
                expected_revision,
                [
                    {
                        "op": "add_panel_candidate",
                        "shot_id": shot_id,
                        "panel_id": panel_id,
                        "image_path": str(output),
                    }
                ],
                sketch_basis=basis,
                media_audit={
                    "panel_generation": {
                        "panel_id": panel_id,
                        "provider": receipt,
                        "prompt": text,
                        "basis": panel_generation_basis(basis, shot, panel),
                    }
                },
            )
