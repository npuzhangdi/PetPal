"""应用核心控制器：管理托盘、各窗口生命周期。

模块化结构（main.py 之下）：
  core/app.py        —— 本文件，托盘 + 生命周期调度
  core/settings.py   —— 配置持久化
  widgets/           —— UI 组件（宠物 / 隐私屏 / 设置 / 密码框）
  tools/             —— 构建脚本（生成启动器与运行时），仅安装时需要
"""

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QPainterPath

from widgets.desktop_pet import DesktopPet
from widgets.privacy_screen import PrivacyScreen
from widgets.password_dialog import PasswordDialog
from widgets.settings_window import SettingsWindow
from core.settings import SettingsManager
from widgets.tray import create_tray, WindowsTray

_APP_NAME = "PetPal"


def _make_tray_icon() -> QIcon:
    """托盘图标：优先 assets/petpal.ico，缺失则绘制简约圆角方块。"""
    from pathlib import Path
    ico = Path(__file__).resolve().parent.parent / "assets" / "petpal.ico"
    if ico.exists():
        ic = QIcon(str(ico))
        if not ic.isNull():
            return ic
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    color = QColor("#4FC3F7")
    path = QPainterPath()
    path.addRoundedRect(4, 4, 24, 24, 6, 6)
    p.fillPath(path, color)
    p.end()
    return QIcon(pm)


class AppController(QObject):
    """统一管理所有组件。"""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.settings = SettingsManager()

        # 组件
        self.pet: DesktopPet | None = None
        self.privacy: PrivacyScreen | None = None
        self.settings_window: SettingsWindow | None = None

        # 状态（宠物显隐状态跨重启记住）
        self._pet_visible: bool = self.settings.get("pet_visible", True)
        self._privacy_active = False

        # 托盘（带应用名 + 图标，任务管理器 / 托盘以 PetPal 显示）
        self.tray = create_tray(self.settings.root, self)
        self.tray.setIcon(_make_tray_icon())
        self.tray.setToolTip(_APP_NAME)
        self._build_tray_menu()
        if isinstance(self.tray, WindowsTray):
            self.tray.settings_requested.connect(self._open_settings)
            self.tray.unavailable.connect(self._on_tray_unavailable)
        else:
            self.tray.activated.connect(lambda reason: self._open_settings() if reason in
                (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动时按上次的状态恢复：宠物若上次"显示"则显示。"""
        if self._pet_visible:
            self._show_pet()

    # ------------------------------------------------------------------
    # 桌面宠物
    # ------------------------------------------------------------------
    def _show_pet(self) -> None:
        if self.pet is None:
            self.pet = DesktopPet(self.settings)
        if not self._privacy_active:
            self.pet.show()
        self._pet_visible = True
        self.settings.set("pet_visible", True)
        self._update_menu_states()

    def _hide_pet(self) -> None:
        if self.pet:
            self.pet.hide()
        self._pet_visible = False
        self.settings.set("pet_visible", False)
        self._update_menu_states()

    def _toggle_pet(self) -> None:
        if self._pet_visible:
            self._hide_pet()
        else:
            self._show_pet()

    # ------------------------------------------------------------------
    # 隐私屏
    # ------------------------------------------------------------------
    def _activate_privacy(self) -> None:
        """开启隐私屏：先弹密码框验证，成功后显示全屏遮罩。"""
        if self._privacy_active or getattr(self, "_auth_busy", False):
            return
        self._auth_busy = True
        dlg = PasswordDialog(self.settings, None, title="开启隐私屏")
        dlg.activateWindow()
        dlg.raise_()
        result = dlg.exec()
        self._auth_busy = False
        if result == PasswordDialog.DialogCode.Accepted:
            self._do_activate_privacy()

    def _deactivate_privacy(self) -> None:
        """关闭隐私屏：触发隐私屏内嵌解锁面板。"""
        if not self._privacy_active:
            return
        if self.privacy:
            self.privacy.request_unlock()
        else:
            dlg = PasswordDialog(self.settings, None, title="关闭隐私屏")
            dlg.activateWindow()
            dlg.raise_()
            if dlg.exec() == PasswordDialog.DialogCode.Accepted:
                self._do_deactivate_privacy()

    def _do_activate_privacy(self) -> None:
        if self.privacy is None:
            self.privacy = PrivacyScreen(self.settings)
            self.privacy.unlock_accepted.connect(self._on_privacy_unlock)
            self.privacy.unlock_rejected.connect(self._on_privacy_unlock_rejected)
            self.privacy.user_activity.connect(self._on_privacy_user_activity)
        if self.settings_window:
            self.settings_window.hide()
        self.privacy.reload_background()
        self.privacy.show()
        self.privacy.raise_()
        self.privacy.activateWindow()
        self.privacy.setFocus()
        self._privacy_active = True
        if self._pet_visible and self.pet:
            self.pet.on_privacy_screen_active(True)
            self.pet.hide()
        self._update_menu_states()

    def _do_deactivate_privacy(self) -> None:
        if self.privacy:
            self.privacy.close_unlock_panel()
            self.privacy.hide()
        self._privacy_active = False
        if self._pet_visible and self.pet:
            self.pet.on_privacy_screen_active(False)
            self.pet.show()
        self._update_menu_states()

    def _on_privacy_unlock(self) -> None:
        if self.pet:
            self.pet.on_privacy_password_ok()
        self._do_deactivate_privacy()

    def _on_privacy_unlock_rejected(self, consecutive: int = 1) -> None:
        if self.pet:
            self.pet.on_privacy_password_wrong(consecutive)
        if self.privacy and self._privacy_active:
            self.privacy.raise_()

    def _on_privacy_user_activity(self) -> None:
        if self.pet and self._pet_visible:
            self.pet.on_user_activity()

    def _toggle_privacy(self) -> None:
        if self._privacy_active:
            self._deactivate_privacy()
        else:
            self._activate_privacy()

    # ------------------------------------------------------------------
    # 密码 / 设置面板
    # ------------------------------------------------------------------
    def _change_password(self) -> None:
        """托盘菜单修改密码；对话框要求当前密码与两次新密码。"""
        if self._privacy_active:
            self._deactivate_privacy()
            return
        dlg = PasswordDialog(self.settings, None, edit_mode=True)
        dlg.activateWindow()
        dlg.raise_()
        dlg.exec()

    def _open_settings(self) -> None:
        """打开独立设置窗口（两个页面：宠物 / 隐私屏）。"""
        if self._privacy_active:
            self._deactivate_privacy()
            return
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self.settings)
            self.settings_window.quit_requested.connect(self._quit)
            self.settings_window.settings_applied.connect(self._apply_settings_to_widgets)
            self.settings_window.pet_action.connect(self._pet_action)
        sw = self.settings_window
        sw._load_values()
        sw.showNormal()
        sw.raise_()
        sw.activateWindow()
        sw.setFocus()

    def _apply_settings_to_widgets(self) -> None:
        """把当前设置应用到各组件（设置面板保存后调用）。"""
        if self.pet:
            self.pet.set_pet_type(self.settings.pet_type)
            self.pet.set_pet_size(int(self.settings.pet_size))
            self.pet.set_wander(bool(self.settings.pet_wander))
            self.pet.set_clothing(str(self.settings.get("pet_clothing", "none")))
            self.pet.set_mood(str(self.settings.get("pet_mood", "happy")))
        visible = self.settings.get("pet_visible", True)
        if visible != self._pet_visible:
            self._show_pet() if visible else self._hide_pet()
        self.apply_theme_to_app(self.app)
        if self.privacy:
            self.privacy.reload_background()
        if self.settings_window:
            self.settings_window._apply_theme_style()

    # ------------------------------------------------------------------
    # 退出
    # ------------------------------------------------------------------
    def _pet_action(self, action):
        if self._privacy_active:
            return
        self._show_pet()
        self.pet.perform_action(action)

    def _on_tray_unavailable(self, message):
        # Do not leave a tray-only application inaccessible when Explorer is unavailable.
        if not self._privacy_active:
            self._open_settings()
            self.settings_window.status.setText(message)

    def cleanup(self) -> None:
        """退出前清理组件。全程 try/except 兜底，避免僵尸进程占住单实例锁。"""
        if isinstance(self.tray, WindowsTray):
            self.tray.dispose()
        else:
            self.tray.hide()
        for win in (self.pet, self.privacy):
            if win is not None:
                try:
                    win.close()
                except Exception:
                    pass

    def apply_theme_to_app(self, app: QApplication) -> None:
        """把当前主题风格应用到整个应用（窗口/对话框）。"""
        try:
            from widgets.theme import Theme
            theme_key = str(self.settings.get("ui_theme", "dark"))
            style = Theme(theme_key).style()
            app.setStyleSheet(style)
        except Exception:
            pass

    def activate(self) -> None:
        """把已有实例唤到前台（第二个实例启动时调用）。"""
        try:
            if self._privacy_active:
                self.privacy.request_unlock()
            elif self.pet:
                self.pet.raise_to_top()
            self.tray.show()
            if not self._privacy_active:
                self._open_settings()
        except Exception:
            pass

    def _quit(self) -> None:
        """隐私屏开启时必须验证密码；解锁后才允许退出。"""
        if self._privacy_active or (self.settings.has_password and self.settings.get("password_protect_quit", True)):
            dlg = PasswordDialog(self.settings, None, title="退出程序")
            dlg.activateWindow()
            dlg.raise_()
            if dlg.exec() != PasswordDialog.DialogCode.Accepted:
                return
        self.settings.set("pet_visible", self._pet_visible)
        if self.privacy:
            self._privacy_active = False
            self.privacy.hide()
            self.privacy.deleteLater()
            self.privacy = None
        self.tray.hide()
        self.app.quit()

    def prepare_setup_exit(self) -> bool:
        """Allow repair to close an unprotected instance, preserving password gates."""
        if getattr(self, "_auth_busy", False):
            return False
        if self._privacy_active or (
                self.settings.has_password and self.settings.get("password_protect_quit", True)):
            self.activate()
            return False
        self.settings.set("pet_visible", self._pet_visible)
        return True

    # ------------------------------------------------------------------
    # 托盘菜单
    # ------------------------------------------------------------------
    def _build_tray_menu(self) -> None:
        menu = QMenu()

        self.act_pet = QAction("显示 / 隐藏 桌面宠物", menu)
        self.act_pet.setCheckable(True)
        self.act_pet.triggered.connect(self._toggle_pet)
        menu.addAction(self.act_pet)

        menu.addSeparator()

        self.act_privacy_on = QAction("开启隐私屏（需密码）", menu)
        self.act_privacy_on.triggered.connect(self._activate_privacy)
        menu.addAction(self.act_privacy_on)

        self.act_privacy_off = QAction("关闭隐私屏（需密码）", menu)
        self.act_privacy_off.triggered.connect(self._deactivate_privacy)
        menu.addAction(self.act_privacy_off)

        menu.addSeparator()

        self.act_settings = QAction("⚙ 设置", menu)
        self.act_settings.triggered.connect(self._open_settings)
        menu.addAction(self.act_settings)

        self.act_pwd = QAction("修改密码", menu)
        self.act_pwd.triggered.connect(self._change_password)
        menu.addAction(self.act_pwd)

        menu.addSeparator()

        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self._menu = menu
        self._update_menu_states()

    def _update_menu_states(self) -> None:
        self.act_pet.setChecked(self._pet_visible)
        self.act_pet.setEnabled(not self._privacy_active)
        self.act_settings.setEnabled(not self._privacy_active)
        self.act_pwd.setEnabled(not self._privacy_active)
        self.act_privacy_on.setEnabled(not self._privacy_active)
        self.act_privacy_off.setEnabled(self._privacy_active)
