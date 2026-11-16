"""Two-page settings window; draft values are committed only on Apply."""
from pathlib import Path
import hashlib
import tempfile
from PySide6.QtCore import QSize, Qt, Signal, Slot
from PySide6.QtGui import QColor, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QComboBox, QCheckBox, QPushButton, QSlider, QTabWidget, QScrollArea,
    QColorDialog, QFileDialog, QFrame)
from core.settings import SettingsManager
from widgets.theme import Theme, THEMES
from widgets.password_dialog import PasswordDialog
from widgets.rounded import round_window
from widgets.background_task import BackgroundTask

PETS = {"cat": "小猫", "dog": "小狗", "cow": "小牛", "rabbit": "小兔"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def _read_preview(path):
    reader = QImageReader(path)
    size = reader.size()
    if size.isValid():
        reader.setScaledSize(size.scaled(QSize(390, 140), Qt.KeepAspectRatio))
    return reader.read()


def _store_background(path, root, image_mode):
    source = Path(path).resolve()
    if not source.is_file():
        if image_mode:
            raise ValueError("请先选择一张可读取的背景图片")
        return ""
    photos = root / "photos"
    photos.mkdir(exist_ok=True)
    if source.parent == photos.resolve():
        if image_mode and _read_preview(str(source)).isNull():
            raise ValueError("请先选择一张可读取的背景图片")
        return source.relative_to(root).as_posix()

    digest = hashlib.sha256()
    temporary = tempfile.NamedTemporaryFile(dir=photos, suffix=".tmp", delete=False)
    staged = Path(temporary.name)
    try:
        with temporary, source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                temporary.write(chunk)
        if image_mode and _read_preview(str(staged)).isNull():
            raise ValueError("请先选择一张可读取的背景图片")
        target = photos / ("import_" + digest.hexdigest()[:12] + source.suffix.lower())
        if not target.exists():
            staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)
    return target.relative_to(root).as_posix()


class SettingsWindow(QWidget):
    settings_applied = Signal()
    privacy_requested = Signal()
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
        self._preview_task = None
        self._loaded_preview_path = ""
        self._preview_image = QPixmap()
        self._save_task = None
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
        self.tabs = QTabWidget()
        self.tabs.addTab(self._scroll(self._privacy_page()), "隐私屏")
        self.tabs.addTab(self._scroll(self._pet_page()), "桌面宠物")
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
        self.pet_enabled = QCheckBox("启用桌面宠物")
        self.pet_enabled.toggled.connect(self._sync_pet_controls)
        form.addRow(self.pet_enabled)
        self.pet_controls, form = self._page()
        page.layout().addRow(self.pet_controls)
        form.setContentsMargins(0, 0, 0, 0)
        self.pet_preview = QLabel()
        self.pet_preview.setAlignment(Qt.AlignCenter)
        self.pet_preview.setMinimumHeight(112)
        form.addRow(self.pet_preview)
        self.pet_type = QComboBox()
        self._pet_previews = {}
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
        self.pet_actions = QWidget()
        actions = QHBoxLayout(self.pet_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        for text, key in (("喂饭", "eat"), ("玩球", "play"), ("睡觉", "sleep"), ("回窝", "home")):
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, k=key: self.pet_action.emit(k))
            actions.addWidget(button)
        form.addRow("陪它一会儿", self.pet_actions)
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
        self.privacy_button = QPushButton("开启隐私屏")
        self.privacy_button.clicked.connect(self.privacy_requested.emit)
        form.addRow(self.privacy_button)
        self.protect_quit = QCheckBox("平时退出也验证密码（隐私屏开启时始终验证）")
        form.addRow(self.protect_quit)
        return page

    def _choose(self, combo, value):
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def _load_values(self):
        if self._save_task is not None:
            return
        s = self.settings
        self._color = s.privacy_color
        self._image_path = s.privacy_image
        self._choose(self.theme_combo, s.get("ui_theme"))
        for key in ("pet_type", "pet_clothing", "pet_mood", "privacy_bg_type"):
            self._choose(getattr(self, key), s.get(key))
        self.pet_size.setValue(s.pet_size)
        enabled = s.get("pet_enabled")
        self.pet_enabled.setChecked(enabled)
        self.pet_visible.setChecked(s.get("pet_visible") if enabled else True)
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
        self._sync_pet_controls()

    def _apply_theme_style(self, *args):
        style = Theme(self.theme_combo.currentData() or "dark").style()
        if self.styleSheet() != style:
            self.setStyleSheet(style)

    def _sync_pet_controls(self, *args):
        enabled = self.pet_enabled.isChecked()
        self.pet_controls.setVisible(enabled)
        self.pet_actions.setEnabled(self.settings.get("pet_enabled"))
        if enabled:
            self._update_pet_preview()

    def _update_pet_preview(self, *args):
        if not self.pet_enabled.isChecked():
            return
        key = self.pet_type.currentData() or "cat"
        if key not in self._pet_previews:
            image = QPixmap(str(self.settings.root / "assets" / "pets" / key / "standing.png"))
            self._pet_previews[key] = image.scaled(120, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.pet_preview.setPixmap(self._pet_previews[key])

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
        if self.privacy_bg_type.currentData() == "image" and self._image_path:
            path = self._image_path
            if path == self._loaded_preview_path:
                if self._preview_image.isNull():
                    self.preview_label.setText("无法读取这张图片")
                else:
                    self.preview_label.setPixmap(self._preview_image)
            else:
                self.preview_label.setText("正在加载图片…")
                if self._preview_task is None:
                    self._loading_preview_path = path
                    self._preview_task = BackgroundTask(lambda: _read_preview(path), self._preview_loaded)
        else:
            self.preview_label.setText("隐私屏背景预览")
        style = f"background:{self._color}; color:white; border-radius:12px;"
        if self.preview_label.styleSheet() != style:
            self.preview_label.setStyleSheet(style)

    @Slot(object, object)
    def _preview_loaded(self, task, image):
        self._preview_task = None
        self._loaded_preview_path = self._loading_preview_path
        self._preview_image = QPixmap.fromImage(image) if isinstance(image, QImage) else QPixmap()
        self._update_preview()

    def _on_pick_color(self):
        color = QColorDialog.getColor(QColor(self._color), self, "选择隐私屏颜色")
        if color.isValid():
            self._color = color.name()
            self._update_preview()

    def _on_pick_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入隐私屏图片", str(self.settings.root / "photos"), "图片 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self._loaded_preview_path = ""
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

    def _collect(self, close_after=False):
        if self._save_task is not None:
            return False
        bg_type = self.privacy_bg_type.currentData()
        if bg_type == "image" and not self._image_path:
            self.status.setText("请先选择一张可读取的背景图片")
            return False
        values = {
            "pet_type": self.pet_type.currentData(), "pet_size": self.pet_size.value(),
            "pet_enabled": self.pet_enabled.isChecked(), "pet_visible": self.pet_visible.isChecked(),
            "pet_wander": self.pet_wander.isChecked(),
            "pet_clothing": self.pet_clothing.currentData(), "pet_mood": self.pet_mood.currentData(),
            "ui_theme": self.theme_combo.currentData(), "privacy_bg_type": bg_type,
            "privacy_color": self._color, "privacy_image": self.settings.get("privacy_image"),
            "password_protect_quit": self.protect_quit.isChecked(),
        }
        path, root = self._image_path, self.settings.root
        if not path:
            values["privacy_image"] = ""
            return self._commit(values, close_after)
        if path == self.settings.privacy_image and (
                bg_type == "color" or bg_type == self.settings.privacy_bg_type):
            return self._commit(values, close_after)
        self._pending_save = values, close_after
        self.status.setText("正在检查并保存背景图片…")
        self.setEnabled(False)
        self._save_task = BackgroundTask(
            lambda: _store_background(path, root, bg_type == "image"), self._background_stored)
        return True

    @Slot(object, object)
    def _background_stored(self, task, result):
        self._save_task = None
        self.setEnabled(True)
        values, close_after = self._pending_save
        if isinstance(result, Exception):
            self.status.setText("保存失败：" + str(result))
            return
        values["privacy_image"] = result
        if self._commit(values, close_after):
            if self._loaded_preview_path == self._image_path:
                self._loaded_preview_path = self.settings.privacy_image
            self._image_path = self.settings.privacy_image
            if self._image_path and self.gallery.findData(self._image_path) == -1:
                self.gallery.addItem(Path(self._image_path).name, self._image_path)
            self._choose(self.gallery, self._image_path)
            self._update_preview()

    def _commit(self, values, close_after):
        try:
            self.settings.update(values)
        except (OSError, ValueError) as exc:
            self.status.setText("保存失败：" + str(exc))
            return False
        self.settings_applied.emit()
        self._sync_pet_controls()
        self.status.setText("设置已保存并应用")
        if close_after:
            self.hide()
        return True

    def apply_only(self):
        self._collect()

    def _accept(self):
        self._collect(close_after=True)

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
