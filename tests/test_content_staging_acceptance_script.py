"""Validate the future paid-QA harness locally without a model request."""
import importlib.util
import json
from pathlib import Path

from agent_fixtures import project as project


def test_non_extraction_minimal_fixture_and_validator_offline(project, monkeypatch, tmp_path):
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('content_acceptance', scripts / 'accept_content_staging.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = module.seed(project)
    assert fixture['submission']['status'] == 'staged'
    assert project.load_compile_settings() == fixture['initial_settings']
    accepted = project.patch_content_submission(fixture['submission']['submission_id'], 1,
        [{'op': 'set', 'path': ['settings', 'font_size_pt'], 'value': 14}], '离线测试验收器')
    (tmp_path / 'task.json').write_text(json.dumps({'steps': [
        {'tool': 'patch_content_submission', 'ok': True, 'result': accepted}]}), encoding='utf-8')
    report = {'audit_directory': str(tmp_path)}
    checks = module.validate(project, fixture, {}, [], report)
    assert all(checks.values()), checks
    assert 'synthetic' in report['content_staging_evidence']['fixture_origin']
