"""Actual Windows DPAPI and project/profile-bound local credential storage."""
import json
import os

import pytest

from narrative.harness import HarnessSettings
from narrative.operations import Operations
from narrative.provider_credentials import _dpapi, credential_scope
from narrative.provider_store import ProviderStore


@pytest.fixture
def ops(tmp_path, monkeypatch):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'application' / 'providers.sqlite3'))
    return Operations.create_graph_project(tmp_path / 'credential.nproj')


def settings(ops, url='https://example.test/v1'):
    return {'extraction_granularity': 'fine', 'profile': HarnessSettings(provider='custom', base_url=url, model='local-test', key_env='NARRATIVE_LOCAL_CREDENTIAL_TEST_KEY').model_dump()}


@pytest.mark.skipif(os.name != 'nt', reason='DPAPI is Windows current-user encryption')
def test_real_dpapi_not_plaintext_and_entropy_binds_exact_scope():
    scope = ('project-one', 'custom', 'https://example.test/v1')
    key = b'qa-local-dpapi-private-value'
    encrypted = _dpapi(key, scope)
    assert key not in encrypted and encrypted != key
    assert _dpapi(encrypted, scope, decrypt=True) == key
    with pytest.raises(ValueError, match='无法解密'):
        _dpapi(encrypted, ('another-project', *scope[1:]), decrypt=True)


@pytest.mark.skipif(os.name != 'nt', reason='DPAPI is Windows current-user encryption')
def test_save_status_scope_clear_and_no_plaintext_in_project_or_profiles(ops, tmp_path, monkeypatch):
    key = 'qa-project-key-never-exported'
    profile = settings(ops)
    result = ops.save_project_settings(profile, api_key=key)
    assert result['api_key_status']['source'] == 'local' and result['api_key_status']['local_available']
    assert ops._saved_provider_api_key(profile['profile']) == key
    assert key not in json.dumps(result)
    assert ops.provider_api_key_status(profile['profile'] | {'base_url': 'HTTPS://EXAMPLE.TEST:443/v1/'})['local_available']
    assert not ops.provider_api_key_status(profile['profile'] | {'base_url': 'https://other.test/v1'})['local_available']
    ops.save_project_settings(profile, api_key='')
    assert ops._saved_provider_api_key(profile['profile']) == key
    provider = ProviderStore()
    for config_key in ('harness_settings', 'harness_profiles'):
        assert key not in (provider.get(ops.graph_project_info()['project_id'], config_key) or '')
    assert key.encode() not in provider.path.read_bytes()
    assert all(key.encode() not in path.read_bytes() for path in tmp_path.glob('credential*') if path.is_file())
    copy = Operations.duplicate_graph_project(tmp_path / 'credential.nproj', tmp_path / 'copy.nproj')
    assert not copy.provider_api_key_status(profile['profile'])['local_available']
    monkeypatch.setenv('NARRATIVE_LOCAL_CREDENTIAL_TEST_KEY', 'qa-environment-fallback')
    cleared = ops.save_project_settings(profile, clear_api_key=True)
    assert not cleared['api_key_status']['local_available'] and cleared['api_key_status']['source'] == 'environment'
    assert ops._saved_provider_api_key(profile['profile']) is None


@pytest.mark.skipif(os.name != 'nt', reason='DPAPI is Windows current-user encryption')
def test_invalid_profile_and_failed_storage_leave_all_settings_and_key_unchanged(ops, monkeypatch):
    from narrative import project_settings
    from narrative.provider_credentials import apply_credential as original_apply
    original = settings(ops)
    ops.save_project_settings(original, api_key='qa-first-key')
    before = ops.load_project_settings()
    with pytest.raises(ValueError):
        ops.save_project_settings(original | {'profile': original['profile'] | {'model': ''}}, api_key='qa-invalid-key')
    assert ops.load_project_settings() == before and ops._saved_provider_api_key(original['profile']) == 'qa-first-key'
    def fail_after_credential(db, project, profile, prepared, namespace='main'):
        original_apply(db, project, profile, prepared, namespace)
        raise OSError('injected transaction failure')
    monkeypatch.setattr('narrative.provider_credentials.apply_credential', fail_after_credential)
    with pytest.raises(OSError):
        project_settings.save(ops, original | {'extraction_granularity': 'standard', 'profile': original['profile'] | {'model': 'changed'}}, api_key='qa-second-key')
    assert ops.load_project_settings() == before
    assert ops._saved_provider_api_key(original['profile']) == 'qa-first-key'


def test_empty_and_conflicting_secret_controls_do_not_write_credentials(ops):
    profile = settings(ops)
    assert not ops.save_project_settings(profile, api_key='')['api_key_status']['local_available']
    with pytest.raises(ValueError, match='不能同时'):
        ops.save_project_settings(profile, api_key='qa-conflicting-key', clear_api_key=True)
    assert not ops.provider_api_key_status()['local_available']
    assert credential_scope('one', profile['profile'])[2] == 'https://example.test/v1'
