"""Form routing binds repeated saves to one durable submission without refreshing CAS."""
import copy

import pytest
from agent_fixtures import project as project

from narrative.native_content import FormSubmissions


def test_form_patch_restore_isolated_new_sessions_and_stale_detach(project):
    forms = FormSubmissions()
    first, second = forms.token(), forms.token()
    original = project.load_compile_settings()
    bad = copy.deepcopy(original['settings'])
    bad['font_size_pt'] = -1
    a = forms.proxy(project, first).save_compile_settings(bad, original['revision'], '暂存')
    b = forms.proxy(project, second).save_compile_settings(bad, original['revision'], '独立新表单')
    assert a['submission_id'] != b['submission_id']
    # An external update must never be hidden by refreshing the protected revision.
    project.save_compile_settings(original['settings'], original['revision'], '其他编辑')
    with pytest.raises(ValueError):
        forms.proxy(project, first).save_compile_settings(original['settings'], original['revision'], '修补')
    assert forms.take_conflict(first)['submission_id'] == a['submission_id']
    latest = project.load_compile_settings()
    accepted = forms.proxy(project, first).save_compile_settings(latest['settings'], latest['revision'], '明确新提交')
    assert accepted['status'] == 'accepted'
    assert project.read_content_submission(a['submission_id'])['submission_id'] == a['submission_id']
    assert project.read_content_submission(b['submission_id'])['status'] == 'staged'


def test_form_patch_retains_id_and_restorable_bound_arguments(project):
    forms = FormSubmissions()
    token = forms.token()
    original = project.load_compile_settings()
    bad = copy.deepcopy(original['settings'])
    bad['font_size_pt'] = -1
    staged = forms.proxy(project, token).save_compile_settings(bad, original['revision'], '暂存')
    args = project.read_content_submission(staged['submission_id'], paths=[[]])['values'][0]['value']
    restored = forms.token()
    forms.restore(restored, staged, args)
    fixed = copy.deepcopy(bad)
    fixed['font_size_pt'] = 15
    result = forms.proxy(project, restored).save_compile_settings(fixed, original['revision'], '修补字号')
    assert result['submission_id'] == staged['submission_id']
    assert result['status'] == 'accepted'
    assert project.load_compile_settings()['settings']['font_size_pt'] == 15
