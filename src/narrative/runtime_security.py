"""Read the current Windows integrity level before starting the desktop."""

import ctypes
import sys
from ctypes import wintypes

MEDIUM_INTEGRITY = 0x2000


def current_integrity_level() -> int | None:
    """Return the process token's mandatory-label RID; other platforms skip it."""
    if sys.platform != "win32":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.HANDLE)]
    security.OpenProcessToken.restype = wintypes.BOOL
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                             wintypes.LPVOID, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
    security.GetTokenInformation.restype = wintypes.BOOL
    security.GetSidSubAuthorityCount.argtypes = [wintypes.LPVOID]
    security.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    security.GetSidSubAuthority.argtypes = [wintypes.LPVOID, wintypes.DWORD]
    security.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

    token = wintypes.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = wintypes.DWORD()
        security.GetTokenInformation(token, 25, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(needed.value)
        if not security.GetTokenInformation(token, 25, buffer, needed.value, ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        # TOKEN_MANDATORY_LABEL starts with SID_AND_ATTRIBUTES, whose first
        # field is the SID pointer. The label RID is its last sub-authority.
        sid = ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID))[0]
        count = security.GetSidSubAuthorityCount(sid)[0]
        return int(security.GetSidSubAuthority(sid, count - 1)[0])
    finally:
        kernel.CloseHandle(token)


def require_medium_integrity() -> None:
    level = current_integrity_level()
    if level is not None and level < MEDIUM_INTEGRITY:
        raise RuntimeError(
            "本地 Python 运行时当前处于 Low（低完整性）进程，无法正常写入普通工程目录和配置库。"
            "这是运行时完整性标签异常；正常用户启动应保持 Medium（中完整性）。"
            "请修复本地运行时标签后重新启动。"
        )
