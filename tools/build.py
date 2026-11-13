"""Compile tiny GUI launchers and brand a real CPython GUI executable locally."""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pe_res
from windows_integrity import require_desktop_installer, prepare_desktop_executable


def application_running(root):
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    name = "Local\\PetPal_" + hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:16]
    handle = kernel.CreateMutexW(None, False, name)
    error = ctypes.get_last_error()
    if handle:
        kernel.CloseHandle(handle)
    if not handle:
        raise ctypes.WinError(error)
    return error == 183


def ensure_not_running(root):
    if application_running(root):
        raise RuntimeError("PetPal 正在运行，请先从托盘退出再修复安装。")


def find_csc():
    windows = Path(os.environ.get("WINDIR", "C:/Windows"))
    for folder in ("Framework64", "Framework"):
        path = windows / "Microsoft.NET" / folder / "v4.0.30319" / "csc.exe"
        if path.is_file():
            return path
    raise RuntimeError("缺少系统 .NET Framework 4.x C# 编译器，请启用该 Windows 组件后重试。")


def stamp_exe(exe, ico):
    # Branding is a requirement, so failure must abort instead of silently succeeding.
    pe_res.set_app_icon(exe, ico)
    pe_res.set_version_info(exe, file_description="PetPal", product_name="PetPal",
        company_name="PetPal", original_filename=exe.name, version=(0, 0, 0, 0))


def prepare_runtime(root, ico):
    scripts = root / ".venv" / "Scripts"
    # A Windows venv pythonw.exe is a redirector which spawns the base pythonw.exe.
    # Copy the real base executable instead, with its adjacent DLL dependencies.
    base = Path(sys.base_prefix)
    source = base / "pythonw.exe"
    if not source.is_file() or not (root / ".venv" / "pyvenv.cfg").is_file():
        raise RuntimeError("官方 Python GUI 解释器或本地虚拟环境缺失")
    scripts.mkdir(parents=True, exist_ok=True)
    target = scripts / "PetPal.exe"
    pending = scripts / "PetPal.pending.exe"
    shutil.copy2(source, pending)
    for dll in base.glob("*.dll"):
        shutil.copy2(dll, scripts / dll.name)
    stamp_exe(pending, ico)
    os.replace(pending, target)
    return target


def build(root):
    require_desktop_installer()
    ensure_not_running(root)
    ico = root / "assets" / "petpal.ico"
    csc = find_csc()
    staging = root / "cache" / "build"
    staging.mkdir(parents=True, exist_ok=True)
    for source, filename in (("launcher.cs", "PetPal.exe"), ("uninstaller.cs", "uninstall.exe")):
        output = staging / filename
        result = subprocess.run([str(csc), "/nologo", "/target:winexe", "/platform:anycpu",
            "/optimize+", "/debug-", "/codepage:65001", "/win32icon:" + str(ico),
            "/out:" + str(output), str(root / "tools" / source)], capture_output=True, text=True,
            encoding="mbcs", errors="replace", timeout=45)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        stamp_exe(output, ico)
        if output.stat().st_size >= 1_000_000:
            raise RuntimeError(filename + " 启动器超过 1 MB")
    runtime = prepare_runtime(root, ico)
    prepare_desktop_executable(runtime, root)
    for filename in ("PetPal.exe", "uninstall.exe"):
        os.replace(staging / filename, root / filename)
        prepare_desktop_executable(root / filename, root)
        print("  已生成 %s (%d 字节)" % (filename, (root / filename).stat().st_size), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    try:
        build(args.root.resolve())
    except Exception as exc:
        print("构建失败：" + str(exc), file=sys.stderr)
        sys.exit(1)
