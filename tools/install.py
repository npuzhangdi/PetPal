"""Local-only setup, called with the user's explicitly selected interpreter."""
from __future__ import annotations
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
MIRRORS = (
    ("清华源", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("阿里源", "https://mirrors.aliyun.com/pypi/simple"),
    ("腾讯源", "https://mirrors.cloud.tencent.com/pypi/simple"),
    ("官方源", "https://pypi.org/simple"),
)


def run(command, *, check=True, **kwargs):
    quiet = kwargs.get("capture_output", False)
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    try:
        result = subprocess.run([str(x) for x in command], cwd=ROOT, **kwargs)
    except subprocess.TimeoutExpired as exc:
        for output in (exc.stdout, exc.stderr):
            if output:
                print(output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output, flush=True)
        raise
    if not quiet:
        for output in (result.stdout, result.stderr):
            if output:
                print(output, end="" if output.endswith("\n") else "\n", flush=True)
    if check and result.returncode:
        raise RuntimeError("命令失败（%s）：%s" % (result.returncode, command[0]))
    return result


class InstallLog:
    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, text):
        self.log.write(text)
        self.stream.write(text)
        return len(text)

    def flush(self):
        self.log.flush()
        self.stream.flush()


def benchmark(mirror):
    name, url = mirror
    started = time.perf_counter()
    try:
        request = urllib.request.Request(url + "/pyside6-essentials/", headers={"User-Agent": "PetPal-Setup/0.0.0"})
        with urllib.request.urlopen(request, timeout=6) as response:
            body = response.read(256 * 1024)
            if response.status != 200 or b"whl" not in body:
                raise ValueError("package index unavailable")
        return time.perf_counter() - started, name, url
    except Exception:
        return float("inf"), name, url


def safe_local_remove(path):
    path = Path(path)
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise RuntimeError("拒绝删除链接目录：" + str(path))
    resolved = path.resolve()
    if resolved.parent != ROOT or resolved.name != ".venv":
        raise RuntimeError("非法环境路径：" + str(resolved))
    if resolved.exists():
        shutil.rmtree(resolved)


def stop_running_for_setup():
    from build import application_running
    if not application_running(ROOT):
        return
    print("  检测到 PetPal 正在运行，正在请求正常退出以继续修复安装。", flush=True)
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    response = "unavailable"
    if python.is_file():
        try:
            result = run([python, "-I", "-B", ROOT / "tools" / "setup_lifecycle.py", ROOT],
                         timeout=8, capture_output=True, check=False)
            response = result.stdout.strip() if result.returncode == 0 else "unavailable"
        except (OSError, subprocess.TimeoutExpired):
            pass
    if response == "stopping":
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not application_running(ROOT):
                print("  PetPal 已正常退出，继续安装。", flush=True)
                return
            time.sleep(0.15)
    elif response == "protected":
        raise RuntimeError("PetPal 处于密码保护状态。请先验证密码并退出程序，再运行 first_run.bat；环境尚未修改。")
    raise RuntimeError("旧版 PetPal 尚在运行或未能正常退出。请在设置窗口点击底部“退出程序”（关闭设置窗口不会退出），"
                       "再运行 first_run.bat；环境尚未修改。")


def setup():
    import sysconfig
    if not ((3, 10) <= sys.version_info[:2] < (3, 15) and sys.implementation.name == "cpython"
            and sysconfig.get_platform() in ("win-amd64", "win-arm64")
            and not sysconfig.get_config_var("Py_GIL_DISABLED")):
        raise RuntimeError("请选择官方 64 位 CPython 3.10–3.14（标准 GIL 版本）")
    from build import ensure_not_running
    from windows_integrity import require_desktop_installer, prepare_desktop_executable
    require_desktop_installer()
    print("  检查运行状态", flush=True)
    stop_running_for_setup()
    ensure_not_running(ROOT)
    env = ROOT / ".venv"
    python = env / "Scripts" / "python.exe"
    marker = ROOT / "cache" / "installed.json"
    previous = {}
    environment_marker = ROOT / "cache" / "environment.json"
    metadata = marker if marker.exists() else environment_marker
    if metadata.exists():
        try:
            previous = json.loads(metadata.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    print("[2/5] 准备本地虚拟环境", flush=True)
    # Venv paths are machine-specific. Copies and interpreter changes require a rebuild.
    reuse = (python.is_file() and previous.get("root", str(ROOT)) == str(ROOT)
             and previous.get("python", sys.executable) == sys.executable)
    if reuse:
        try:
            result = run([python, "-I", "-c", "import sys,json; print(json.dumps([sys.prefix,sys.base_prefix]))"], timeout=15, capture_output=True)
            prefix, base = json.loads(result.stdout)
            reuse = Path(prefix).resolve() == env.resolve() and Path(base).resolve() == Path(sys.base_prefix).resolve()
        except (RuntimeError, subprocess.TimeoutExpired, ValueError):
            reuse = False
    if not reuse:
        safe_local_remove(env)
        run([sys.executable, "-I", "-m", "venv", env], timeout=240)
    else:
        print("  复用已验证的本地环境，继续上次未完成的安装。", flush=True)
    # Venv redirectors inherit the folder label too. Normalize before invoking
    # pip/build through them, otherwise even a normal desktop installer forks low.
    for executable in (python, env / "Scripts" / "pythonw.exe"):
        prepare_desktop_executable(executable, ROOT)
    environment_marker.write_text(json.dumps({"root": str(ROOT), "python": sys.executable}), encoding="utf-8")
    marker.unlink(missing_ok=True)
    print("[3/5] 四源测速（读取目标包索引，包含连接和下载耗时）", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        ranked = sorted(pool.map(benchmark, MIRRORS))
    for elapsed, name, url in ranked:
        print("  %s：%s" % (name, "不可用" if elapsed == float("inf") else "%d ms" % (elapsed * 1000)), flush=True)
    available = [item for item in ranked if item[0] != float("inf")]
    if not available:
        raise RuntimeError("四个源均不可用，请检查网络后重新运行 first_run.bat")
    print("  优先使用：" + available[0][1], flush=True)
    pip_env = os.environ.copy()
    # All pip settings and cache remain scoped to this invocation and this folder.
    pip_env.update(PIP_CONFIG_FILE=os.devnull, PIP_CACHE_DIR=str(ROOT / "cache" / "pip"),
                   PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    for key in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_TARGET", "PIP_PREFIX", "PIP_USER", "PYTHONHOME", "PYTHONPATH"):
        pip_env.pop(key, None)
    installed = False
    for _, name, url in available:
        print("  正在使用 %s 安装依赖，失败会自动切换下一源" % name, flush=True)
        result = run([python, "-I", "-m", "pip", "install", "--index-url", url,
            "--only-binary=:all:", "--progress-bar", "off", "--timeout", "15", "--retries", "1", "-r", str(ROOT / "tools" / "requirements.txt")], env=pip_env, check=False, timeout=600)
        if result.returncode == 0:
            installed = True
            break
    if not installed:
        raise RuntimeError("所有可用源安装失败，详细错误见上方输出")
    print("[4/5] 本地编译 PetPal.exe 与 uninstall.exe，并准备同名运行时", flush=True)
    run([python, "-I", "-B", ROOT / "tools" / "build.py", "--root", ROOT], timeout=90)
    print("[5/5] 检查依赖、窗口创建、运行时进程和启动器大小", flush=True)
    run([python, "-I", "-B", ROOT / "tools" / "verify_install.py"], timeout=60)
    marker.parent.mkdir(exist_ok=True)
    marker.write_text(json.dumps({"root": str(ROOT), "python": sys.executable, "version": sys.version, "mirror": url}, indent=2), encoding="utf-8")
    print("安装完成。双击 PetPal.exe 启动，托盘右键打开设置。首次开启隐私屏时自定义密码。", flush=True)
    print("未修改系统 PATH 或系统 pip 配置。卸载请运行 uninstall.exe。", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # Prevent child Python/pip from putting temp files outside the application directory.
    temp = ROOT / "cache" / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    logs = ROOT / "cache" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    install_log = open(logs / "install.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = InstallLog(sys.stdout, install_log)
    sys.stderr = InstallLog(sys.stderr, install_log)
    print("\n--- PetPal 安装 / 修复：%s ---" % time.strftime("%Y-%m-%d %H:%M:%S"), flush=True)
    os.environ.update(TEMP=str(temp), TMP=str(temp), PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    import msvcrt
    lock = open(ROOT / "cache" / "setup.lock", "a+b")
    lock.seek(0)
    lock.write(b"0")
    lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        setup()
    except (Exception, KeyboardInterrupt) as exc:
        traceback.print_exc()
        print("安装未完成：" + str(exc), file=sys.stderr)
        print("修正问题后可重新运行 first_run.bat；配置与素材会保留。", file=sys.stderr)
        print("完整日志：cache/logs/install.log", file=sys.stderr)
        sys.exit(1)
    finally:
        lock.close()
