"""Installer regressions, including the real GUI launcher's delayed probe race."""
import contextlib
import io
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import install
import windows_integrity as integrity
from build import find_csc


class SetupTests(unittest.TestCase):
    def test_running_instance_stops_before_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = root / ".venv" / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.touch()
            with patch.object(install, "ROOT", root), \
                 contextlib.redirect_stdout(io.StringIO()), \
                 patch("build.application_running", side_effect=[True, False]), \
                 patch.object(install, "run", return_value=subprocess.CompletedProcess([], 0, "stopping\n")) as run:
                install.stop_running_for_setup()
                self.assertIn("setup_lifecycle.py", str(run.call_args.args[0][3]))

    def test_protected_or_legacy_instance_does_not_enter_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = root / ".venv" / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.touch()
            for response, message in (("protected", "密码保护"), ("unavailable", "退出程序")):
                with self.subTest(response=response), patch.object(install, "ROOT", root), \
                     contextlib.redirect_stdout(io.StringIO()), \
                     patch("build.application_running", return_value=True), \
                     patch.object(install, "run", return_value=subprocess.CompletedProcess([], 0, response)):
                    with self.assertRaisesRegex(RuntimeError, message):
                        install.stop_running_for_setup()

    def test_low_integrity_install_requires_user_desktop(self):
        with patch.object(integrity, "process_integrity", return_value=0x1000):
            with self.assertRaisesRegex(RuntimeError, "低完整性"):
                integrity.require_desktop_installer()

    def test_only_generated_executables_receive_desktop_label(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = root / "PetPal.exe"
            allowed.write_bytes(b"MZtest placeholder")
            other = root / "unrelated.exe"
            other.write_bytes(b"test placeholder")
            with patch.object(integrity, "process_integrity", return_value=0x2000), \
                 patch.object(integrity, "file_integrity", return_value=integrity.MEDIUM), \
                 patch.object(integrity, "_create_labeled_file") as create:
                integrity.prepare_desktop_executable(allowed, root)
                create.assert_not_called()
                with self.assertRaises(ValueError):
                    integrity.prepare_desktop_executable(other, root)
                create.assert_not_called()

    @unittest.skipIf(integrity.process_integrity() < integrity.MEDIUM,
                     "原生文件替换验证需要普通用户桌面进程")
    def test_inherited_low_executable_is_replaced_without_changing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "PetPal.exe"
            content = b"MZnative replacement regression"
            integrity._create_labeled_file(executable, content, level=0x1000)
            self.assertEqual(integrity.file_integrity(executable), 0x1000)
            integrity.prepare_desktop_executable(executable, root)
            self.assertGreaterEqual(integrity.file_integrity(executable), integrity.MEDIUM)
            self.assertEqual(executable.read_bytes(), content)
            self.assertEqual(list(root.iterdir()), [executable])

    def test_failed_replacement_preserves_original_and_reports_windows_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "PetPal.exe"
            content = b"MZoriginal executable"
            executable.write_bytes(content)
            with patch.object(integrity, "process_integrity", return_value=integrity.MEDIUM), \
                 patch.object(integrity, "file_integrity", return_value=0x1000), \
                 patch.object(integrity, "_create_labeled_file", side_effect=OSError("specific Windows reason")):
                with self.assertRaisesRegex(RuntimeError, "specific Windows reason"):
                    integrity.prepare_desktop_executable(executable, root)
            self.assertEqual(executable.read_bytes(), content)
            self.assertEqual(list(root.iterdir()), [executable])

    def test_subprocess_error_output_is_retained(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, "7"):
                install.run([sys.executable, "-I", "-c", "import sys; print('diagnostic detail'); sys.exit(7)"])
        self.assertIn("diagnostic detail", output.getvalue())

    @unittest.skipIf(integrity.process_integrity() < integrity.MEDIUM,
                     "原生编译验证需要普通用户桌面进程；当前为低完整性自动化会话")
    def test_gui_launcher_waits_for_delayed_probe_and_propagates_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / ".venv" / "Scripts"
            scripts.mkdir(parents=True)
            (root / "tools").mkdir()
            (root / "tools" / "runtime_probe.py").touch()
            stub = root / "slow_probe.cs"
            stub.write_text('''using System; using System.IO; using System.Threading;
class Probe { static int Main() {
    Thread.Sleep(3500);
    string root = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", ".."));
    File.WriteAllText(Path.Combine(root, "probe-completed.txt"), "complete");
    return 17;
} }''', encoding="utf-8")
            csc = find_csc()
            for source, target in ((ROOT / "tools" / "launcher.cs", root / "PetPal.exe"),
                                   (stub, scripts / "PetPal.exe")):
                result = subprocess.run([str(csc), "/nologo", "/target:winexe", "/codepage:65001",
                    "/out:" + str(target), str(source)], capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode("mbcs", errors="replace"))
                integrity.prepare_desktop_executable(target, root)
            started = time.monotonic()
            result = subprocess.run([str(root / "PetPal.exe"), "--check-runtime"], timeout=15)
            self.assertEqual(result.returncode, 17)
            self.assertTrue((root / "probe-completed.txt").is_file())
            self.assertGreaterEqual(time.monotonic() - started, 3.4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
