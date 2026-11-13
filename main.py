"""PetPal entry: tray-only application with an installation-scoped single-instance lock.

PetPal.exe -> .venv/Scripts/PetPal.exe (branded real CPython GUI binary) -> main.py.
The launcher, venv and code remain separate. Errors are logged locally and surfaced
with a native message box because no console is attached.
"""

import ctypes
import sys
import traceback
import hashlib
import os
import logging

sys.dont_write_bytecode = True
from pathlib import Path

_APP_NAME = "PetPal"
_BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BASE_DIR))

#: 任务栏 / 托盘的归组标识。格式约定为 公司.产品.子产品，不能带 "Local\" 前缀，
#: 否则同一应用会被拆成多个任务栏图标。
_APP_USER_MODEL_ID = "PetPal.DesktopPet"

_INSTALL_ID = hashlib.sha256(str(_BASE_DIR).casefold().encode("utf-8")).hexdigest()[:16]
_MUTEX_NAME = "Local\\PetPal_" + _INSTALL_ID
_IPC_SOCKET = "petpal_" + _INSTALL_ID

# 全局持有，防止被 GC（互斥体句柄必须活到进程结束）
_singleton_handle = None
_ipc_server = None

# MessageBox 样式
_MB_OK = 0x00000000
_MB_ICONERROR = 0x00000010
_MB_ICONWARNING = 0x00000030
_MB_TOPMOST = 0x00040000


def _message_box(text: str, icon: int = _MB_ICONERROR) -> None:
    """不依赖 Qt 的兜底提示框。

    进程跑在 GUI 子系统下（没有控制台），Qt 又可能因缺少依赖而导入失败，
    此时只有直接调 user32 才能把问题告诉用户。
    """
    try:
        ctypes.windll.user32.MessageBoxW(
            None, text, _APP_NAME, _MB_OK | icon | _MB_TOPMOST
        )
    except Exception:
        pass


def _set_app_user_model_id() -> None:
    """设置进程的 AppUserModelID，让任务栏把窗口 / 托盘归到 PetPal 名下。

    这是任务栏显示"PetPal + 自己的图标"而不是归到 pythonw.exe 的关键一步；
    必须在创建任何窗口之前调用。失败不影响功能，因此静默忽略。
    """
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            _APP_USER_MODEL_ID
        )
    except Exception:
        pass


def _app_icon():
    """应用图标：优先 assets/petpal.ico，缺失则返回空图标。"""
    from PySide6.QtGui import QIcon

    ico = _BASE_DIR / "assets" / "petpal.ico"
    if ico.exists():
        icon = QIcon(str(ico))
        if not icon.isNull():
            return icon
    return QIcon()


def _acquire_single_instance() -> bool:
    """尝试获取单实例锁。

    返回 True = 本进程拿到锁，可以正常启动；
    返回 False = 已有一个实例在运行。
    任何异常都视为"拿到锁"（绝不因为锁机制本身把用户挡在门外）。
    """
    global _singleton_handle
    try:
        from ctypes import wintypes

        ERROR_ALREADY_EXISTS = 183
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE

        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateMutexW(None, True, _MUTEX_NAME)
        err = ctypes.get_last_error()
        if not handle:
            return True
        if err == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _singleton_handle = handle
        return True
    except Exception:
        return True


def _notify_existing_instance() -> bool:
    """通知已在运行的实例把宠物 / 托盘唤到前台。"""
    try:
        from PySide6.QtNetwork import QLocalSocket

        sock = QLocalSocket()
        sock.connectToServer(_IPC_SOCKET)
        if sock.waitForConnected(400):
            sock.write(b"show")
            sock.flush()
            sock.waitForBytesWritten(400)
            sock.disconnectFromServer()
            return True
    except Exception:
        pass
    return False


def _start_ipc_listener(controller) -> None:
    """Handle activation and a password-aware shutdown request for local repair."""
    global _ipc_server
    try:
        from PySide6.QtNetwork import QLocalServer
        from PySide6.QtCore import QTimer

        QLocalServer.removeServer(_IPC_SOCKET)
        server = QLocalServer()
        if not server.listen(_IPC_SOCKET):
            return

        def _read_command(conn):
            payload = bytes(conn.readAll())
            conn._petpal_command = getattr(conn, "_petpal_command", b"") + payload
            command = conn._petpal_command
            if command == b"show":
                controller.activate()
            elif command == b"setup-exit\n":
                allowed = controller.prepare_setup_exit()
                conn.write(b"stopping\n" if allowed else b"protected\n")
                conn.flush()
                if allowed:
                    # Give the client its acknowledgement before releasing the mutex.
                    QTimer.singleShot(150, controller._quit)
            elif len(command) > 64:
                conn.disconnectFromServer()

        def _on_new_connection() -> None:
            while server.hasPendingConnections():
                conn = server.nextPendingConnection()
                if conn is not None:
                    conn.readyRead.connect(lambda c=conn: _read_command(c))
                    if conn.bytesAvailable():
                        _read_command(conn)
                    conn.disconnected.connect(conn.deleteLater)

        server.newConnection.connect(_on_new_connection)
        _ipc_server = server
    except Exception:
        pass


def _report_fatal(detail: str) -> None:
    """记录并弹窗报告致命错误（无控制台，弹窗是唯一能触达用户的方式）。"""
    try:
        log_path = _BASE_DIR / "cache" / "logs" / "error_log.txt"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write("\n--- 未捕获异常 ---\n" + detail + "\n")
    except Exception:
        pass

    last_line = detail.strip().splitlines()[-1] if detail.strip() else "未知错误"
    _message_box(
        "PetPal 启动失败。\r\n\r\n"
        + last_line[:300]
        + "\r\n\r\n请重新运行 first_run.bat 修复运行环境，\r\n"
        + "或查看 cache\\logs\\error_log.txt 了解详情。"
    )


def main() -> int:
    # 必须在任何窗口创建之前设置，否则任务栏归组已定型
    _set_app_user_model_id()
    # Desktop launches must never inherit an offscreen/minimal Qt test backend.
    if sys.platform == "win32":
        os.environ["QT_QPA_PLATFORM"] = "windows"
    log_dir = _BASE_DIR / "cache" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    from logging.handlers import RotatingFileHandler
    handler = RotatingFileHandler(log_dir / "petpal.log", maxBytes=256_000, backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    # 应用名 + 窗口图标：让任务管理器 / 托盘以 PetPal 显示
    app.setApplicationName(_APP_NAME)
    app.setApplicationDisplayName(_APP_NAME)
    app.setWindowIcon(_app_icon())
    app.setOrganizationName(_APP_NAME)

    if not _acquire_single_instance():
        raised = _notify_existing_instance()
        if raised:
            return 0
        try:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(
                None, "程序已在运行",
                ("已把正在运行的程序显示出来。" if raised else "程序已经在运行。")
                + "\n\n请查看任务栏右下角的托盘图标。",
            )
        except Exception:
            pass
        return 0

    app.setQuitOnLastWindowClosed(False)

    from core.app import AppController

    sys.excepthook = lambda kind, value, tb: _report_fatal("".join(traceback.format_exception(kind, value, tb)))
    controller = AppController(app)
    controller.apply_theme_to_app(app)
    _start_ipc_listener(controller)
    controller.start()
    app.aboutToQuit.connect(controller.cleanup)
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        _report_fatal(traceback.format_exc())
        sys.exit(1)
