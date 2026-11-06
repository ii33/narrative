"""Unified structured creative agreements with project defaults and script overrides."""
from __future__ import annotations

import copy
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from .content_errors import (
    ContentConflictError,
    ContentGuardError,
    ContentValidationError,
    content_item,
)
from .graph_store import GraphStore

_KEY = 'project_style_v1'
FIELD_LABELS = {'tone': '整体基调', 'audiovisual_style': '视听风格', 'dialogue_style': '对白风格',
                'rhythm': '叙事节奏与镜头倾向', 'audience': '受众', 'adaptation_principles': '叙事与改编原则',
                'format_length': '剧本形式与篇幅', 'production_constraints': '制作约束', 'other': '其他要求'}


class CreativeAgreementFields(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    tone: str = ''
    audiovisual_style: str = ''
    dialogue_style: str = ''
    rhythm: str = ''
    audience: str = ''
    adaptation_principles: str = ''
    format_length: str = ''
    production_constraints: str = ''
    other: str = ''


def normalize_overrides(value):
    if not isinstance(value, dict) or set(value) - set(FIELD_LABELS) or any(v is not None and not isinstance(v, str) for v in value.values()):
        raise ContentValidationError('创作约定覆盖只接受规定字段的文本；null或省略继承，空串明确清空')
    return {key: text for key, text in value.items() if text is not None}


def render_agreement(fields):
    return '\n'.join((text if key == 'other' else FIELD_LABELS[key] + '：' + text)
                     for key, text in fields.items() if text.strip())


def read_defaults(db):
    row = db.execute('SELECT value FROM meta WHERE key=?', (_KEY,)).fetchone()
    value = json.loads(row[0]) if row else {'revision': 0, 'text': ''}
    fields = value.get('defaults', {'other': value.get('text', '')})
    return {'revision': value['revision'], 'fields': CreativeAgreementFields.model_validate(fields).model_dump()}


def read_style(db):
    defaults = read_defaults(db)
    return {'revision': defaults['revision'], 'text': render_agreement(defaults['fields'])}


def legacy_overrides(screenplay):
    explicit = screenplay.get('creative_agreement_overrides')
    if explicit is not None:
        return normalize_overrides(explicit)
    brief = screenplay.get('creative_brief') or {}
    known, other = {}, []
    for key, value in brief.items():
        if key in FIELD_LABELS and isinstance(value, str):
            known[key] = value
        else:
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            other.append(str(key) + '：' + text)
    if other:
        known['other'] = '\n'.join(([known['other']] if known.get('other') else []) + other)
    return known


def effective_fields(defaults, screenplay=None):
    fields = CreativeAgreementFields.model_validate(defaults).model_dump()
    return fields | (legacy_overrides(screenplay) if screenplay is not None else {})


def effective_style(source, screenplay):
    project = source.get('_project_style', {'revision': 0, 'text': ''})
    defaults = source.get('_creative_defaults', {'fields': {'other': project['text']}})['fields']
    fields = effective_fields(defaults, screenplay)
    if fields == CreativeAgreementFields.model_validate(defaults).model_dump():
        return copy.deepcopy(project)
    return {'revision': project['revision'], 'text': render_agreement(fields), 'fields': fields}


def agreement_context(source, screenplay):
    project = source.get('_project_style', {'revision': 0, 'text': ''})
    defaults = source.get('_creative_defaults', {'fields': {'other': project['text']}})['fields']
    overrides = legacy_overrides(screenplay)
    fields = effective_fields(defaults, screenplay)
    return {'fields': fields, 'sources': {key: 'script' if key in overrides else 'project' if fields[key].strip() else 'unrestricted' for key in fields}}


def _load_view(db, screenplay_id=None):
    defaults = read_defaults(db)
    screenplay = None
    if screenplay_id is not None:
        from .screenplay_store import load_state
        screenplay = load_state(db)['screenplays'].get(screenplay_id)
        if screenplay is None:
            raise ContentGuardError('剧本不存在，不能读取创作约定')
    return {'screenplay_id': screenplay_id, 'project_revision': defaults['revision'],
            'script_revision': screenplay['revision'] if screenplay else None,
            'project_defaults': defaults['fields'], 'script_overrides': legacy_overrides(screenplay) if screenplay else {},
            'effective': effective_fields(defaults['fields'], screenplay),
            'effective_sources': {key: 'script' if screenplay and key in legacy_overrides(screenplay) else 'project' if text.strip() else 'unrestricted' for key, text in effective_fields(defaults['fields'], screenplay).items()},
            'effective_style': effective_style({'_project_style': read_style(db), '_creative_defaults': defaults}, screenplay or {}),
            'title': screenplay['title'] if screenplay else None, 'language': screenplay['language'] if screenplay else None,
            'fields': [{'id': key, 'label': label} for key, label in FIELD_LABELS.items()]}


def _write_defaults(db, fields, revision):
    db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)', (_KEY, json.dumps(
        {'revision': revision, 'text': render_agreement(fields), 'defaults': fields}, ensure_ascii=False)))


class ProjectStyleOperationsMixin:
    _store: GraphStore

    def load_project_style(self):
        with self._store.connect() as db:
            return read_style(db)

    def save_project_style(self, text, expected_revision, reason='编辑项目风格约定'):
        with self._store.connect(write=True) as db:
            previous = read_defaults(db)
            if type(expected_revision) is not int or expected_revision != previous['revision']:
                raise ContentConflictError('项目风格约定已变化，请刷新后重试')
            if not isinstance(text, str):
                raise ContentValidationError('项目风格约定须为文本，可为空', problems=[{'code': 'CONTENT_VALIDATION', 'message': '风格须为文本', 'path': ['text']}])
            if not isinstance(reason, str) or not reason.strip():
                raise ContentValidationError('请说明修改风格约定的原因', problems=[{'code': 'CONTENT_VALIDATION', 'message': '请说明修改原因', 'path': ['reason']}])
            fields = previous['fields'] | {'other': text}
            _write_defaults(db, fields, previous['revision'] + 1)
            revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            db.execute('INSERT INTO actions(id,revision,action,reason,summary) VALUES (?,?,?,?,?)', (
                uuid4().hex, revision, 'save_project_style', reason,
                json.dumps({'style_revision': previous['revision'] + 1, 'text_length': len(text)}, ensure_ascii=False)))
            return read_style(db)

    def load_creative_agreement(self, screenplay_id=None):
        with self._store.connect() as db:
            if not db.in_transaction:
                db.execute('BEGIN')
            return _load_view(db, screenplay_id)

    def load_effective_creative_style(self, screenplay_id):
        with self._store.connect() as db:
            if not db.in_transaction:
                db.execute('BEGIN')
            from .screenplay_store import load_state
            screenplay = load_state(db)['screenplays'].get(screenplay_id)
            if screenplay is None:
                raise ContentGuardError('剧本不存在')
            return effective_style({'_project_style': read_style(db), '_creative_defaults': read_defaults(db)}, screenplay)

    def save_creative_agreement(self, project_defaults, expected_project_revision, reason, screenplay_id=None,
                                script_overrides=None, expected_script_revision=None, title=None, language=None):
        if screenplay_id is None and any(value is not None for value in [script_overrides, expected_script_revision, title, language]):
            raise ContentGuardError('剧本覆盖、名称与语言须指定剧本')
        with self._store.connect(write=True) as db:
            previous = _load_view(db, screenplay_id)
            if type(expected_project_revision) is not int or expected_project_revision != previous['project_revision']:
                raise ContentConflictError('工程创作约定已变化，请刷新后重试')
            if screenplay_id is not None and (type(expected_script_revision) is not int or expected_script_revision != previous['script_revision']):
                raise ContentConflictError('剧本已变化，请刷新后重试；工程默认与剧本覆盖均未保存')
            with content_item(['project_defaults'], project_defaults):
                fields = CreativeAgreementFields.model_validate(project_defaults).model_dump()
            if not isinstance(reason, str) or not reason.strip():
                raise ContentValidationError('保存创作约定须提供原因', problems=[{'code': 'CONTENT_VALIDATION', 'message': '须提供修改原因', 'path': ['reason']}])
            if screenplay_id is None and any(value is not None for value in [script_overrides, expected_script_revision, title, language]):
                raise ContentGuardError('剧本覆盖、名称与语言须指定剧本')
            with content_item(['script_overrides'], script_overrides):
                overrides = normalize_overrides(script_overrides) if script_overrides is not None else None
            for field_name, label, value in [('title', '剧本名称', title), ('language', '语言', language)]:
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    raise ContentValidationError(label + '不能为空', problems=[{'code': 'CONTENT_VALIDATION', 'message': label + '不能为空', 'path': [field_name]}])

            project_changed = fields != previous['project_defaults']
            script_changed = False
            if screenplay_id is not None:
                from .screenplay_models import Screenplay
                from .screenplay_store import load_state, now
                screenplay = load_state(db)['screenplays'][screenplay_id]
                patch = {}
                if overrides is not None and (overrides != screenplay.get('creative_agreement_overrides')):
                    patch['creative_agreement_overrides'] = overrides
                if title is not None and title.strip() != screenplay['title']:
                    patch['title'] = title.strip()
                if language is not None and language.strip() != screenplay['language']:
                    patch['language'] = language.strip()
                script_changed = bool(patch)
                if script_changed:
                    screenplay = Screenplay.model_validate(screenplay | patch | {'revision': screenplay['revision'] + 1, 'updated_at': now()}).model_dump(mode='json')
                    db.execute('UPDATE screenplays SET data=? WHERE id=?', (json.dumps(screenplay, ensure_ascii=False), screenplay_id))
            if project_changed:
                _write_defaults(db, fields, previous['project_revision'] + 1)
            revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            db.execute('INSERT INTO actions(id,revision,action,reason,summary) VALUES(?,?,?,?,?)', (
                uuid4().hex, revision, 'save_creative_agreement', reason, json.dumps({'screenplay_id': screenplay_id,
                'project_changed': project_changed, 'script_changed': script_changed,
                'project_revision': previous['project_revision'] + int(project_changed),
                'script_revision': previous['script_revision'] + int(script_changed) if screenplay_id else None}, ensure_ascii=False)))
            return _load_view(db, screenplay_id)
