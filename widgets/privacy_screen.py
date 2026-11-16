"""One opaque cover per monitor, managed as a single password-protected session."""
import time
from PySide6.QtCore import QObject, Qt, Signal, QTimer, Slot
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton
from widgets.theme import Theme
from widgets.background_task import BackgroundTask
from core.settings import default_privacy_image


class ScreenCover(QWidget):
    def __init__(self, manager, screen):
        super().__init__()
        self.manager = manager
        self.target_screen = screen
        self.setWindowTitle("PetPal · 隐私屏")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        # Deliberately opaque: every monitor, including taskbar, must be covered.
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setFocusPolicy(Qt.StrongFocus)
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
        cancel.clicked.connect(self.close_panel)
        self.unlock = QPushButton("解锁")
        self.unlock.clicked.connect(self.submit)
        buttons.addWidget(cancel)
        buttons.addWidget(self.unlock)
        layout.addLayout(buttons)
        self.panel.hide()
        self._refresh = QTimer(self)
        self._refresh.setInterval(200)
        self._refresh.timeout.connect(self.update_cooldown)
        settings = self.manager.settings
        self.setStyleSheet(Theme(settings.get("ui_theme")).style())
        self._color = QColor(settings.privacy_color)
        self._background = QImage()
        self._render_task = None
        self._render_size = None
        self.setGeometry(screen.geometry())
        screen.geometryChanged.connect(self.setGeometry)

    def render_background(self):
        image = self.manager.image
        size = self.size() * self.devicePixelRatioF()
        if (image.isNull() or size.isEmpty() or self._render_task is not None
                or size == self._render_size):
            return
        self._render_size = size

        def render():
            # Crop to this monitor's aspect ratio before allocating the scaled image.
            width, height = image.width(), image.height()
            crop_width = min(width, max(1, height * size.width() // size.height()))
            crop_height = min(height, max(1, width * size.height() // size.width()))
            source = image
            if crop_width != width or crop_height != height:
                source = image.copy((width - crop_width) // 2, (height - crop_height) // 2,
                                    crop_width, crop_height)
            return source.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

        self._render_task = BackgroundTask(render, self._background_rendered)

    @Slot(object, object)
    def _background_rendered(self, task, image):
        self._render_task = None
        if not self.manager.active or self not in self.manager.covers:
            return
        if isinstance(image, QImage) and not image.isNull():
            self._background = image
            self.update()
        self.render_background()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.center_panel()
        self.render_background()

    def center_panel(self):
        self.panel.adjustSize()
        self.panel.move((self.width() - self.panel.width()) // 2, (self.height() - self.panel.height()) // 2)

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._background.isNull() or self._background.hasAlphaChannel():
            painter.fillRect(event.rect(), self._color)
        if not self._background.isNull():
            painter.drawImage(self.rect(), self._background)
        painter.end()

    def open_panel(self):
        for cover in self.manager.covers:
            cover.close_panel()
        self.panel.show()
        self.center_panel()
        self.panel.raise_()
        self.raise_()
        self.activateWindow()
        self.password.setFocus()
        self.update_cooldown()

    def close_panel(self):
        self._refresh.stop()
        self.password.clear()
        self.panel.hide()

    def update_cooldown(self):
        remaining = max(0, self.manager.blocked_until - time.monotonic())
        self.password.setReadOnly(self.manager.verifying)
        self.unlock.setEnabled(not remaining and not self.manager.verifying)
        if remaining and self.panel.isVisible():
            if not self._refresh.isActive():
                self._refresh.start()
        else:
            self._refresh.stop()
        if self.manager.verifying:
            self.hint.setText("正在验证密码…")
        elif remaining:
            self.hint.setText(f"密码错误，{int(remaining) + 1} 秒后可重试")
        elif self.manager.failures:
            self.hint.setText("请重新输入密码 · Esc 收起")
        else:
            self.hint.setText("按 Esc 收起，隐私屏继续保持开启")

    def submit(self):
        if self.manager.verify(self.password.text()):
            self.password.clear()

    def mousePressEvent(self, event):
        self.manager.user_activity.emit()
        self.open_panel()

    def keyPressEvent(self, event):
        self.manager.user_activity.emit()
        if event.key() == Qt.Key_Escape:
            self.close_panel()
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
        self.image = QImage()
        self._load_task = None
        self._verify_task = None
        app = QApplication.instance()
        app.screenAdded.connect(self._screen_added)
        app.screenRemoved.connect(self._screen_removed)

    def _screen_added(self, screen):
        if self.active:
            cover = ScreenCover(self, screen)
            self.covers.append(cover)
            cover.show()
            cover.raise_()
            cover.render_background()

    def _screen_removed(self, screen):
        for cover in list(self.covers):
            if cover.target_screen is screen:
                self.covers.remove(cover)
                cover.close_panel()
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
        if self.settings.privacy_bg_type == "image":
            path = self.settings.privacy_image or default_privacy_image()
            if path:
                self._load_task = BackgroundTask(lambda: QImage(path), self._background_loaded)

    def hide(self):
        self.active = False
        self._load_task = None
        self._verify_task = None
        self.image = QImage()
        for cover in self.covers:
            cover.close_panel()
            cover.hide()
            cover.deleteLater()
        self.covers.clear()

    @Slot(object, object)
    def _background_loaded(self, task, image):
        if task is not self._load_task:
            return
        self._load_task = None
        if not isinstance(image, QImage) or image.isNull():
            return
        self.image = image
        for cover in self.covers:
            cover.render_background()

    @property
    def verifying(self):
        return self._verify_task is not None

    def verify(self, password):
        if not self.active or self.verifying or time.monotonic() < self.blocked_until:
            return False
        settings = self.settings
        self._verify_task = BackgroundTask(lambda: settings.verify_password(password), self._password_checked)
        for cover in self.covers:
            if cover.panel.isVisible():
                cover.update_cooldown()
        return True

    @Slot(object, object)
    def _password_checked(self, task, accepted):
        if task is not self._verify_task:
            return
        self._verify_task = None
        if accepted is True:
            self.unlock_accepted.emit()
            return
        self.failures += 1
        self.blocked_until = time.monotonic() + min(10, self.failures)
        for cover in self.covers:
            if cover.panel.isVisible():
                cover.update_cooldown()
        self.unlock_rejected.emit(self.failures)

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
            cover.close_panel()

    def activateWindow(self):
        if self.covers:
            self.covers[0].activateWindow()

    def setFocus(self):
        if self.covers:
            self.covers[0].setFocus()

    def close(self):
        self.hide()
