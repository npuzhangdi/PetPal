"""Security, UI lifecycle and portable-config regression tests. Standard unittest."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QObject, Signal, QRect, QTimer
from PySide6.QtGui import QColor, QCloseEvent, QPixmap, QFontDatabase
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtTest import QTest
from core.settings import SettingsManager
from widgets.password_dialog import PasswordDialog
from widgets.settings_window import SettingsWindow
from widgets.privacy_screen import PrivacyScreen
from widgets.desktop_pet import DesktopPet
from widgets.theme import THEMES
from widgets.tray import WindowsTray, NotifyData
APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)
if not QFontDatabase.families():
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")


class PetPalTests(unittest.TestCase):
    def test_native_tray_failed_registration_reports_once_and_recovers(self):
        shell = SimpleNamespace(Shell_NotifyIconW=Mock(return_value=False))
        tray = SimpleNamespace(_wanted=True, _data=NotifyData, _shell=shell,
            _registered=False, _failures=0, _reported=False,
            unavailable=SimpleNamespace(emit=Mock()))
        for _ in range(4):
            WindowsTray._ensure_registered(tray)
        self.assertFalse(tray._registered)
        tray.unavailable.emit.assert_called_once()
        # Explorer becomes available: modify fails, add + version negotiation succeed.
        shell.Shell_NotifyIconW.side_effect = [False, True, True]
        WindowsTray._ensure_registered(tray)
        self.assertTrue(tray._registered)
        self.assertEqual(tray._failures, 0)
        self.assertFalse(tray._reported)
        self.assertEqual([call.args[0] for call in shell.Shell_NotifyIconW.call_args_list[-3:]], [1, 0, 4])
        # A later Explorer restart loses the icon; the periodic check adds it again.
        shell.Shell_NotifyIconW.side_effect = [False, True, True]
        WindowsTray._ensure_registered(tray)
        self.assertTrue(tray._registered)
        tray._wanted = False
        shell.Shell_NotifyIconW.reset_mock()
        WindowsTray._ensure_registered(tray)
        shell.Shell_NotifyIconW.assert_not_called()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="petpal-test-")
        self.root = Path(self.temp.name)
        assets = self.root / "assets"
        assets.mkdir()
        for name in ("petpal.ico", "petpal_icon.png"):
            shutil.copyfile(ROOT / "assets" / name, assets / name)
        self.settings = SettingsManager(self.root)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            if isinstance(window, PrivacyScreen):
                window.hide()
            else:
                window.hide()
            window.deleteLater()
        APP.processEvents()
        self.temp.cleanup()

    def wait_until(self, condition):
        deadline = time.monotonic() + 5
        while not condition():
            APP.processEvents()
            if time.monotonic() >= deadline:
                self.fail("Timed out waiting for the GUI operation")
            time.sleep(0.005)

    def test_password_roundtrip_and_salt(self):
        s = self.settings
        self.assertFalse(s.verify_password("1234"))
        s.set_password("中文 密码 ")
        old = s.get("password_hash")
        s.set_password("中文 密码 ")
        self.assertNotEqual(old, s.get("password_hash"))
        restored = SettingsManager(self.root)
        self.assertTrue(restored.verify_password("中文 密码 "))
        self.assertFalse(restored.verify_password("中文 密码"))
        data = json.loads((self.root / "config/settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("password", data)
        self.assertNotIn("中文 密码", json.dumps(data, ensure_ascii=False))

    def test_invalid_and_corrupt_config(self):
        self.settings.update({"pet_type": "invalid", "pet_size": "bad", "privacy_color": "oops", "pet_visible": "false"})
        self.assertEqual(self.settings.pet_size, 160)
        self.assertEqual(self.settings.pet_type, "cat")
        path = self.root / "config/settings.json"
        path.parent.mkdir()
        path.write_text("{broken", encoding="utf-8")
        restored = SettingsManager(self.root)
        self.assertTrue(restored.recovered)
        self.assertFalse(restored.has_password)
        self.assertTrue(path.with_suffix(".broken.json").exists())

    def test_legacy_local_password_migration(self):
        self.settings.set("password", "old-password")
        migrated = SettingsManager(self.root)
        self.assertTrue(migrated.verify_password("old-password"))
        self.assertNotIn("password", migrated._data)

    def test_new_password_requires_confirmation(self):
        dialog = PasswordDialog(self.settings)
        self.windows.append(dialog)
        dialog._new_edit.setText("12345")
        dialog._confirm_edit.setText("54321")
        dialog._on_accept()
        self.wait_until(lambda: dialog._task is None)
        self.assertFalse(self.settings.has_password)
        dialog._confirm_edit.setText("12345")
        dialog._on_accept()
        self.wait_until(lambda: dialog._task is None)
        self.assertEqual(dialog.result(), QDialog.Accepted)

    def test_password_change_requires_current_password(self):
        self.settings.set_password("old-password")
        dialog = PasswordDialog(self.settings, edit_mode=True)
        self.windows.append(dialog)
        dialog._new_edit.setText("new-password")
        dialog._confirm_edit.setText("new-password")
        dialog._pwd_edit.setText("wrong-password")
        dialog._on_accept()
        self.wait_until(lambda: dialog._task is None)
        self.assertTrue(self.settings.verify_password("old-password"))
        self.assertFalse(dialog.ok.isEnabled())
        dialog._pwd_edit.setText("old-password")
        dialog._on_accept()  # Enter cannot bypass cooldown.
        self.assertTrue(self.settings.verify_password("old-password"))
        dialog._ready()
        dialog._on_accept()
        self.wait_until(lambda: dialog._task is None)
        self.assertTrue(self.settings.verify_password("new-password"))

    def test_settings_two_pages_all_themes_and_visibility(self):
        window = SettingsWindow(self.settings)
        self.windows.append(window)
        window.show()
        APP.processEvents()
        self.assertEqual(window.tabs.count(), 2)
        self.assertEqual(window.tabs.tabText(0), "隐私屏")
        self.assertFalse(window.pet_enabled.isChecked())
        self.assertTrue(window.pet_controls.isHidden())
        quit_requested = Mock()
        window.quit_requested.connect(quit_requested)
        window.quit_button.click()
        quit_requested.assert_called_once_with()
        privacy_requested = Mock()
        window.privacy_requested.connect(privacy_requested)
        window.privacy_button.click()
        privacy_requested.assert_called_once_with()
        for key in THEMES:
            window._choose(window.theme_combo, key)
            self.assertFalse(window.grab().isNull())
        window.pet_visible.setChecked(False)
        window.pet_type.setCurrentIndex(3)
        window.apply_only()
        self.assertFalse(self.settings.get("pet_visible"))
        self.assertEqual(self.settings.pet_type, "rabbit")
        window.pet_type.setCurrentIndex(2)
        window.hide()
        window._load_values()
        self.assertEqual(window.pet_type.currentData(), "rabbit")
        window.privacy_bg_type.setCurrentIndex(1)
        self.assertFalse(window._collect())

    def test_image_import_is_relative_and_preview_preserved(self):
        source = self.root / "external.png"
        image = QPixmap(80, 60)
        image.fill(Qt.green)
        image.save(str(source))
        window = SettingsWindow(self.settings)
        self.windows.append(window)
        window._image_path = str(source)
        window.privacy_bg_type.setCurrentIndex(1)
        self.assertTrue(window._collect())
        self.wait_until(lambda: window._save_task is None and window._preview_task is None)
        self.assertTrue(self.settings.get("privacy_image").startswith("photos/"))
        self.assertTrue(Path(self.settings.privacy_image).exists())
        self.assertFalse(window.preview_label.pixmap().isNull())
        window.pet_enabled.setChecked(True)
        with patch("widgets.settings_window._store_background") as store:
            window.apply_only()
            store.assert_not_called()
        self.assertTrue(self.settings.get("pet_enabled"))

    def test_privacy_needs_password(self):
        privacy = PrivacyScreen(self.settings)
        self.windows.append(privacy)
        with self.assertRaises(RuntimeError):
            privacy.show()

    def test_privacy_wrong_password_close_and_cooldown(self):
        self.settings.set_password("right-password")
        privacy = PrivacyScreen(self.settings)
        self.windows.append(privacy)
        accepted = []
        privacy.unlock_accepted.connect(lambda: accepted.append(True))
        privacy.show()
        self.assertEqual(len(privacy.covers), len(APP.screens()))
        cover = privacy.covers[0]
        self.assertFalse(cover._refresh.isActive())
        cover.open_panel()
        QTest.keyClick(cover.password, Qt.Key_Escape)
        self.assertFalse(cover.panel.isVisible())
        self.assertTrue(privacy.active)
        cover.open_panel()
        cover.password.setText("wrong-password")
        cover.submit()
        self.wait_until(lambda: not privacy.verifying)
        self.assertTrue(cover._refresh.isActive())
        self.assertFalse(accepted)
        self.assertTrue(privacy.active)
        cover.password.setText("right-password")
        cover.submit()
        self.assertFalse(accepted)
        close = QCloseEvent()
        cover.closeEvent(close)
        self.assertFalse(close.isAccepted())
        cover.close_panel()
        self.assertFalse(cover._refresh.isActive())
        privacy.blocked_until = 0
        cover.open_panel()
        cover.password.setText("right-password")
        cover.submit()
        self.wait_until(lambda: not privacy.verifying)
        self.assertEqual(accepted, [True])

    def test_unlock_keeps_ui_responsive_and_only_checks_once(self):
        self.settings.set_password("right-password")
        privacy = PrivacyScreen(self.settings)
        self.windows.append(privacy)
        privacy.show()
        cover = privacy.covers[0]
        cover.open_panel()
        entered, release = Event(), Event()

        def slow_verify(candidate):
            entered.set()
            return release.wait(5) and candidate == "right-password"

        with patch.object(self.settings, "verify_password", side_effect=slow_verify) as verify:
            cover.password.setText("right-password")
            cover.submit()
            try:
                self.wait_until(entered.is_set)
                fired = []
                QTimer.singleShot(0, lambda: fired.append(True))
                self.wait_until(lambda: bool(fired))
                self.assertTrue(privacy.active)
                self.assertTrue(privacy.verifying)
                self.assertFalse(cover.unlock.isEnabled())
                cover.submit()
                verify.assert_called_once_with("right-password")
            finally:
                release.set()
            self.wait_until(lambda: not privacy.verifying)

    def test_privacy_image_renders_after_cover_appears(self):
        source = self.root / "wide.png"
        image = QPixmap(1600, 100)
        image.fill(Qt.green)
        self.assertTrue(image.save(str(source)))
        self.settings.set_password("right-password")
        self.settings.update({"privacy_bg_type": "image", "privacy_image": str(source)})
        privacy = PrivacyScreen(self.settings)
        self.windows.append(privacy)
        privacy.show()
        cover = privacy.covers[0]
        self.assertTrue(cover.isVisible())
        self.assertTrue(cover._background.isNull())
        self.assertEqual(cover.grab().toImage().pixelColor(0, 0), QColor(self.settings.privacy_color))
        self.wait_until(lambda: not cover._background.isNull())
        self.assertEqual(cover._background.size(), cover.size() * cover.devicePixelRatioF())
        self.assertEqual(cover.grab().toImage().pixelColor(0, 0), QColor(Qt.green))
        privacy.hide()
        self.assertTrue(privacy.image.isNull())
        self.assertFalse(privacy.covers)

    def test_hotplug_negative_origin_and_removal(self):
        class Monitor(QObject):
            geometryChanged = Signal(QRect)
            def geometry(self): return QRect(-1280, -100, 1280, 720)
        self.settings.set_password("password")
        privacy = PrivacyScreen(self.settings)
        self.windows.append(privacy)
        privacy.show()
        initial = len(privacy.covers)
        monitor = Monitor()
        privacy._screen_added(monitor)
        self.assertEqual(len(privacy.covers), initial + 1)
        cover = privacy.covers[-1]
        self.assertEqual(cover.geometry(), monitor.geometry())
        privacy._screen_removed(monitor)
        self.assertEqual(len(privacy.covers), initial)

    def test_controller_password_gates_and_live_settings(self):
        from core.app import AppController
        self.settings.set_password("password")
        with patch("core.app.SettingsManager", return_value=self.settings):
            controller = AppController(APP)
        controller.start()
        try:
            self.assertIsNone(controller.settings_window)
            self.assertIsNone(controller.pet)
            self.assertFalse(controller.act_pet.isVisible())
            with patch("core.app.PasswordDialog.exec", return_value=QDialog.Rejected):
                controller._activate_privacy()
                self.assertFalse(controller._privacy_active)
            with patch("core.app.PasswordDialog.exec", return_value=QDialog.Accepted):
                controller._activate_privacy()
            self.assertTrue(controller._privacy_active)
            self.assertFalse(controller.act_settings.isEnabled())
            self.assertFalse(controller.act_pwd.isEnabled())
            self.settings.set("password_protect_quit", False)
            with patch("core.app.PasswordDialog.exec", return_value=QDialog.Rejected):
                controller._quit()
            self.assertTrue(controller._privacy_active)
            controller.privacy.unlock_accepted.emit()
            self.assertFalse(controller._privacy_active)
        finally:
            controller.cleanup()
            controller.tray.deleteLater()

    def test_pet_requires_settings_opt_in_and_persists(self):
        from core.app import AppController

        path = self.root / "config/settings.json"
        path.parent.mkdir()
        path.write_text('{"pet_visible": true, "pet_type": "rabbit"}', encoding="utf-8")
        with patch("core.app.SettingsManager", return_value=SettingsManager(self.root)):
            controller = AppController(APP)
        controller.start()
        try:
            self.assertIsNone(controller.pet)
            self.assertFalse(controller.act_pet.isVisible())
            controller._pet_action("eat")
            controller._toggle_pet()
            self.assertIsNone(controller.pet)
            controller._open_settings()
            window = controller.settings_window
            self.windows.append(window)
            window.pet_enabled.setChecked(True)
            self.assertIsNone(controller.pet)
            window.apply_only()
            pet = controller.pet
            self.assertTrue(pet.isVisible())
            self.assertEqual(pet._pet_type, "rabbit")
            self.assertTrue(controller.act_pet.isVisible())
            with patch.object(pet, "_load_pet", wraps=pet._load_pet) as load, \
                 patch.object(controller.settings, "_save", wraps=controller.settings._save) as save:
                window.apply_only()
                load.assert_not_called()
                save.assert_not_called()
            restored = SettingsManager(self.root)
            self.assertTrue(restored.get("pet_enabled"))
            with patch("core.app.SettingsManager", return_value=restored):
                restarted = AppController(APP)
            try:
                restarted.start()
                self.assertTrue(restarted.pet.isVisible())
                restarted.act_pet.trigger()
                self.assertFalse(restarted.pet.isVisible())
                self.assertFalse(restarted.pet._anim_timer.isActive())
                self.assertFalse(SettingsManager(self.root).get("pet_visible"))
                restarted.act_pet.trigger()
                self.assertTrue(restarted.pet._anim_timer.isActive())
            finally:
                restarted.cleanup()
                restarted.tray.deleteLater()
            window.pet_enabled.setChecked(False)
            window.apply_only()
            self.assertIsNone(controller.pet)
            self.assertFalse(pet._anim_timer.isActive())
            self.assertFalse(controller.act_pet.isVisible())
            self.assertFalse(SettingsManager(self.root).get("pet_enabled"))
        finally:
            controller.cleanup()
            controller.tray.deleteLater()

    def test_setup_exit_respects_privacy_and_password_protection(self):
        from core.app import AppController
        with patch("core.app.SettingsManager", return_value=self.settings):
            controller = AppController(APP)
        try:
            with patch.object(controller, "activate") as activate:
                self.assertTrue(controller.prepare_setup_exit())
                controller._auth_busy = True
                self.assertFalse(controller.prepare_setup_exit())
                activate.assert_not_called()
                controller._auth_busy = False
                self.settings.set_password("protected-password")
                self.assertFalse(controller.prepare_setup_exit())
                activate.assert_called_once()
                self.settings.set("password_protect_quit", False)
                self.assertTrue(controller.prepare_setup_exit())
                controller._privacy_active = True
                self.assertFalse(controller.prepare_setup_exit())
        finally:
            controller.cleanup()
            controller.tray.deleteLater()

    def test_all_pet_assets_and_actions(self):
        pet = DesktopPet(self.settings)
        self.windows.append(pet)
        for key in ("cat", "dog", "cow", "rabbit"):
            pet.set_pet_type(key)
            self.assertFalse(pet._standing.isNull())
            self.assertTrue(all(not frame.isNull() for frame in pet._frames))
            for action in ("eat", "play", "sleep", "home"):
                pet.perform_action(action)
                self.assertFalse(pet.grab().isNull())
        pet.set_wander(False)
        pet._state_until = 0
        pet._tick_state()
        self.assertGreater(pet._state_until, pet._now_ms())
        pet.show()
        pet.hide()
        self.assertFalse(pet._anim_timer.isActive())
        pet.show()
        self.assertTrue(pet._anim_timer.isActive())


if __name__ == "__main__":
    unittest.main(verbosity=2)
