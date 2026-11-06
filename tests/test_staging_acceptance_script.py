"""Exercise the paid-QA fixture offline; this is not real model acceptance."""
import importlib.util
import json
from pathlib import Path

from agent_fixtures import project as project


def test_minimal_real_qa_fixture_and_validator_without_model_request(project, monkeypatch, tmp_path):
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('staging_acceptance', scripts / 'accept_extraction_staging.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = module.seed(project)
    assert fixture['submission']['status'] == 'staged'
    assert not project.graph_snapshot()['nodes']
    assert '无需再读取' in module.prompt(fixture)
    result = project.patch_extraction_submission(fixture['submission']['submission_id'], 1,
        [{'op': 'set', 'section': 'nodes', 'index': 3, 'field': 'quotes', 'value': [module.TEXT]}],
        '离线校验验收器，不是模型输出')
    (tmp_path / 'task.json').write_text(json.dumps({'steps': [
        {'tool': 'patch_extraction_submission', 'ok': True, 'result': result}]}), encoding='utf-8')
    report = {'audit_directory': str(tmp_path)}
    checks = module.validate(project, fixture, {}, [], report)
    assert all(checks.values()), checks
    assert report['staging_evidence']['seed_origin'].startswith('synthetic')
