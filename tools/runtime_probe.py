"""Installer probe executed through the actual GUI launcher and runtime."""
import ctypes
import json
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
report = ROOT / "cache" / "runtime-check.json"
report.parent.mkdir(parents=True, exist_ok=True)
try:
    os.environ["QT_QPA_PLATFORM"] = "windows"
    from PySide6.QtWidgets import QApplication, QWidget, QSystemTrayIcon
    from PySide6.QtCore import QTimer
    from widgets.tray import WindowsTray
    from tools.windows_integrity import process_integrity
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    widget = QWidget()
    widget.resize(80, 80)
    widget.grab()
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetConsoleWindow.restype = wintypes.HWND
    kernel.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    path = ctypes.create_unicode_buffer(32768)
    kernel.GetModuleFileNameW(None, path, len(path))
    proof = {"ok": True, "executable": sys.executable,
        "image": path.value, "prefix": sys.prefix, "pid": os.getpid(),
        "platform": app.platformName(),
        "integrity": process_integrity(),
        "console_window": bool(kernel.GetConsoleWindow()),
        "tray_available": QSystemTrayIcon.isSystemTrayAvailable()}
    tray = WindowsTray(ROOT)
    tray.show()
    def finish():
        rect = tray.geometry()
        proof.update(tray_registered=tray.registered,
                     tray_geometry=[rect.x(), rect.y(), rect.width(), rect.height()])
        report.write_text(json.dumps(proof, indent=2), encoding="utf-8")
        tray.dispose()
        app.quit()
    QTimer.singleShot(6500, finish)
    app.exec()
except Exception as exc:
    report.write_text(json.dumps({"ok": False, "error": repr(exc)}), encoding="utf-8")
    sys.exit(1)
