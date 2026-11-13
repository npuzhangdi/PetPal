"""Functional installation check; never changes a user's configuration."""
import json
import os
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def verify():
    for filename in ("PetPal.exe", "uninstall.exe"):
        target = ROOT / filename
        assert target.is_file() and target.stat().st_size < 1_000_000, filename
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for test in ("test_petpal.py", "test_setup.py"):
        subprocess.run([sys.executable, "-I", "-X", "utf8", "-B", str(ROOT / "tests" / test)],
                       env=environment, check=True, timeout=45)
    report = ROOT / "cache" / "runtime-check.json"
    report.unlink(missing_ok=True)
    result = subprocess.run([str(ROOT / "PetPal.exe"), "--check-runtime"], env=environment, timeout=30)
    assert result.returncode == 0 and report.is_file(), "GUI 启动链失败"
    proof = json.loads(report.read_text(encoding="utf-8"))
    runtime = ROOT / ".venv" / "Scripts" / "PetPal.exe"
    assert proof.get("ok"), proof
    assert Path(proof["image"]).resolve() == runtime.resolve(), proof
    assert Path(proof["executable"]).resolve() == runtime.resolve(), proof
    assert Path(proof["prefix"]).resolve() == (ROOT / ".venv").resolve(), proof
    assert proof.get("platform") == "windows", proof
    assert not proof.get("console_window"), proof
    if proof.get("integrity", 0) < 0x2000:
        raise RuntimeError("PetPal 运行时仍处于 Windows 低完整性级别，请从资源管理器直接运行 first_run.bat 修复。")
    if not proof.get("tray_registered"):
        raise RuntimeError("Windows 通知区实际注册失败，请查看 cache/runtime-check.json；安装未标记为成功。")
    print("验证通过：桌宠、设置、密码、多屏遮罩、启动链和实际托盘注册；常驻映像为 PetPal.exe。")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    verify()
