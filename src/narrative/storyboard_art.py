"""Render grayscale blocking sketches and export an approved shot for image-to-image work."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

W, H = 960, 540


class SketchSubject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    kind: Literal["person", "box", "table", "door", "vehicle", "rock"] = "person"
    x: float = Field(default=0.48, ge=0, le=1)
    y: float = Field(default=0.86, ge=0, le=1)
    scale: float = Field(default=1, ge=0.15, le=2)
    pose: Literal["stand", "reach", "sit", "walk"] = "stand"
    facing: Literal["right", "left"] = "right"


class SketchLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    composition: Literal["single", "two_shot", "object", "empty"] = "single"
    framing: Literal["wide", "medium", "close"] = "medium"
    light: Literal["left", "right", "back", "overhead"] = "left"
    setting: Literal["interior", "exterior"] = "interior"
    subjects: list[SketchSubject] | None = Field(default=None, max_length=12)


def _subjects(plan):
    if plan.subjects is not None:
        return plan.subjects
    return {
        "single": [SketchSubject()],
        "two_shot": [
            SketchSubject(x=0.31),
            SketchSubject(x=0.7, y=0.82, scale=0.91, facing="left"),
        ],
        "object": [SketchSubject(kind="box", x=0.52, scale=1.2)],
        "empty": [],
    }[plan.composition]


def _backdrop(plan):
    horizon = 322
    base = Image.new("L", (W, H), 218)
    draw = ImageDraw.Draw(base)
    if plan.setting == "interior":
        draw.polygon([(0, 0), (155, 93), (155, horizon), (0, 470)], fill=174, outline=104)
        draw.polygon([(W, 0), (803, 93), (803, horizon), (W, 470)], fill=192, outline=108)
        draw.polygon([(155, 93), (803, 93), (803, horizon), (155, horizon)], fill=209, outline=112)
        draw.line([(0, 470), (155, horizon), (803, horizon), (W, 470)], fill=112, width=2)
        for x in range(-400, 1500, 200):
            draw.line([(480 + (x - 480) * 0.32, horizon + 2), (x, H)], fill=182)
    else:
        draw.polygon(
            [
                (0, 276),
                (97, 210),
                (231, 260),
                (352, 185),
                (480, 254),
                (621, 202),
                (770, 269),
                (888, 216),
                (W, 255),
                (W, H),
                (0, H),
            ],
            fill=169,
            outline=102,
        )
        draw.polygon(
            [(0, 331), (262, 301), (532, 318), (790, 289), (W, 310), (W, H), (0, H)], fill=194
        )
    light_center = {
        "left": (-0.05, 0.03),
        "right": (1.05, 0.03),
        "back": (0.50, 0.35),
        "overhead": (0.50, -0.25),
    }[plan.light]
    illumination = Image.new("L", (96, 54))
    illumination.putdata(
        [
            int(
                max(
                    0,
                    253
                    - 145
                    * ((x / 95 - light_center[0]) ** 2 + (y / 53 - light_center[1]) ** 2) ** 0.5,
                )
            )
            for y in range(54)
            for x in range(96)
        ]
    )
    return Image.blend(base, illumination.resize((W, H), Image.Resampling.BICUBIC), 0.37)


def _draw_subject(draw, subject, plan):
    k = {"wide": 0.67, "medium": 1.0, "close": 1.72}[plan.framing] * subject.scale
    x, y = W * subject.x, H * subject.y
    if plan.framing == "close":
        y += 126 * subject.scale
    direction = -1 if subject.facing == "left" else 1

    def point(a, b):
        return round(x + a * k * direction), round(y + b * k)

    def shape(coords, fill):
        points = [point(*pair) for pair in coords]
        draw.polygon(points, fill=fill)
        draw.line(points + [points[0]], fill=51, width=max(2, round(2 * k)), joint="curve")

    def ellipse(a, b, c, d, fill):
        left, top = point(a, b)
        right, bottom = point(c, d)
        draw.ellipse(
            (min(left, right), top, max(left, right), bottom),
            fill=fill,
            outline=52,
            width=max(2, round(2 * k)),
        )

    shade = 87 if plan.light == "back" else 155
    shadow = {"left": 110, "right": -110, "back": 25, "overhead": 8}[plan.light]
    draw.polygon(
        [
            (x - 27 * k, y - 4 * k),
            (x + 23 * k, y - 5 * k),
            (x + (shadow + 30) * k, y + 15 * k),
            (x + shadow * k, y + 18 * k),
        ],
        fill=112,
    )
    if subject.kind == "person":
        head_y = -148 if subject.pose == "sit" else -207
        hip = head_y + 124
        leg1, leg2 = {
            "stand": ((-16, -37), (16, -37)),
            "reach": ((-18, -38), (20, -38)),
            "walk": ((-30, -32), (30, -43)),
            "sit": ((42, -48), (13, -39)),
        }[subject.pose]
        for knee, foot, side in [(leg1, leg1[0] - 5, -11), (leg2, leg2[0] + 4, 11)]:
            shape(
                [
                    (side - 7, hip),
                    (side + 7, hip),
                    (knee[0] + 5, knee[1]),
                    (foot + 7, 0),
                    (foot - 4, 0),
                    (knee[0] - 5, knee[1]),
                ],
                shade,
            )
        shape(
            [
                (-17, head_y + 35),
                (14, head_y + 33),
                (23, head_y + 65),
                (14, hip),
                (-17, hip),
                (-25, head_y + 62),
            ],
            shade,
        )
        arm = [
            (11, head_y + 43),
            (21, head_y + 40),
            (43, head_y + 67),
            (77, head_y + 60),
            (80, head_y + 67),
            (41, head_y + 78),
            (19, head_y + 64),
        ]
        if subject.pose != "reach":
            arm = [
                (10, head_y + 43),
                (23, head_y + 43),
                (26, head_y + 88),
                (21, head_y + 112),
                (14, head_y + 109),
                (15, head_y + 85),
            ]
        shape(arm, min(shade + 20, 194))
        ellipse(-19, head_y - 8, 16, head_y + 31, min(shade + 27, 212))
        draw.line(
            [point(9, head_y + 2), point(17, head_y + 12), point(11, head_y + 14)], fill=49, width=2
        )
    elif subject.kind == "rock":
        shape([(-72, -15), (-46, -63), (3, -84), (49, -64), (69, -24), (52, -3), (-45, 0)], shade)
        shape([(3, -84), (49, -64), (69, -24), (27, -31)], min(shade + 39, 212))
    elif subject.kind == "vehicle":
        shape(
            [
                (-108, -15),
                (-103, -48),
                (-66, -54),
                (-34, -85),
                (43, -83),
                (67, -55),
                (107, -43),
                (108, -14),
            ],
            shade,
        )
        shape([(-55, -54), (-31, -77), (2, -77), (2, -54)], 206)
        for wheel in [-66, 64]:
            ellipse(wheel - 16, -31, wheel + 16, 0, 65)
    elif subject.kind == "door":
        shape([(-42, 0), (-42, -195), (42, -195), (42, 0)], shade)
        ellipse(24, -99, 30, -93, 211)
    else:
        height = 91 if subject.kind == "table" else 65
        shape(
            [(-62, -height), (-35, -height - 18), (72, -height - 18), (55, -height)],
            min(shade + 48, 222),
        )
        if subject.kind == "table":
            for leg in [-54, 43]:
                shape([(leg, -height), (leg + 8, -height), (leg + 8, 0), (leg, 0)], shade)
        else:
            shape([(-62, -height), (55, -height), (55, 0), (-62, 0)], shade)
            shape([(55, -height), (72, -height - 18), (72, -17), (55, 0)], shade - 25)


def render_sketch(spec: dict) -> Image.Image:
    plan = SketchLayout.model_validate(spec)
    image = _backdrop(plan)
    draw = ImageDraw.Draw(image)
    for subject in sorted(_subjects(plan), key=lambda s: s.y):
        _draw_subject(draw, subject, plan)
    return image


def render_camera_guide(layout, view, objects):
    """Approximate projected silhouettes; the image model supplies actual anatomy."""
    plan = SketchLayout.model_validate(layout)
    image = _backdrop(plan)
    draw = ImageDraw.Draw(image)
    by_id = {obj['id']: obj for obj in objects}
    for projection in sorted(view.get('projections', []), key=lambda row: row['depth'], reverse=True):
        if not projection['visible']:
            continue
        obj = by_id[projection['object_id']]
        x, y = projection['screen_x'] * W, projection['screen_y'] * H
        height = max(1, projection['size_ratio'] * H)
        left, right, top, bottom = x - height * .18, x + height * .18, y - height / 2, y + height / 2
        if obj['kind'] == 'person':
            draw.ellipse((x-height*.085, top, x+height*.085, top+height*.17), fill=155, outline=52, width=2)
            draw.polygon([(left, top+height*.2), (right, top+height*.2), (x+height*.1, bottom-height*.4),
                          (x-height*.1, bottom-height*.4)], fill=155, outline=52)
            draw.line((x-height*.05, bottom-height*.4, x-height*.08, bottom), fill=52, width=max(2, round(height*.035)))
            draw.line((x+height*.05, bottom-height*.4, x+height*.08, bottom), fill=52, width=max(2, round(height*.035)))
        else:
            draw.rectangle((left, top, right, bottom), fill=150, outline=52, width=2)
    return image


class StoryboardArtMixin:
    if TYPE_CHECKING:
        # Implemented by the composited storyboard and screenplay Operations.
        def _write_storyboard(self, unit_id, revision, changes, *, render_receipt=None, expected_style=None, expected_body_revision=None, sketch_receipt=None, sketch_basis=None, media_patch=None, media_audit=None) -> dict: ...
        def _asset_db(self, *, write=False) -> Any: ...
        def get_asset(self, asset_id) -> dict: ...
        def _asset_card(self, card) -> dict: ...
        def read_storyboard(self, unit_id) -> dict: ...
        def apply_storyboard_changes(self, unit_id, revision, changes) -> dict: ...
        def read_script_unit_context(self, unit_id, dependency_refs=None, target_block_ids=None) -> dict: ...

    def generate_storyboard_sketch(self, unit_id, shot_id, offset_ms, layout, expected_revision, prompt='', simple_color=False):
        """Generate a descriptive sketch from a private blocking guide; never fall back."""
        from .storyboard_style_provider import render_sketch_image
        from .storyboard_timing import integer

        if not isinstance(prompt, str) or type(simple_color) is not bool:
            raise ValueError('草图提示须为文本，简彩须为布尔值')
        if not isinstance(layout, dict):
            raise ValueError('构图参考须为对象')
        board = self.read_storyboard(unit_id)
        if integer(expected_revision, '修订号') != board['revision']:
            raise ValueError('分镜已修改，请刷新')
        if board['approved']:
            raise ValueError('请先解除定案再生成草图')
        shot = next((row for row in board['shots'] if row['id'] == shot_id), None)
        if not shot:
            raise ValueError('镜头不属于本场')
        if integer(offset_ms, '镜内位置') >= shot['duration_ms']:
            raise ValueError('关键帧位置须在镜头时长内')
        context = self.read_script_unit_context(unit_id)
        current_blocks = {row['id'] for row in context['unit']['blocks']}
        if any(identifier not in current_blocks for identifier in shot['source_block_ids']):
            raise ValueError('本镜正文依据已失效，请先重新选择正文块')
        basis, references = self._storyboard_sketch_basis(board, shot, offset_ms, context)
        asset_descriptions = [{key: card[key] for key in ('id', 'category', 'title', 'detail', 'prompt') if key in card}
                              for card in basis['assets']]
        text = (
            '绘制可用于影视分镜判断的描述型素描，不是几何示意图。'
            '以构图参考确认主体位置和景别，具体人物、物件和动作以正文与调度依据为准。'
            '清楚表现关键姿态、视线、手与道具接触、主体遮挡、主光方向、明暗形体及必要投影。'
            '不添加编辑色、编号、文字标签、运动箭头或界面。'
            + ('允许为叙事判断保留必要简洁色彩。' if simple_color else '使用黑白灰素描，以结构和明暗表达。')
            + '\n镜头稿与视觉元素：' + json.dumps({key: basis[key] for key in ('description', 'shot_language', 'visual_elements', 'sound_intents', 'sound_notes')}, ensure_ascii=False)
            + '\n本镜描述：' + shot['title'] + '\n本镜正文：' + json.dumps(basis['blocks'], ensure_ascii=False)
            + '\n创作约定：' + json.dumps(basis['creative_agreement'], ensure_ascii=False)
            + '\n当时共享调度：' + json.dumps(basis['objects'], ensure_ascii=False)
            + '\n命名时刻与当前动作时间：' + json.dumps({'action_ms': basis['action_ms'], 'moments': basis['named_moments']}, ensure_ascii=False)
            + '\n当前声音节拍：' + json.dumps(basis['active_sound_cues'], ensure_ascii=False)
            + '\n只绘制当前时刻：后续命名时刻不得提前显露，声音本身不意味着声源人物必须入画。'
            + '\n摄影机与投影（仅2.5D近似，不替代姿态、接触与复杂遮挡判断）：' + json.dumps(basis['camera_view'], ensure_ascii=False)
            + '\n关联资产：' + json.dumps(asset_descriptions, ensure_ascii=False)
            + '\n参考图顺序：首张是构图参考；其后图像为关联资产外观依据，不把参考图布局或文字画进成图。'
            + '\n本次绘制要求：' + prompt
        )
        with tempfile.TemporaryDirectory(prefix='storyboard-sketch-') as temp:
            guide = Path(temp) / 'composition-guide.png'
            output = Path(temp) / 'generated-sketch.png'
            if shot.get('camera', {}).get('keys') and basis['objects'] and not layout.get('subjects'):
                render_camera_guide(layout, basis['camera_view'], basis['objects']).save(guide)
            else:
                render_sketch(layout).save(guide)
            receipt = render_sketch_image(guide, text, output, reference_images=references)
            # Decode the returned file before any binding; missing/broken output is fatal.
            with Image.open(output) as image:
                image.load()
                if image.width < 1 or image.height < 1:
                    raise ValueError('生成草图不是有效图片')
            return self._write_storyboard(
                unit_id, expected_revision,
                [{'op': 'set_keyframe', 'shot_id': shot_id, 'offset_ms': offset_ms, 'sketch_path': str(output)}],
                sketch_receipt={'provider': receipt, 'prompt': text, 'user_prompt': prompt, 'layout': layout, 'simple_color': simple_color},
                sketch_basis=basis,
            )

    def _storyboard_sketch_basis(self, board, shot, offset_ms, context, asset_db=None):
        from .storyboard_audio import preview_audio
        from .storyboard_camera import camera_view
        from .storyboard_timing import object_states

        objects = object_states(board, shot.get('action_start_ms', 0) + offset_ms)
        assets = []
        references = []
        for identifier in sorted({row['asset_id'] for row in [*objects, *shot.get('visual_elements', [])] if row.get('asset_id')}):
            if asset_db is None:
                card = self.get_asset(identifier)
            else:
                row = asset_db.execute('SELECT data FROM assets WHERE id=?', (identifier,)).fetchone()
                if not row:
                    raise ValueError('生成依据资产已删除')
                card = self._asset_card(json.loads(row[0]))
            card = {key: value for key, value in card.items() if key != 'revision'}
            for field in ('images', 'reference_images'):
                for image in card.get(field, []):
                    path = Path(image['path'])
                    if not path.is_file():
                        raise ValueError('生成依据参考图缺失')
                    image['content_hash'] = hashlib.sha256(path.read_bytes()).hexdigest()
                    if path not in references:
                        references.append(path)
            assets.append(card)
        action_ms = shot.get('action_start_ms', 0) + offset_ms
        moments = [{key: marker[key] for key in ('id', 'name', 'time_ms')} for marker in board.get('markers', [])]
        cues = [{key: sound[key] for key in ('id', 'kind', 'label') if key in sound}
                for sound in preview_audio(board, shot, offset_ms)]
        return {'unit_id': board['unit_id'], 'shot_id': shot['id'], 'offset_ms': offset_ms,
                'sound_notes': shot.get('sound_notes', ''), 'description': shot.get('description', ''), 'shot_language': shot.get('shot_language', ''), 'visual_elements': shot.get('visual_elements', []), 'sound_intents': [row for row in board.get('sound_intents', []) if row['id'] in shot.get('sound_intent_ids', [])],
                'action_ms': action_ms, 'named_moments': moments, 'active_sound_cues': cues,
                'body_revision': context['unit']['body_revision'],
                'blocks': [row for row in context['unit']['blocks'] if row['id'] in shot['source_block_ids']],
                'creative_agreement': context.get('creative_agreement', {}), 'project_style': context['project_style'],
                'objects': objects, 'camera_view': camera_view(board, shot, offset_ms, objects), 'assets': assets}, references

    def export_storyboard_style_package(self, unit_id, shot_id, output_path):
        board = self.read_storyboard(unit_id)
        if not board["approved"]:
            raise ValueError("请先定案本场分镜")
        shot = next((s for s in board["shots"] if s["id"] == shot_id), None)
        if shot is None or not shot["keyframes"]:
            raise ValueError("请先为本镜绑定草图关键帧")
        context = self.read_script_unit_context(unit_id)
        paragraphs = [
            b["payload"].get("text", "")
            for b in context["unit"]["blocks"]
            if b["id"] in shot["source_block_ids"]
        ]
        frames = []
        for frame in shot["keyframes"]:
            sketch = Path(frame["sketch_path"])
            if not sketch.is_file():
                raise ValueError("草图缺失，请重新绑定")
            frames.append(
                (
                    sketch,
                    f"sketches/{frame['id']}{sketch.suffix.lower()}",
                    {"frame_id": frame["id"], "offset_ms": frame["offset_ms"]},
                )
            )
        style = context["project_style"]["text"]
        prompt = (
            f"镜头：{shot['title']}\n本镜正文：{' '.join(paragraphs)}\n项目风格：{style or '项目尚未指定'}\n"
            "以每张原草图为图像参考，保留宽高比、主体位置、动作轮廓、景别和主光方向。"
            "按项目风格逐帧出图。按清单中的关键帧编号与时间点回绑风格图。"
        )
        manifest = {
            "unit_id": unit_id,
            "shot_id": shot_id,
            "board_revision": board["revision"],
            "duration_ms": shot["duration_ms"],
            "keyframes": [m | {"sketch": name} for _, name, m in frames],
        }
        output = Path(output_path).resolve()
        if output.suffix.lower() != ".zip":
            raise ValueError("风格化制作包须为 ZIP 文件")
        # Exclusive creation preserves any existing export, including after a failure.
        with output.open("xb") as stream:
            try:
                with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as package:
                    for src, name, _ in frames:
                        package.write(src, name)
                    package.writestr(
                        "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
                    )
                    package.writestr("prompt.txt", prompt)
            except Exception:
                stream.close()
                output.unlink(missing_ok=True)
                raise
        return str(output)
