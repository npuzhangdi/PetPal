"""Shared password challenge; password changes verify the old password."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton
from widgets.theme import Theme
from widgets.rounded import round_window


class PasswordDialog(QDialog):
    def __init__(self, settings, parent=None, edit_mode=False, title="请输入密码"):
        super().__init__(parent)
        self.settings = settings
        self._edit_mode = edit_mode or not settings.has_password
        self.setWindowTitle("PetPal · " + title)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(Theme(settings.get("ui_theme")).style())
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        heading = QLabel("设置隐私密码" if not settings.has_password else ("修改密码" if edit_mode else title))
        heading.setStyleSheet("font-size:20px; font-weight:600; background:transparent")
        layout.addWidget(heading)
        self._pwd_edit = None
        if settings.has_password:
            self._pwd_edit = self._field("当前密码" if self._edit_mode else "输入隐私密码", layout)
        if self._edit_mode:
            self._new_edit = self._field("新密码（4–128 个字符）", layout)
            self._confirm_edit = self._field("再次输入新密码", layout)
        self.error = QLabel("密码仅保存在本机，忘记后无法找回。" if self._edit_mode else "验证后继续")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        row = QHBoxLayout()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("保存密码" if self._edit_mode else "验证")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._on_accept)
        row.addWidget(cancel)
        row.addWidget(self.ok)
        layout.addLayout(row)
        self._attempts = 0
        self._cooling = False
        self._cooldown = QTimer(self)
        self._cooldown.setSingleShot(True)
        self._cooldown.timeout.connect(self._ready)

    def _field(self, hint, layout):
        field = QLineEdit()
        field.setEchoMode(QLineEdit.Password)
        field.setMaxLength(128)
        field.setPlaceholderText(hint)
        field.returnPressed.connect(self._on_accept)
        layout.addWidget(field)
        return field

    def _ready(self):
        self._cooling = False
        self.ok.setEnabled(True)
        self.error.setText("请重新输入密码")

    def _on_accept(self):
        if self._cooling:
            return
        if self.settings.has_password and not self.settings.verify_password(self._pwd_edit.text()):
            self._attempts += 1
            self._cooling = True
            self.ok.setEnabled(False)
            delay = min(10, self._attempts)
            self.error.setText(f"密码错误，{delay} 秒后可重试")
            self._pwd_edit.clear()
            self._pwd_edit.setFocus()
            self._cooldown.start(delay * 1000)
            return
        if self._edit_mode:
            value = self._new_edit.text()
            if not 4 <= len(value) <= 128:
                self.error.setText("密码须为 4–128 个字符")
                return
            if value != self._confirm_edit.text():
                self.error.setText("两次新密码不一致")
                return
            try:
                self.settings.set_password(value)
            except OSError:
                self.error.setText("保存失败，请检查软件目录是否可写")
                return
        self.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        round_window(self)

    def showEvent(self, event):
        super().showEvent(event)
        screen = self.screen().availableGeometry()
        self.move(screen.center() - self.rect().center())
        (self._pwd_edit if self._pwd_edit is not None else self._new_edit).setFocus()
        self.raise_()
        self.activateWindow()
