"""Current-user DPAPI credentials: local application data, never project exports."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from ctypes import wintypes
from urllib.parse import urlsplit, urlunsplit


def credential_scope(project_id, profile):
    parsed = urlsplit(profile['base_url'].strip().rstrip('/').removesuffix('/chat/completions'))
    host = (parsed.hostname or '').lower().encode('idna').decode('ascii')
    if ':' in host:
        host = '['+host+']'
    port = parsed.port
    if port and (parsed.scheme.lower(), port) not in (('https', 443), ('http', 80)):
        host += ':'+str(port)
    address = urlunsplit((parsed.scheme.lower(), host, parsed.path.rstrip('/'), '', ''))
    return str(project_id), profile['provider'], address


def _entropy(scope):
    return hashlib.sha256(json.dumps(scope, ensure_ascii=False).encode('utf-8')).digest()


def _dpapi(data, scope, decrypt=False):
    if os.name != 'nt':
        raise ValueError('本机加密凭据当前需要Windows当前用户DPAPI；可继续使用环境变量')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    def blob(value):
        buffer = ctypes.create_string_buffer(value)
        return Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer
    source, source_buffer = blob(data)
    entropy, entropy_buffer = blob(_entropy(scope))
    output = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        function = crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output))
    else:
        function = crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob), ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), 'Narrative local provider credential', ctypes.byref(entropy), None, None, 1, ctypes.byref(output))
    function.restype = wintypes.BOOL
    try:
        if not function(*args):
            raise ValueError('本机凭据无法解密，请重新保存或清除' if decrypt else '本机凭据加密失败，配置未保存')
        return ctypes.string_at(output.data, output.size)
    finally:
        if output.data:
            kernel.LocalFree(output.data)
        # Keep source buffers alive through the native call, then erase our copies.
        ctypes.memset(source_buffer, 0, len(source_buffer))
        ctypes.memset(entropy_buffer, 0, len(entropy_buffer))


def prepare_credential(project_id, profile, api_key=None, clear_api_key=False):
    if type(clear_api_key) is not bool or (api_key is not None and not isinstance(api_key, str)):
        raise ValueError('密钥须为文本，清除选项须为布尔值')
    scope = credential_scope(project_id, profile)
    value = (api_key or '').strip()
    if value and clear_api_key:
        raise ValueError('保存新密钥与清除密钥不能同时进行')
    if clear_api_key:
        return 'clear', None
    return ('set', _dpapi(value.encode('utf-8'), scope)) if value else ('preserve', None)


def ensure_schema(db, namespace='main'):
    db.execute(f'CREATE TABLE IF NOT EXISTS {namespace}.local_credentials ('
               'project_id TEXT NOT NULL, provider TEXT NOT NULL, base_url TEXT NOT NULL, encrypted_key BLOB NOT NULL, '
               'PRIMARY KEY(project_id,provider,base_url))')


def apply_credential(db, project_id, profile, prepared, namespace='main'):
    action, value = prepared
    if action == 'preserve':
        return
    ensure_schema(db, namespace)
    scope = credential_scope(project_id, profile)
    if action == 'clear':
        db.execute(f'DELETE FROM {namespace}.local_credentials WHERE project_id=? AND provider=? AND base_url=?', scope)
    else:
        db.execute(f'INSERT INTO {namespace}.local_credentials VALUES(?,?,?,?) '
                   'ON CONFLICT(project_id,provider,base_url) DO UPDATE SET encrypted_key=excluded.encrypted_key', (*scope, value))


def saved_key(store, project_id, profile):
    with store._connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_credentials'").fetchone():
            return None
        row = db.execute('SELECT encrypted_key FROM local_credentials WHERE project_id=? AND provider=? AND base_url=?', credential_scope(project_id, profile)).fetchone()
    if row is None:
        return None
    return _dpapi(bytes(row[0]), credential_scope(project_id, profile), decrypt=True).decode('utf-8')


def public_status(store, project_id, profile):
    from .harness import configured_key
    local = False
    unavailable = False
    try:
        local = bool(saved_key(store, project_id, profile))
    except (ValueError, UnicodeError):
        unavailable = True
    environment = bool(configured_key(profile['key_env'])[0])
    return {'available': local or environment, 'source': 'local' if local else 'environment' if environment else 'missing',
            'local_available': local, 'environment_available': environment,
            **({'error_code': 'local_credential_unavailable'} if unavailable else {})}
