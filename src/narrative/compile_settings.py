"""Validated project-level screenplay typography, independent of creative style."""
from __future__ import annotations

import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .content_errors import ContentConflictError, ContentValidationError, content_item
from .graph_store import GraphStore

_KEY = 'screenplay_compile_settings_v1'


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class Margins(StrictModel):
    top: float = Field(default=25, ge=0, le=80)
    right: float = Field(default=23, ge=0, le=80)
    bottom: float = Field(default=25, ge=0, le=80)
    left: float = Field(default=23, ge=0, le=80)


class ParagraphStyle(StrictModel):
    alignment: Literal['left', 'center', 'right', 'justify'] = 'left'
    indent_left_mm: float = Field(default=0, ge=0, le=100)
    indent_right_mm: float = Field(default=0, ge=0, le=100)
    space_before_pt: float = Field(default=12, ge=0, le=100)
    space_after_pt: float = Field(default=12, ge=0, le=100)


class Paragraphs(StrictModel):
    scene: ParagraphStyle = Field(default_factory=ParagraphStyle)
    speaker: ParagraphStyle = Field(default_factory=lambda: ParagraphStyle(alignment='center', space_before_pt=0, space_after_pt=0))
    dialogue: ParagraphStyle = Field(default_factory=lambda: ParagraphStyle(indent_left_mm=35, indent_right_mm=23))
    action: ParagraphStyle = Field(default_factory=ParagraphStyle)
    scene_description: ParagraphStyle = Field(default_factory=ParagraphStyle)


class CompileSettings(StrictModel):
    font_family: str = Field(default='SimSun', min_length=1, max_length=200, pattern=r'^[^<>\x00-\x1f;{}]+$')
    font_size_pt: float = Field(default=12, ge=6, le=40)
    line_height: float = Field(default=1.65, ge=1, le=4)
    page_margins_mm: Margins = Field(default_factory=Margins)
    paragraphs: Paragraphs = Field(default_factory=Paragraphs)

    @model_validator(mode='after')
    def usable_page(self):
        width = 210 - self.page_margins_mm.left - self.page_margins_mm.right
        if width < 40 or 297 - self.page_margins_mm.top - self.page_margins_mm.bottom < 40:
            raise ContentValidationError('页边距须保留至少40mm正文区域')
        if any(getattr(self.paragraphs, key).indent_left_mm + getattr(self.paragraphs, key).indent_right_mm > width - 20 for key in ['scene', 'speaker', 'dialogue', 'action', 'scene_description']):
            raise ContentValidationError('段落缩进须保留至少20mm文字区域')
        return self


def read_compile_settings(db):
    row = db.execute('SELECT value FROM meta WHERE key=?', (_KEY,)).fetchone()
    stored = json.loads(row[0]) if row else {'revision': 0, 'settings': {}}
    return {'revision': stored['revision'], 'settings': CompileSettings.model_validate(stored['settings']).model_dump(),
            'defaults': CompileSettings().model_dump()}


class CompileSettingsOperationsMixin:
    _store: GraphStore

    def load_compile_settings(self):
        with self._store.connect() as db:
            return read_compile_settings(db)

    def save_compile_settings(self, settings, expected_revision, reason='编辑剧本编译排版'):
        with self._store.connect(write=True) as db:
            previous = read_compile_settings(db)
            if type(expected_revision) is not int or expected_revision != previous['revision']:
                raise ContentConflictError('编译排版设置已变化，请刷新后重试')
            with content_item(['settings'], settings):
                value = CompileSettings.model_validate(settings).model_dump()
            if not isinstance(reason, str) or not reason.strip():
                raise ContentValidationError('请说明修改排版的原因', problems=[{'code': 'CONTENT_VALIDATION', 'message': '请说明修改排版的原因', 'path': ['reason']}])
            result = {'revision': previous['revision'] + 1, 'settings': value}
            db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)', (_KEY, json.dumps(result, ensure_ascii=False)))
            revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            db.execute('INSERT INTO actions(id,revision,action,reason,summary) VALUES (?,?,?,?,?)',
                       (uuid4().hex, revision, 'save_compile_settings', reason, json.dumps({'compile_settings_revision': result['revision'], 'settings': value}, ensure_ascii=False)))
            return result | {'defaults': CompileSettings().model_dump()}


def default_settings():
    return CompileSettings().model_dump()
