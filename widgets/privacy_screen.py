"""One opaque cover per monitor, managed as a single password-protected session."""
import time
from PySide6.QtCore import QObject, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton
from widgets.theme import Theme
from core.settings import default_privacy_image


class ScreenCover(QWidget):
    def __init__(self, manager, screen):
        super().__init__()
        self.manager = manager
        self.target_screen = screen
        self.setWindowTitle("PetPal · 隐私屏")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        # Deliberately opaque: every monitor, including taskbar, must be covered.
        self.setFocusPolicy(Qt.StrongFocus)
        self.setGeometry(screen.geometry())
        screen.geometryChanged.connect(self.setGeometry)
        self.panel = QFrame(self)
        self.panel.setObjectName("card")
        self.panel.setFixedWidth(360)
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        title = QLabel("解锁隐私屏")
        title.setStyleSheet("font-size:22px; font-weight:600; background:transparent")
        layout.addWidget(title)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setMaxLength(128)
        self.password.setPlaceholderText("输入隐私密码")
        self.password.returnPressed.connect(self.submit)
        layout.addWidget(self.password)
        self.hint = QLabel("按 Esc 收起，隐私屏继续保持开启")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        buttons = QHBoxLayout()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.panel.hide)
        self.unlock = QPushButton("解锁")
        self.unlock.clicked.connect(self.submit)
        buttons.addWidget(cancel)
        buttons.addWidget(self.unlock)
        layout.addLayout(buttons)
        self.panel.hide()
        self._refresh = QTimer(self)
        self._refresh.setInterval(200)
        self._refresh.timeout.connect(self.update_cooldown)
        self._refresh.start()
        self.reload_background()

    def reload_background(self):
        settings = self.manager.settings
        self.setStyleSheet(Theme(settings.get("ui_theme")).style())
        self._color = QColor(settings.privacy_color)
        path = settings.privacy_image or default_privacy_image()
        self._image = QPixmap(path) if settings.privacy_bg_type == "image" else QPixmap()
        self._scaled = QPixmap()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scaled = QPixmap()
        self.center_panel()

    def center_panel(self):
        self.panel.adjustSize()
        self.panel.move((self.width() - self.panel.width()) // 2, (self.height() - self.panel.height()) // 2)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._color)
        if not self._image.isNull():
            if self._scaled.isNull():
                self._scaled = self._image.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            painter.drawPixmap((self.width() - self._scaled.width()) // 2, (self.height() - self._scaled.height()) // 2, self._scaled)
        painter.end()

    def open_panel(self):
        for cover in self.manager.covers:
            cover.panel.hide()
            cover.password.clear()
        self.panel.show()
        self.center_panel()
        self.panel.raise_()
        self.raise_()
        self.activateWindow()
        self.password.setFocus()
        self.update_cooldown()

    def update_cooldown(self):
        remaining = max(0, self.manager.blocked_until - time.monotonic())
        self.unlock.setEnabled(remaining == 0)
        if remaining:
            self.hint.setText(f"密码错误，{int(remaining) + 1} 秒后可重试")
        elif self.manager.failures:
            self.hint.setText("请重新输入密码 · Esc 收起")

    def submit(self):
        if time.monotonic() < self.manager.blocked_until:
            return
        if self.manager.settings.verify_password(self.password.text()):
            self.password.clear()
            self.manager.unlock_accepted.emit()
        else:
            self.manager.failures += 1
            self.manager.blocked_until = time.monotonic() + min(10, self.manager.failures)
            self.password.clear()
            self.update_cooldown()
            self.manager.unlock_rejected.emit(self.manager.failures)

    def mousePressEvent(self, event):
        self.manager.user_activity.emit()
        self.open_panel()

    def keyPressEvent(self, event):
        self.manager.user_activity.emit()
        if event.key() == Qt.Key_Escape:
            self.panel.hide()
        elif not self.panel.isVisible():
            self.open_panel()

    def _restore_cover(self):
        if self.manager.active and self in self.manager.covers:
            self.showNormal()
            self.setGeometry(self.target_screen.geometry())
            self.raise_()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.manager.active and self in self.manager.covers:
            QTimer.singleShot(0, self._restore_cover)

    def changeEvent(self, event):
        super().changeEvent(event)
        if self.isMinimized() and self.manager.active:
            QTimer.singleShot(0, self._restore_cover)

    def closeEvent(self, event):
        event.ignore()
        self.open_panel()


class PrivacyScreen(QObject):
    unlock_accepted = Signal()
    unlock_rejected = Signal(int)
    user_activity = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.covers = []
        self.active = False
        self.failures = 0
        self.blocked_until = 0
        app = QApplication.instance()
        app.screenAdded.connect(self._screen_added)
        app.screenRemoved.connect(self._screen_removed)

    def _screen_added(self, screen):
        if self.active:
            cover = ScreenCover(self, screen)
            self.covers.append(cover)
            cover.show()
            cover.raise_()

    def _screen_removed(self, screen):
        for cover in list(self.covers):
            if cover.target_screen is screen:
                self.covers.remove(cover)
                cover.hide()
                cover.deleteLater()

    def show(self):
        if self.active:
            return
        if not self.settings.has_password:
            raise RuntimeError("请先设置隐私密码")
        self.active = True
        self.failures = 0
        self.blocked_until = 0
        for screen in QApplication.screens():
            self._screen_added(screen)
        self.raise_()

    def hide(self):
        self.active = False
        for cover in self.covers:
            cover.password.clear()
            cover.hide()
            cover.deleteLater()
        self.covers.clear()

    def reload_background(self):
        for cover in self.covers:
            cover.reload_background()

    def raise_(self):
        for cover in self.covers:
            cover.raise_()

    def request_unlock(self):
        from PySide6.QtGui import QCursor
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        cover = next((c for c in self.covers if c.target_screen is screen), None)
        if cover:
            cover.open_panel()

    def close_unlock_panel(self):
        for cover in self.covers:
            cover.panel.hide()

    def activateWindow(self):
        if self.covers:
            self.covers[0].activateWindow()

    def setFocus(self):
        if self.covers:
            self.covers[0].setFocus()

    def close(self):
        self.hide()
