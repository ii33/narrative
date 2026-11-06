"""Scene-local storyboard editing through an atomic, revision-checked operation."""
from __future__ import annotations

import json
import shutil
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from .content_errors import (
    ContentConflictError,
    ContentGuardError,
    ContentValidationError,
    content_item,
    content_write,
)
from .graph_store import GraphStore, active_connection, on_rollback
from .project_package import layout
from .readable_ids import allocate_id
from .storyboard_art import StoryboardArtMixin
from .storyboard_audio import (
    AUDIO_FIELDS,
    apply_audio,
    audio_conflicts,
    audio_paths,
    validate_audio,
)
from .storyboard_camera import (
    CAMERA_FIELDS,
    apply_camera,
    resolve_markers,
    timing_conflicts,
    validate_camera,
)
from .storyboard_contract import ACTION_FIELDS, validate_action_shape
from .storyboard_panels import StoryboardPanelsMixin, apply_panel, panel_paths, validate_panels
from .storyboard_script import apply_intent, script_fields, validate_script
from .storyboard_timing import STAGING_FIELDS, apply_staging, preview, validate_timing
from .storyboard_video import StoryboardVideoMixin, media_paths, validate_media


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label}必须是大于等于 {minimum} 的整数")
    return value


def _image_path(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError("关键帧需要本地图片的完整路径")
    path = Path(value).resolve()
    if not path.is_file() or path.stat().st_size == 0 or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        raise ValueError("关键帧图片不存在或格式不受支持")
    try:
        with Image.open(path) as image:
            image.verify()
    except (OSError, SyntaxError, ValueError) as error:
        raise ValueError("关键帧文件无法作为图片读取") from error
    return str(path)


def _frame_paths(board):
    for shot in board["shots"]:
        for frame in shot["keyframes"]:
            for field in ("sketch_path", "style_path"):
                if frame.get(field):
                    yield frame, field


def _media_paths(board):
    yield from panel_paths(board)
    yield from _frame_paths(board)
    yield from audio_paths(board)
    yield from media_paths(board)


def _load(db, unit_id, root):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='script_units'").fetchone() or not db.execute(
        "SELECT 1 FROM script_units WHERE id=?", (unit_id,)
    ).fetchone():
        raise ContentGuardError("请先选择本工程的场次")
    row = db.execute("SELECT value FROM meta WHERE key=?", ("storyboard:" + unit_id,)).fetchone()
    board = json.loads(row[0]) if row else {"unit_id": unit_id, "revision": 0, "approved": False, "shots": [], "fps": 24, "objects": [], "markers": []}

    for frame, field in _media_paths(board):
        frame[field] = str(root / frame[field])
    return board


def _shot_fields(change, previous, block_ids):
    title = change.get("title", previous.get("title"))
    if not isinstance(title, str) or not title.strip():
        raise ValueError("请填写镜头内容")
    duration = _integer(change.get("duration_ms", previous.get("duration_ms", 3000)), "镜头时长", 1)
    refs = change.get("source_block_ids", previous.get("source_block_ids", []))
    if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in block_ids for ref in refs) or len(set(refs)) != len(refs):
        raise ValueError("正文依据须为本场有效且无重复的正文块")
    action_start = _integer(change.get("action_start_ms", previous.get("action_start_ms", 0)), "动作起点")
    return {**script_fields(change, previous), "title": title.strip(), "duration_ms": duration, "source_block_ids": refs, "action_start_ms": action_start,
            "action_start_marker_id": change.get("action_start_marker_id", previous.get("action_start_marker_id")),
            "duration_locked": change.get("duration_locked", previous.get("duration_locked", False))}


def _approve(board, value, block_ids):
    if type(value) is not bool:
        raise ValueError("定案状态须为布尔值")
    if value:
        if not board["shots"]:
            raise ValueError("空轨道无法定案")
        for shot in board["shots"]:
            if any(ref not in block_ids for ref in shot["source_block_ids"]):
                raise ValueError("请先重新定位失效的正文依据")
            if not shot["keyframes"] or shot["keyframes"][0]["offset_ms"] != 0:
                raise ValueError("定案前每个镜头都需要起始草图")
            for frame in shot["keyframes"]:
                _image_path(frame["sketch_path"])
    board["approved"] = value


def _set_frame(db, shot, change, approved):
    offset = _integer(change.get("offset_ms"), "镜内位置")
    if offset >= shot["duration_ms"]:
        raise ValueError("关键帧位置须在镜头时长内")
    frame = next((f for f in shot["keyframes"] if f["offset_ms"] == offset), None)
    if "style_path" in change:
        if "sketch_path" in change or not approved or not frame:
            raise ValueError("风格图须绑定定案方案中已有的草图")
        frame["style_path"] = _image_path(change["style_path"])
    else:
        if approved:
            raise ContentGuardError("请先解除定案再编辑草图")
        path = _image_path(change.get("sketch_path"))
        if frame is None:
            frame = {"id": allocate_id(db, "storyboard", "frame-"), "offset_ms": offset}
            shot["keyframes"].append(frame)
            shot["keyframes"].sort(key=lambda f: f["offset_ms"])
        frame.pop("sketch_source", None)
        frame.pop("sketch_basis_hash", None)
        frame.update(sketch_path=path, style_path="")


_FIELDS = ACTION_FIELDS

def _apply(db, board, change, block_ids):
    raw_op = change.get('op') if isinstance(change, dict) else None
    if board['approved'] and raw_op != 'set_approved' and not (raw_op == 'set_keyframe' and 'style_path' in change):
        raise ContentGuardError('请先解除定案再编辑镜头方案')
    collections = {'shot_id': board['shots'], 'object_id': board.get('objects', []), 'marker_id': board.get('markers', []),
                   'sound_id': [r for shot in board['shots'] for r in shot.get('sounds', [])],
                   'intent_id': board.get('sound_intents', []), 'panel_id': [r for shot in board['shots'] for r in shot.get('panels', [])]}
    for field, rows in collections.items():
        if isinstance(change, dict) and field in change and change[field] is not None and not any(r['id'] == change[field] for r in rows):
            raise ContentGuardError(f'{field}不属于当前场，未执行跨目标修改')
    op = validate_action_shape(change)
    if op == "set_approved":
        _approve(board, change.get("approved"), block_ids)
        return
    if op in {"create_sound_intent", "update_sound_intent", "delete_sound_intent"}:
        apply_intent(board, change, lambda prefix: allocate_id(db, "storyboard", prefix))
        return
    if op in AUDIO_FIELDS:
        apply_audio(board, change, lambda prefix: allocate_id(db, "storyboard", prefix))
        return
    if op in CAMERA_FIELDS:
        apply_camera(board, change, lambda prefix: allocate_id(db, "storyboard", prefix))
        return
    if op in STAGING_FIELDS:
        apply_staging(board, change, lambda prefix: allocate_id(db, "storyboard", prefix))
        return
    if op == 'use_panel_frame':
        shot = next((row for row in board['shots'] if row['id'] == change['shot_id']), None)
        panel = next((row for row in (shot or {}).get('panels', []) if row['id'] == change['panel_id']), None)
        candidate = next((row for row in (panel or {}).get('candidates', []) if row['id'] == (panel or {}).get('adopted_candidate_id')), None)
        if candidate is None or panel is None or shot is None:
            raise ValueError('请先采用此格候选图再用于编排')
        _set_frame(db, shot, {'offset_ms': panel['offset_ms'], 'sketch_path': candidate['path']}, board['approved'])
        panel['frame_id'] = next(row['id'] for row in shot['keyframes'] if row['offset_ms'] == panel['offset_ms'])
        return
    if op in {"create_panel", "update_panel", "delete_panel", "add_panel_candidate", "adopt_panel_candidate"}:
        apply_panel(board, change, lambda prefix: allocate_id(db, "storyboard", prefix), _image_path)
        return
    shots = board["shots"]
    if op == "create_shot":
        fields = _shot_fields(change, {}, block_ids)
        shots.append({"id": allocate_id(db, "storyboard", "shot-"), **fields, "keyframes": []})
        return
    shot = next((s for s in shots if s["id"] == change.get("shot_id")), None)
    if not shot:
        raise ContentGuardError("指定的镜头不属于本场")
    if op == "update_shot":
        shot.update(_shot_fields(change, shot, block_ids))
    elif op == "delete_shot":
        shots.remove(shot)
    elif op == "move_shot":
        index = _integer(change.get("index"), "镜头顺序")
        if index >= len(shots):
            raise ValueError("镜头顺序超出范围")
        shots.remove(shot)
        shots.insert(index, shot)
    elif op == "set_keyframe":
        _set_frame(db, shot, change, board["approved"])
    elif op in ("delete_keyframe", "move_keyframe"):
        frame = next((f for f in shot["keyframes"] if f["id"] == change.get("frame_id")), None)
        if frame is None:
            raise ContentGuardError("指定的关键帧不属于此镜头")
        if op == "move_keyframe":
            if frame.get("marker_id"):
                raise ValueError("此帧绑定命名时刻，请移动标记或先解除绑定")
            frame["offset_ms"] = _integer(change.get("offset_ms"), "关键帧位置")
            shot["keyframes"].sort(key=lambda row: row["offset_ms"])
        else:
            shot["keyframes"].remove(frame)


class StoryboardOperationsMixin(StoryboardPanelsMixin, StoryboardArtMixin, StoryboardVideoMixin):
    _store: GraphStore

    def export_storyboard_script(self, unit_id, format, path, illustrated=True, annotated=True):
        from .storyboard_manuscript import export_scene
        return export_scene(self, unit_id, format, path, illustrated, annotated)

    def read_storyboard(self, unit_id):
        with self._store.connect() as db:
            if not db.in_transaction:
                db.execute("BEGIN")
            return _load(db, unit_id, layout(self._store.path).material / "storyboard")

    def check_storyboard(self, unit_id):
        from .storyboard_review import check_storyboard
        return check_storyboard(self, self.read_storyboard(unit_id))

    def preview_storyboard(self, unit_id, time_ms):
        board = self.read_storyboard(unit_id)
        resolve_markers(board)
        validate_camera(board)
        validate_audio(board, check_media=False)
        validate_script(board, self.get_asset, {row['id']: row for row in self.read_script_unit_context(unit_id)['unit']['blocks']})
        validate_timing(board)
        return preview(board, time_ms)

    def preview_storyboard_changes(self, unit_id, revision, changes):
        """Simulate existing-object edits without allocating IDs or persisting anything."""
        original = self.read_storyboard(unit_id)
        if _integer(revision, '修订号') != original['revision']:
            raise ContentConflictError('分镜已修改，请刷新')
        if not isinstance(changes, list) or not changes:
            raise ValueError('请提供待预览变更')
        allowed = {'update_marker', 'delete_marker', 'update_shot', 'move_shot', 'move_keyframe', 'delete_keyframe',
                   'update_object', 'set_camera_overlays', 'move_action_keyframe', 'delete_action_keyframe', 'bind_keyframe_marker',
                   'bind_action_keyframe_marker', 'move_camera_keyframe', 'delete_camera_keyframe'}
        board = deepcopy(original)
        conflicts = []
        with self._store.connect() as db:
            blocks = {row[0] for row in db.execute('SELECT id FROM script_blocks WHERE unit_id=?', (unit_id,))}
        try:
            for change in changes:
                op = validate_action_shape(change)
                if op not in allowed:
                    raise ValueError('预览只支持已有对象的更新、移动、绑定与删除，不分配新编号')
                _apply(None, board, change, blocks)
            resolve_markers(board)
            conflicts.extend(timing_conflicts(board))
            conflicts.extend(audio_conflicts(board))
            if not conflicts:
                validate_camera(board)
                validate_audio(board, check_media=False)
                validate_script(board, self.get_asset, {row['id']: row for row in self.read_script_unit_context(unit_id)['unit']['blocks']})
                validate_timing(board)
        except (ValueError, TypeError) as error:
            conflicts.append({'code': 'invalid_storyboard_change', 'message': str(error)})
        old_shots = {row['id']: row for row in original['shots']}
        old_objects = {row['id']: row for row in original.get('objects', [])}
        affected_objects = [{'object_id': row['id'], 'name': row['name']} for row in board.get('objects', []) if row != old_objects.get(row['id'])]
        affected_shots = []
        affected_frames = []
        for index, shot in enumerate(board['shots']):
            old = old_shots[shot['id']]
            changed = shot != old or bool(affected_objects) or original['shots'][index]['id'] != shot['id']
            if changed:
                affected_shots.append({'shot_id': shot['id'], 'title': shot['title'], 'action_start_ms': shot['action_start_ms']})
                prior = {frame['id']: frame for frame in old['keyframes']}
                for frame in shot['keyframes']:
                    if frame != prior.get(frame['id']) or shot.get('camera') != old.get('camera') or affected_objects or shot['action_start_ms'] != old['action_start_ms']:
                        affected_frames.append({'shot_id': shot['id'], 'frame_id': frame['id'], 'offset_ms': frame['offset_ms']})
        return {'unit_id': unit_id, 'revision': revision, 'valid': not conflicts, 'conflicts': conflicts,
                'affected_shots': affected_shots, 'affected_frames': affected_frames, 'affected_objects': affected_objects,
                'preview': board if not conflicts else None}

    def load_storyboard_camera_presets(self):
        with self._store.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='storyboard_camera_presets'").fetchone()
        return json.loads(row[0]) if row else {'revision': 0, 'presets': []}

    @content_write
    def save_storyboard_camera_preset(self, name, settings, expected_revision, reason):
        from .storyboard_camera import EASINGS, POSE_FIELDS, PRESETS, validate_handheld
        from .storyboard_timing import number
        if not isinstance(reason, str) or not reason.strip():
            raise ContentGuardError('保存项目预设需要明确原因')
        if not isinstance(name, str) or not name.strip():
            raise ContentValidationError('保存项目预设需要名称', problems=[{'code': 'CONTENT_VALIDATION', 'message': '预设名称不能为空', 'path': ['name']}])
        if not isinstance(settings, dict) or set(settings) - {'preset', 'amount', 'start', 'end', 'easing', 'handheld'} or settings.get('preset') not in PRESETS:
            raise ValueError('项目预设仅保存preset/amount/start/end/easing/handheld，不保存场内目标编号')
        if settings.get('easing', 'linear') not in EASINGS:
            raise ValueError('摄影机速度曲线无效')
        validate_handheld(settings.get('handheld'))
        for side in ('start', 'end'):
            if side in settings:
                if not isinstance(settings[side], dict) or set(settings[side]) - POSE_FIELDS:
                    raise ValueError('预设摄影机姿态字段无效')
                for field, value in settings[side].items():
                    number(value, field)
                pose = settings[side]
                if pose.get('focus_distance', 1) <= 0:
                    raise ValueError('对焦距离须大于0')
                if not 1 <= pose.get('fov_deg', 50) < 179 or not -90 <= pose.get('pitch', 0) <= 90:
                    raise ValueError('预设视野或俯仰超出范围')
        if 'amount' in settings:
            number(settings['amount'], '运镜幅度')
        with self._store.connect(write=True) as db:
            row = db.execute("SELECT value FROM meta WHERE key='storyboard_camera_presets'").fetchone()
            data = json.loads(row[0]) if row else {'revision': 0, 'presets': []}
            if type(expected_revision) is not int or expected_revision != data['revision']:
                raise ContentConflictError('项目摄影机预设已修改，请刷新')
            preset: dict[str, Any] | None = next((row for row in data['presets'] if row['name'] == name.strip()), None)
            if preset is None:
                preset = {'id': allocate_id(db, 'storyboard', 'camera-preset-'), 'name': name.strip()}
                data['presets'].append(preset)
            preset['settings'] = deepcopy(settings)
            data['revision'] += 1
            db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('storyboard_camera_presets',?)", (json.dumps(data, ensure_ascii=False),))
            graph_revision = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
            db.execute('INSERT INTO actions(id,revision,action,reason,summary) VALUES(?,?,?,?,?)',
                       (uuid4().hex, graph_revision, 'save_storyboard_camera_preset', reason,
                        json.dumps({'preset_id': preset['id'], 'name': preset['name'], 'revision': data['revision']}, ensure_ascii=False)))
            return data

    def render_storyboard_style(self, unit_id, shot_id, frame_id, expected_revision):
        """Transform the approved sketch and bind only if its plan still matches."""
        from tempfile import TemporaryDirectory

        from .storyboard_style_provider import render_style_image

        board = self.read_storyboard(unit_id)
        if board["revision"] != _integer(expected_revision, "修订号") or not board["approved"]:
            raise ValueError("请刷新并确认分镜已经定案")
        shot = next((s for s in board["shots"] if s["id"] == shot_id), None)
        frame = next((f for f in (shot or {}).get("keyframes", []) if f["id"] == frame_id), None)
        if not shot or not frame:
            raise ValueError("关键帧不属于当前镜头")
        context = self.read_script_unit_context(unit_id)
        generation_basis, references = self._storyboard_sketch_basis(board, shot, frame['offset_ms'], context)
        paragraphs = [b["payload"].get("text", "") for b in context["unit"]["blocks"] if b["id"] in shot["source_block_ids"]]
        style = context["project_style"]["text"]
        if not style.strip():
            raise ValueError("请在创作约定中填写项目风格")
        prompt = (f"Transform this storyboard sketch into the final production image. "
                  f"Preserve viewpoint, framing, silhouette, blocking and main light direction. "
                  f"Remove sketch lines unless required by the art style. Shot: {shot['title']}. "
                  + 'Shot writing and visual usage: ' + json.dumps({key: generation_basis[key] for key in ('description', 'shot_language', 'visual_elements')}, ensure_ascii=False) + '. '
                  f"Screenplay: {' '.join(paragraphs)}. Art direction: {style}. "
                  'Asset appearance references: ' + json.dumps([{key: card[key] for key in ('id','category','title','detail','prompt') if key in card}
                                                               for card in generation_basis['assets']], ensure_ascii=False))
        with TemporaryDirectory(prefix="storyboard-style-") as directory:
            output = Path(directory) / "style.png"
            receipt = (render_style_image(Path(frame["sketch_path"]), prompt, output, reference_images=references)
                       if references else render_style_image(Path(frame["sketch_path"]), prompt, output))
            change = {"op": "set_keyframe", "shot_id": shot_id, "offset_ms": frame["offset_ms"], "style_path": str(output)}
            return self._write_storyboard(unit_id, expected_revision, [change], render_receipt=receipt,
                                          expected_style=style, expected_body_revision=context["unit"]["body_revision"],
                                          sketch_basis=generation_basis)

    @content_write
    def apply_storyboard_changes(self, unit_id, revision, changes):
        return self._write_storyboard(unit_id, revision, changes)

    def _write_storyboard(self, unit_id, revision, changes, *, render_receipt=None, expected_style=None, expected_body_revision=None, sketch_receipt=None, sketch_basis=None, media_patch=None, media_audit=None):
        if sketch_receipt is not None and sketch_basis is None:
            raise ValueError('生成草图须提供完整生成依据')
        if media_patch is not None and (not isinstance(media_patch, dict) or set(media_patch) != {'shot_id', 'values'}
                                       or not isinstance(media_patch['values'], dict)
                                       or set(media_patch['values']) - {'generations', 'selected_video', 'animation'}):
            raise ValueError('内部媒体写入字段无效')
        if media_audit is not None and not isinstance(media_audit, dict):
            raise ValueError('媒体审计须为对象')
        root = layout(self._store.path).material / "storyboard"
        with ExitStack() as rollback, ExitStack() as dependency_locks, self._store.connect(write=True) as db:
            board = _load(db, unit_id, root)
            if type(revision) is not int or revision < 0:
                raise ContentConflictError('修订号须为当前非负整数')
            if revision != board["revision"]:
                raise ContentConflictError("分镜已修改，请刷新后重试")
            if not isinstance(changes, list) or (not changes and media_patch is None):
                raise ValueError('请提供分镜变更')
            if render_receipt is not None:
                from .project_style import effective_fields, read_defaults, render_agreement
                source = db.execute(
                    "SELECT s.data,u.data FROM script_units u JOIN script_segments g ON g.id=u.segment_id "
                    "JOIN screenplays s ON s.id=g.screenplay_id WHERE u.id=?", (unit_id,)).fetchone()
                style = render_agreement(effective_fields(read_defaults(db)["fields"], json.loads(source[0])))
                if style != expected_style or json.loads(source[1])["body_revision"] != expected_body_revision:
                    raise ContentConflictError("生成期间正文或项目风格已变化，请重新核对")
            asset_lookup = self.get_asset
            asset_db = None
            needs_asset_lock = sketch_basis is not None or any(row.get('asset_id') for shot in board['shots'] for row in shot.get('visual_elements', [])) or any(change.get('asset_id') or any(row.get('asset_id') for row in change.get('visual_elements', []) if isinstance(row, dict)) for change in changes)
            if needs_asset_lock:
                # Serialize asset writers while checking actual selected cards/images and binding.
                asset_db = dependency_locks.enter_context(self._asset_db(write=True))
                def locked_asset(identifier):
                    row = asset_db.execute("SELECT data FROM assets WHERE id=?", (identifier,)).fetchone()
                    if row is None:
                        raise ValueError("资产不存在")
                    return json.loads(row[0])
                asset_lookup = locked_asset
            if sketch_basis is not None:
                assert asset_db is not None
                shot = next(row for row in board['shots'] if row['id'] == sketch_basis['shot_id'])
                current_basis, _ = self._storyboard_sketch_basis(
                    board, shot, sketch_basis['offset_ms'], self.read_script_unit_context(unit_id), asset_db)
                if current_basis != sketch_basis:
                    raise ContentConflictError('生成期间正文、创作约定或资产依据已变化，请重新生成')
            if media_patch is not None:
                media_shot = next((shot for shot in board['shots'] if shot['id'] == media_patch['shot_id']), None)
                if media_shot is None:
                    raise ValueError('媒体所属镜头不属于本场')
                media_shot.update(deepcopy(media_patch['values']))
            block_ids = {r[0] for r in db.execute("SELECT id FROM script_blocks WHERE unit_id=?", (unit_id,))}
            for index, change in enumerate(changes):
                with content_item(['changes', index], change):
                    _apply(db, board, change, block_ids)
            if sketch_receipt is not None:
                from .storyboard_review import sketch_signature
                assert sketch_basis is not None
                generated_shot = next(row for row in board['shots'] if row['id'] == sketch_basis['shot_id'])
                generated_frame = next(row for row in generated_shot['keyframes'] if row['offset_ms'] == sketch_basis['offset_ms'])
                generated_frame['sketch_source'] = {
                    'user_prompt': sketch_receipt.get('user_prompt', ''), 'layout': deepcopy(sketch_receipt.get('layout', {})),
                    'simple_color': sketch_receipt['simple_color'], 'provider': sketch_receipt['provider'].get('provider'),
                    'job_id': sketch_receipt['provider'].get('job_id'), 'body_revision': sketch_basis['body_revision'],
                    'asset_ids': [row['id'] for row in sketch_basis['assets']]}
                generated_frame['sketch_basis_hash'] = sketch_signature(sketch_basis)
            if media_audit is not None and media_audit.get('panel_generation'):
                generation = media_audit['panel_generation']
                generated_panel = next(panel for shot in board['shots'] for panel in shot.get('panels', []) if panel['id'] == generation['panel_id'])
                generated_panel['candidates'][-1]['generation_basis'] = deepcopy(generation['basis'])
                generated_panel['candidates'][-1]['generation_prompt'] = generation['prompt']
            resolve_markers(board)
            validate_camera(board)
            validate_audio(board)
            validate_script(board, asset_lookup, {row['id']: row for row in self.read_script_unit_context(unit_id)['unit']['blocks']})
            validate_timing(board, asset_lookup)
            validate_media(board)
            validate_panels(board)
            board["revision"] += 1
            for frame, field in _media_paths(board):
                source = Path(frame[field])
                copied = False
                if source.parent != root:
                    root.mkdir(parents=True, exist_ok=True)
                    target = root / (uuid4().hex + source.suffix.lower())
                    with target.open("xb") as output:
                        rollback.callback(target.unlink, missing_ok=True)
                        on_rollback(self._store.path, lambda path=target: path.unlink(missing_ok=True))
                        with source.open("rb") as original:
                            shutil.copyfileobj(original, output)
                    source = target
                    copied = True
                if copied and field == 'audio_path':
                    from .storyboard_audio import probe_audio
                    if probe_audio(str(source))['media_sha256'] != frame['media_sha256']:
                        raise ValueError('复制期间音频源文件已变化，请重新导入')
                if copied and field in ('path', 'output_path') and isinstance(frame.get('media'), dict):
                    import hashlib
                    with source.open('rb') as media_file:
                        digest = hashlib.file_digest(media_file, 'sha256').hexdigest()
                    if digest != frame['media'].get('media_sha256'):
                        raise ValueError('复制期间视频源文件已变化，请重新导入')
                frame[field] = source.name
            db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                       ("storyboard:" + unit_id, json.dumps(board, ensure_ascii=False)))
            graph_revision = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
            summary = {"unit_id": unit_id, "storyboard_revision": board["revision"], "operations": [c["op"] for c in changes]}
            if media_audit is not None:
                summary.update(deepcopy(media_audit))
            if render_receipt is not None:
                summary["style_render"] = render_receipt
            if sketch_receipt is not None:
                summary["sketch_generation"] = sketch_receipt
                summary["sketch_basis"] = sketch_basis
            db.execute("INSERT INTO actions(id,revision,action,reason,summary) VALUES(?,?,?,?,?)",
                       (uuid4().hex, graph_revision, "apply_storyboard_changes", "编辑分镜导演台", json.dumps(summary, ensure_ascii=False)))
            for frame, field in _media_paths(board):
                frame[field] = str(root / frame[field])
            if render_receipt is not None:
                board["style_render"] = {key: value for key, value in render_receipt.items() if not key.startswith("raw_")}
            if sketch_receipt is not None:
                assert sketch_basis is not None
                board["sketch_render"] = {key: sketch_receipt["provider"].get(key) for key in ("provider", "job_id", "usage", "output_file", "input_image_count")}
                rendered_shot = next(shot for shot in board['shots'] if shot['id'] == sketch_basis['shot_id'])
                board['sketch_render']['output_file'] = next(frame['sketch_path'] for frame in rendered_shot['keyframes']
                                                            if frame['offset_ms'] == sketch_basis['offset_ms'])
            if active_connection(self._store.path) is None:
                db.commit()
            rollback.pop_all()
            return board
