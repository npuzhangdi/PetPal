"""Two-page settings window; draft values are committed only on Apply."""
from pathlib import Path
import hashlib
import shutil
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QComboBox, QCheckBox, QPushButton, QSlider, QTabWidget, QScrollArea,
    QColorDialog, QFileDialog, QFrame)
from core.settings import SettingsManager
from widgets.theme import Theme, THEMES
from widgets.password_dialog import PasswordDialog
from widgets.rounded import round_window

PETS = {"cat": "小猫", "dog": "小狗", "cow": "小牛", "rabbit": "小兔"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


class SettingsWindow(QWidget):
    settings_applied = Signal()
    pet_action = Signal(str)
    quit_requested = Signal()

    def __init__(self, settings: SettingsManager, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("PetPal · 设置")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setMinimumWidth(540)
        self.resize(600, min(720, self.screen().availableGeometry().height() - 48))
        self._drag_pos = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 20)
        outer.setSpacing(16)
        header = QHBoxLayout()
        mark = QLabel()
        mark.setPixmap(QPixmap(str(settings.root / "assets" / "petpal_icon.png")).scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        header.addWidget(mark)
        title = QLabel("PetPal")
        title.setStyleSheet("font-size:24px; font-weight:700; background:transparent")
        header.addWidget(title, 1)
        self.theme_combo = QComboBox()
        for key, theme in THEMES.items():
            self.theme_combo.addItem(theme["name"], key)
        self.theme_combo.currentIndexChanged.connect(self._apply_theme_style)
        header.addWidget(self.theme_combo)
        close = QPushButton("×")
        close.setFixedSize(34, 34)
        close.clicked.connect(self.hide)
        header.addWidget(close)
        outer.addLayout(header)
        subtitle = QLabel("你的桌面伙伴，也为你的屏幕留一点私人空间。")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._scroll(self._pet_page()), "桌面宠物")
        self.tabs.addTab(self._scroll(self._privacy_page()), "隐私屏")
        outer.addWidget(self.tabs, 1)
        self.status = QLabel("设置只保存在此软件目录")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        footer = QHBoxLayout()
        self.quit_button = QPushButton("退出程序")
        self.quit_button.clicked.connect(lambda: self.quit_requested.emit())
        footer.addWidget(self.quit_button)
        footer.addStretch()
        for text, action in (("取消", self.hide), ("应用", self.apply_only), ("保存并关闭", self._accept)):
            button = QPushButton(text)
            button.clicked.connect(action)
            footer.addWidget(button)
        outer.addLayout(footer)
        self._load_values()
        self.move(self.screen().availableGeometry().center() - self.rect().center())

    def _scroll(self, content):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        return scroll

    def _page(self):
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(20, 20, 20, 20)
        form.setSpacing(14)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        return page, form

    def _pet_page(self):
        page, form = self._page()
        self.pet_preview = QLabel()
        self.pet_preview.setAlignment(Qt.AlignCenter)
        self.pet_preview.setMinimumHeight(112)
        form.addRow(self.pet_preview)
        self.pet_type = QComboBox()
        for key, name in PETS.items():
            self.pet_type.addItem(name, key)
        self.pet_type.currentIndexChanged.connect(self._update_pet_preview)
        form.addRow("选择伙伴", self.pet_type)
        self.pet_size = QSlider(Qt.Horizontal)
        self.pet_size.setRange(80, 260)
        self.size_label = QLabel()
        row = QHBoxLayout()
        row.addWidget(self.pet_size, 1)
        row.addWidget(self.size_label)
        self.pet_size.valueChanged.connect(lambda n: self.size_label.setText(f"{n} px"))
        form.addRow("显示大小", row)
        self.pet_visible = QCheckBox("显示桌宠，下次启动时恢复此状态")
        self.pet_wander = QCheckBox("自由走动（关闭后仍可吃饭、玩耍和睡觉）")
        form.addRow(self.pet_visible)
        form.addRow(self.pet_wander)
        self.pet_clothing = QComboBox()
        for key, name in (("none", "自然装扮"), ("hat", "小帽子"), ("scarf", "暖围巾"), ("bow", "蝴蝶结")):
            self.pet_clothing.addItem(name, key)
        form.addRow("穿搭", self.pet_clothing)
        self.pet_mood = QComboBox()
        for key, name in (("happy", "开心"), ("cool", "酷酷的"), ("party", "派对")):
            self.pet_mood.addItem(name, key)
        form.addRow("心情", self.pet_mood)
        actions = QHBoxLayout()
        for text, key in (("喂饭", "eat"), ("玩球", "play"), ("睡觉", "sleep"), ("回窝", "home")):
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, k=key: self.pet_action.emit(k))
            actions.addWidget(button)
        form.addRow("陪它一会儿", actions)
        note = QLabel("拖动桌宠可改变位置，右键可切换伙伴与穿搭。隐私屏开启时，桌宠会暂时休息。")
        note.setWordWrap(True)
        form.addRow(note)
        return page

    def _privacy_page(self):
        page, form = self._page()
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFixedHeight(140)
        form.addRow(self.preview_label)
        self.privacy_bg_type = QComboBox()
        self.privacy_bg_type.addItem("纯色背景", "color")
        self.privacy_bg_type.addItem("图片背景", "image")
        self.privacy_bg_type.currentIndexChanged.connect(self._sync_bg_rows)
        form.addRow("背景方式", self.privacy_bg_type)
        self.color_button = QPushButton("选择颜色")
        self.color_button.clicked.connect(self._on_pick_color)
        form.addRow("纯色", self.color_button)
        self.gallery = QComboBox()
        self.gallery.currentIndexChanged.connect(self._gallery_changed)
        self.import_button = QPushButton("导入图片…")
        self.import_button.clicked.connect(self._on_pick_image)
        row = QHBoxLayout()
        row.addWidget(self.gallery, 1)
        row.addWidget(self.import_button)
        form.addRow("图片库", row)
        self.image_name = QLabel()
        self.image_name.setWordWrap(True)
        form.addRow(self.image_name)
        note = QLabel("自动覆盖所有显示器。开启和关闭均需密码；点击屏幕即可解锁，Esc 仅收起密码面板。")
        note.setWordWrap(True)
        form.addRow(note)
        self.password_button = QPushButton("修改隐私密码")
        self.password_button.clicked.connect(self._change_password)
        form.addRow("密码管理", self.password_button)
        self.protect_quit = QCheckBox("平时退出也验证密码（隐私屏开启时始终验证）")
        form.addRow(self.protect_quit)
        return page

    def _choose(self, combo, value):
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def _load_values(self):
        s = self.settings
        self._color = s.privacy_color
        self._image_path = s.privacy_image
        self._choose(self.theme_combo, s.get("ui_theme"))
        for key in ("pet_type", "pet_clothing", "pet_mood", "privacy_bg_type"):
            self._choose(getattr(self, key), s.get(key))
        self.pet_size.setValue(s.pet_size)
        self.pet_visible.setChecked(s.get("pet_visible"))
        self.pet_wander.setChecked(s.pet_wander)
        self.protect_quit.setChecked(s.get("password_protect_quit"))
        self.password_button.setText("修改隐私密码" if s.has_password else "设置隐私密码")
        self.gallery.blockSignals(True)
        self.gallery.clear()
        self.gallery.addItem("选择 photos/ 中的图片", "")
        photos = s.root / "photos"
        if photos.exists():
            for path in sorted(photos.iterdir()):
                if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
                    self.gallery.addItem(path.name, str(path))
        self._choose(self.gallery, self._image_path)
        self.gallery.blockSignals(False)
        self.status.setText("设置只保存在此软件目录")
        self._sync_bg_rows()
        self._apply_theme_style()
        self._update_pet_preview()

    def _apply_theme_style(self, *args):
        self.setStyleSheet(Theme(self.theme_combo.currentData() or "dark").style())

    def _update_pet_preview(self, *args):
        key = self.pet_type.currentData() or "cat"
        image = QPixmap(str(self.settings.root / "assets" / "pets" / key / "standing.png"))
        self.pet_preview.setPixmap(image.scaled(120, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _sync_bg_rows(self, *args):
        if not hasattr(self, "gallery"):
            return
        image_mode = self.privacy_bg_type.currentData() == "image"
        self.color_button.setEnabled(not image_mode)
        self.gallery.setEnabled(image_mode)
        self.import_button.setEnabled(image_mode)
        self._update_preview()

    def _update_preview(self):
        if not hasattr(self, "_color"):
            return
        self.color_button.setText("选择颜色 · " + self._color.upper())
        self.image_name.setText(Path(self._image_path).name if self._image_path else "导入的图片会复制到 photos/，便于移动软件。")
        self.preview_label.clear()
        if self.privacy_bg_type.currentData() == "image" and self._image_path:
            image = QPixmap(self._image_path)
            self.preview_label.setPixmap(image.scaled(390, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.preview_label.setText("隐私屏背景预览")
        self.preview_label.setStyleSheet(f"background:{self._color}; color:white; border-radius:12px;")

    def _on_pick_color(self):
        color = QColorDialog.getColor(QColor(self._color), self, "选择隐私屏颜色")
        if color.isValid():
            self._color = color.name()
            self._update_preview()

    def _on_pick_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入隐私屏图片", str(self.settings.root / "photos"), "图片 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            if QPixmap(path).isNull():
                self.status.setText("无法读取这张图片，请选择其他图片")
                return
            self._image_path = path
            self._update_preview()

    def _gallery_changed(self, *args):
        path = self.gallery.currentData()
        if path:
            self._image_path = path
            self._update_preview()

    def _change_password(self):
        dialog = PasswordDialog(self.settings, self, edit_mode=True)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self.password_button.setText("修改隐私密码")
            self.status.setText("隐私密码已更新")

    def _collect(self):
        if self.privacy_bg_type.currentData() == "image" and (not self._image_path or QPixmap(self._image_path).isNull()):
            self.status.setText("请先选择一张可读取的背景图片")
            return False
        try:
            image_value = ""
            if self._image_path and Path(self._image_path).is_file():
                source = Path(self._image_path).resolve()
                photos = self.settings.root / "photos"
                photos.mkdir(exist_ok=True)
                if source.parent != photos.resolve():
                    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
                    target = photos / ("import_" + digest + source.suffix.lower())
                    if not target.exists():
                        shutil.copy2(source, target)
                    source = target
                image_value = source.relative_to(self.settings.root).as_posix()
            self.settings.update({
                "pet_type": self.pet_type.currentData(), "pet_size": self.pet_size.value(),
                "pet_visible": self.pet_visible.isChecked(), "pet_wander": self.pet_wander.isChecked(),
                "pet_clothing": self.pet_clothing.currentData(), "pet_mood": self.pet_mood.currentData(),
                "ui_theme": self.theme_combo.currentData(), "privacy_bg_type": self.privacy_bg_type.currentData(),
                "privacy_color": self._color, "privacy_image": image_value,
                "password_protect_quit": self.protect_quit.isChecked(),
            })
        except (OSError, ValueError) as exc:
            self.status.setText("保存失败：" + str(exc))
            return False
        self.settings_applied.emit()
        self.status.setText("设置已保存并应用")
        return True

    def apply_only(self):
        self._collect()

    def _accept(self):
        if self._collect():
            self.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        round_window(self)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() < 72:
            self._drag_pos = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
