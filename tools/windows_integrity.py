"""Desktop runtime integrity checks; never elevate a process or change folder ACLs."""
from __future__ import annotations
import ctypes
import os
import re
import uuid
from ctypes import wintypes
from pathlib import Path

MEDIUM = 0x2000


def process_integrity() -> int:
    """Read the current process's mandatory integrity RID (low=4096, medium=8192)."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                          wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetSidSubAuthorityCount.argtypes = [wintypes.LPVOID]
    advapi.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    advapi.GetSidSubAuthority.argtypes = [wintypes.LPVOID, wintypes.DWORD]
    advapi.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = wintypes.DWORD()
        advapi.GetTokenInformation(token, 25, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 25, buffer, len(buffer), ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID))[0]
        count = advapi.GetSidSubAuthorityCount(sid)[0]
        return int(advapi.GetSidSubAuthority(sid, count - 1)[0])
    finally:
        kernel.CloseHandle(token)


def require_desktop_installer():
    if process_integrity() < MEDIUM:
        raise RuntimeError("当前安装进程处于 Windows 低完整性级别，无法配置普通桌面托盘。"
                           "请从资源管理器直接双击 first_run.bat；不要从受限进程启动。无需管理员权限。")


def file_integrity(path: Path) -> int:
    """Read only the mandatory label; querying an audit SACL is unnecessary."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
        wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPVOID)]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    descriptor = wintypes.LPVOID()
    string = wintypes.LPWSTR()
    result = advapi.GetNamedSecurityInfoW(str(path), 1, 0x10, None, None, None, None,
                                          ctypes.byref(descriptor))
    if result in (1, 50):  # Filesystems without Windows mandatory labels.
        return MEDIUM
    if result:
        raise ctypes.WinError(result)
    try:
        if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 0x10,
                                                                           ctypes.byref(string), None):
            raise ctypes.WinError(ctypes.get_last_error())
        labels = re.findall(r"\(ML;[^;]*;[^;]*;[^;]*;[^;]*;([^)]*)\)", string.value or "")
        levels = {"UN": 0, "LW": 0x1000, "ME": MEDIUM, "MP": 0x2100, "HI": 0x3000, "SI": 0x4000}
        return min((levels[value] if value in levels else int(value.rsplit("-", 1)[-1])
                    for value in labels), default=MEDIUM)
    finally:
        if string:
            kernel.LocalFree(string)
        if descriptor:
            kernel.LocalFree(descriptor)


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", wintypes.LPVOID),
                ("bInheritHandle", wintypes.BOOL)]


def _create_labeled_file(path: Path, content: bytes, level=MEDIUM):
    """Create a NEW file at no higher than the caller's existing integrity.

    Assigning the initial label avoids requiring WRITE_OWNER on an existing
    executable. DACL and ownership follow ordinary file creation rules.
    """
    if level not in (0x1000, MEDIUM) or process_integrity() < level:
        raise PermissionError("不能创建高于当前进程完整性级别的文件")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR,
        wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(_SecurityAttributes), wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel.WriteFile.restype = wintypes.BOOL
    kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel.FlushFileBuffers.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    descriptor = wintypes.LPVOID()
    label = "ME" if level == MEDIUM else "LW"
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            "S:(ML;;NW;;;" + label + ")", 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
        handle = kernel.CreateFileW(str(path), 0x40000000, 0, ctypes.byref(attributes), 1, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.LocalFree(descriptor)
    try:
        written = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(content)
        if not kernel.WriteFile(handle, buffer, len(content), ctypes.byref(written), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value != len(content):
            raise OSError("启动项文件未完整写入")
        if not kernel.FlushFileBuffers(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


def prepare_desktop_executable(path: Path, root: Path):
    """Replace only a generated low-labeled EXE with an identical desktop file.

    Never relabel an existing file, edit its DACL, or modify folder permissions.
    This operation requires an already ordinary desktop installation process.
    """
    path = Path(path)
    if path.is_symlink():
        raise ValueError("拒绝修改链接启动项：" + str(path))
    root = root.resolve()
    path = path.resolve()
    scripts = root / ".venv" / "Scripts"
    allowed = {root / "PetPal.exe", root / "uninstall.exe",
               scripts / "PetPal.exe", scripts / "python.exe", scripts / "pythonw.exe"}
    if path not in allowed or not path.is_file():
        raise ValueError("非法桌面启动项路径：" + str(path))
    require_desktop_installer()
    if file_integrity(path) >= MEDIUM:
        return
    content = path.read_bytes()
    if not content.startswith(b"MZ"):
        raise ValueError("启动项不是有效的 Windows exe：" + str(path))
    pending = path.with_name(path.name + ".desktop-" + uuid.uuid4().hex + ".tmp")
    try:
        _create_labeled_file(pending, content)
        if file_integrity(pending) < MEDIUM or pending.read_bytes() != content:
            raise OSError("新启动项的完整性或内容校验失败")
        os.replace(pending, path)
        if file_integrity(path) < MEDIUM:
            raise OSError("启动项替换后的完整性校验失败")
    except OSError as exc:
        raise RuntimeError("创建桌面启动项失败：%s；Windows 原因：%s" % (path, exc)) from exc
    finally:
        pending.unlink(missing_ok=True)
