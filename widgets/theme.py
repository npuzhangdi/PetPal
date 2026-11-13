"""主题风格：深色系默认，支持浅/薄荷/海洋切换。

主题对象集中提供：
- style(qwidget): 控件样式表
- accent/background/foreground 颜色
- rounded window style
"""

from __future__ import annotations


THEMES = {
    "dark": {
        "name": "深色",
        "bg": "#111322",
        "card": "#1b1f31",
        "fg": "#e8f0ff",
        "accent": "#4FC3F7",
        "secondary": "#222944",
        "input": "#232a44",
        "border": "rgba(79,195,247,120)",
    },
    "light": {
        "name": "浅色",
        "bg": "#f6f8fc",
        "card": "#ffffff",
        "fg": "#202331",
        "accent": "#2c82c9",
        "secondary": "#e9eef7",
        "input": "#ffffff",
        "border": "rgba(44,130,201,120)",
    },
    "mint": {
        "name": "薄荷",
        "bg": "#eefaf5",
        "card": "#ffffff",
        "fg": "#183a34",
        "accent": "#13b7a3",
        "secondary": "#dff5ef",
        "input": "#ffffff",
        "border": "rgba(19,183,163,120)",
    },
    "ocean": {
        "name": "海洋",
        "bg": "#0d1726",
        "card": "#111f33",
        "fg": "#eaf6ff",
        "accent": "#38a8d8",
        "secondary": "#16293d",
        "input": "#101d2d",
        "border": "rgba(56,168,216,120)",
    },
}


class Theme:
    def __init__(self, key: str = "dark") -> None:
        if key not in THEMES:
            key = "dark"
        self.key = key
        self.data = THEMES[key]

    @property
    def name(self) -> str:
        return self.data["name"]

    def color(self, role: str) -> str:
        return self.data.get(role, self.data["bg"])

    def style(self) -> str:
        d = self.data
        return f"""
        QWidget {{
            background-color: {d['bg']};
            color: {d['fg']};
            font-size: 13px;
            font-family: "Microsoft YaHei UI", "Segoe UI";
            border-radius: 10px;
        }}
        QLabel {{
            background: transparent;
            color: {d['fg']};
        }}
        QPushButton {{
            background: {d['accent']};
            color: #ffffff;
            border: none;
            border-radius: 8px;
            padding: 7px 14px;
        }}
        QPushButton:hover {{ background: rgba(0,0,0,30); }}
        QPushButton:disabled {{ background: {d['secondary']}; color: rgba(255,255,255,120); }}
        QPushButton[rounded="true"] {{
            border-radius: 10px;
        }}
        QLineEdit, QComboBox {{
            background: {d['input']};
            color: {d['fg']};
            border: 1px solid {d['border']};
            border-radius: 8px;
            padding: 7px 10px;
        }}
        QComboBox::drop-down {{ border: none; width: 24px; }}
        QComboBox QAbstractItemView {{
            background: {d['card']};
            color: {d['fg']};
            border: 1px solid {d['border']};
            selection-background-color: {d['accent']};
            selection-color: #ffffff;
            padding: 4px;
        }}
        QCheckBox {{
            background: transparent;
            color: {d['fg']};
            spacing: 6px;
        }}
        QRadioButton {{ background: transparent; color: {d['fg']}; spacing: 6px; }}
        QFrame#card {{
            background: {d['card']};
            border: 1px solid {d['border']};
            border-radius: 12px;
        }}
        QTabWidget::pane {{
            border: 1px solid {d['border']};
            border-radius: 10px;
            background: {d['card']};
        }}
        QTabBar::tab {{
            background: {d['secondary']};
            color: {d['fg']};
            border: none;
            border-bottom: 2px solid transparent;
            padding: 9px 16px;
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
        }}
        QTabBar::tab:selected {{
            background: {d['card']};
            color: {d['accent']};
            border-bottom: 2px solid {d['accent']};
            font-weight: bold;
        }}
        QMenu {{ background:{d['card']}; border:1px solid {d['border']}; padding:6px; }}
        QMenu::item {{ padding:8px 20px; border-radius:6px; }}
        QMenu::item:selected {{ background:{d['secondary']}; }}
        QSlider::groove:horizontal {{ height:6px; background:{d['secondary']}; border-radius:3px; }}
        QSlider::handle:horizontal {{ width:18px; margin:-6px 0; background:{d['accent']}; border-radius:9px; }}
        QScrollArea {{ background: transparent; border: none; }}
        QScrollArea > QWidget > QWidget {{ background: transparent; }}
        QScrollBar:vertical {{ background: {d['bg']}; width: 10px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: {d['accent']}; min-height: 24px; border-radius: 5px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """
