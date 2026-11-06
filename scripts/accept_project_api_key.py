"""Two isolated requests check validity and the saved-key SDK path."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import accept_screenplay as qa
from accept_usage_statistics import seed as usage_seed
from accept_usage_statistics import validate as validate_usage

from narrative.harness import HarnessSettings, configured_key, harness_env

TASK = '这是工程密钥路径的隔离测试。不要调用工具，不要读取或修改工程，只回复“工程密钥测试完成”。'
_SECRET = ''


def seed(ops):
    fixture = usage_seed(ops)
    settings = HarnessSettings.model_validate(ops.harness_settings())
    # A unique unset environment name also excludes registry fallback without
    # modifying the user's registry or original provider profile.
    settings = settings.model_copy(update={'key_env': 'NARRATIVE_ISOLATED_SAVED_CREDENTIAL_ONLY'})
    os.environ.pop(settings.key_env, None)
    ops.save_provider_profile(settings.model_dump(), api_key=_SECRET)
    status = ops.provider_api_key_status(settings.model_dump())
    assert harness_env(ops)[1] == _SECRET
    fixture['credential_evidence'] = {'status': status, 'selected_environment_absent': settings.key_env not in os.environ,
        'saved_resolver_matches': ops._saved_provider_api_key(settings.model_dump()) == _SECRET,
        'credential_source': 'project DPAPI; unique unset key_env excludes process and registry fallback'}
    return fixture



def on_started(ops, fixture, report, directory, began):
    from narrative import credential_check
    captured = []
    original = credential_check._UpstreamHTTPSConnection
    class RecordingConnection(original):
        def getresponse(self):
            response = super().getresponse()
            read = response.read
            def record(*args, **kwargs):
                data = read(*args, **kwargs)
                captured.append(data)
                return data
            response.read = record
            return response
    credential_check._UpstreamHTTPSConnection = RecordingConnection
    try:
        check = credential_check.check_model_credential(ops, ops.harness_settings())
    finally:
        credential_check._UpstreamHTTPSConnection = original
    (directory / 'credential-check-raw.txt').write_text(b''.join(captured).decode('utf-8', errors='replace').replace(_SECRET, '[REDACTED]'), encoding='utf-8')
    (directory / 'credential-check.json').write_text(json.dumps(check, ensure_ascii=False, indent=2), encoding='utf-8')
    fixture['credential_evidence']['service_check'] = check
    report['credential_check'] = check
    if check['valid'] is not True:
        raise RuntimeError('Credential validation not completed: '+check['code'])


def validate(ops, fixture, screenplay, calls, report):
    checks = validate_usage(ops, fixture, screenplay, calls, report)
    directory = Path(report['audit_directory'])
    public = json.dumps(ops.load_project_settings(), ensure_ascii=False)
    audit_files = [p for p in directory.rglob('*') if p.is_file() and p.suffix in {'.json', '.jsonl', '.txt'}]
    checks.update(project_saved_key_selected=fixture['credential_evidence']['saved_resolver_matches'],
        selected_environment_absent=fixture['credential_evidence']['selected_environment_absent'],
        public_settings_no_secret=_SECRET not in public,
        audit_no_plaintext_secret=all(_SECRET not in p.read_text(encoding='utf-8') for p in audit_files))
    report['credential_evidence'] = fixture['credential_evidence']
    report['credential_check_requests'] = 1
    report['total_service_requests'] = 2
    probe_usage = fixture['credential_evidence']['service_check'].get('usage')
    report.setdefault('usage', []).append([probe_usage] if probe_usage else [])
    report['credential_check_usage_known'] = probe_usage is not None
    checks['credential_service_valid'] = fixture['credential_evidence']['service_check']['valid'] is True
    return checks


def main():
    global _SECRET
    _SECRET = configured_key('ARK_API_KEY')[0]
    if not _SECRET:
        raise ValueError('ARK_API_KEY unavailable; real acceptance not completed')
    output = Path(sys.argv[sys.argv.index('--output') + 1]).resolve()
    os.environ['NARRATIVE_PROVIDER_DB'] = str(output / 'isolated-providers.sqlite3')
    qa.seed, qa.TASK = seed, TASK
    qa.PROMPT_VERSION = 'project-api-key-real/2'
    qa.FIXTURE_ORIGIN = 'New isolated project/profile and DPAPI store; one credential check plus one no-tool SDK reply (two requests total), no retry.'
    return qa.main(validate=validate, required_tools=[], use_writing_target=False, prompt_factory=lambda _: TASK, on_started=on_started)


if __name__ == '__main__':
    raise SystemExit(main())
