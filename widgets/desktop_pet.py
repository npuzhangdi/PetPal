"""Four independent pets with autonomous walking, feeding, ball play and sleep.

Animation runs at 160 ms, life decisions at 1 s. Hidden pets suspend both timers.
Actions and outfits are available from the tray settings and the pet context menu.
"""

import math
import random
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget,
    QMenu,
    QApplication,
)
from PySide6.QtCore import Qt, QTimer, QPoint, QPointF, QRect
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPen,
    QBrush,
    QPixmap,
    QFont,
    QAction,
)

from core.settings import SettingsManager

_PET_DIR = Path(__file__).resolve().parent.parent / "assets" / "pets"
_PET_TYPES = ["cat", "dog", "cow", "rabbit"]
_PET_NAMES = {"cat": "小猫", "dog": "小狗", "cow": "小牛", "rabbit": "小兔"}

# 状态
S_WANDER = "wander"
S_SLEEP = "sleep"
S_EAT = "eat"
S_PLAY = "play"
S_IDLE = "idle"
S_PRIVACY = "privacy"


def _pet_dir() -> Path:
    return _PET_DIR


class DesktopPet(QWidget):
    """有生活状态的桌面宠物。"""

    _ANIM_MS = 160          # 走路帧切换
    _STATE_MS = 1000      # 状态机 tick（每秒检查状态，动画定时器独立处理走动）
    _SPEECH_MS = 5000      # 气泡停留

    def __init__(self, settings: SettingsManager) -> None:
        super().__init__()
        self.settings = settings
        self._drag_pos: QPoint | None = None

        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.NoFocus)

        # 加载宠物
        self._load_pet(self.settings.pet_type)

        # 状态
        self._state = S_IDLE
        self._frame_index = 0
        self._clothing = str(settings.get("pet_clothing", "none"))
        self._mood = str(settings.get("pet_mood", "happy"))
        self._game_tick = 0
        self._facing = 1
        self._bob_phase = 0.0
        self._wander_target = QPoint(0, 0)
        self._state_until = 0          # 毫秒时间戳，状态结束时间
        self._last_activity_ms = self._now_ms()
        self._bubble: str | None = None
        self._bubble_until = 0
        # 隐私屏互动
        self._privacy_mode = False
        self._privacy_strict = False   # 输入错误后变严肃

        # 定时器
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(self._ANIM_MS)
        self._anim_timer.timeout.connect(self._tick_anim)
        self._anim_timer.start()

        self._state_timer = QTimer(self)
        self._state_timer.setInterval(self._STATE_MS)
        self._state_timer.timeout.connect(self._tick_state)
        self._state_timer.start()

        # 默认位置
        self._move_to_den()
        self._build_context_menu()
        QApplication.instance().screenAdded.connect(self._screen_changed)
        QApplication.instance().screenRemoved.connect(self._screen_changed)

    # ------------------------------------------------------------------
    # 时间工具
    # ------------------------------------------------------------------
    def _now_ms(self) -> int:
        import time
        return int(time.monotonic() * 1000)

    def _in_ms(self, ms: int) -> int:
        return self._now_ms() + ms

    # ------------------------------------------------------------------
    # 宠物资源
    # ------------------------------------------------------------------
    def _load_pet(self, pet_type: str) -> None:
        if pet_type not in _PET_TYPES:
            pet_type = "cat"
        d = _pet_dir() / pet_type
        self._frames: list[QPixmap] = []
        for i in range(4):
            self._frames.append(QPixmap(str(d / f"frame_{i:02d}.png")))
        self._standing = QPixmap(str(d / "standing.png"))
        self._sleeping = QPixmap(str(d / "sleeping.png"))
        # 若没有睡觉帧，用站立帧替代
        if self._sleeping.isNull():
            self._sleeping = self._standing
        self._pet_type = pet_type
        size = int(self.settings.pet_size)
        self.setFixedSize(size, size + 30)  # 多 30px 给窝/气泡
        self._frame_index = 0
        self._bubble = None

    def set_pet_type(self, pet_type: str) -> None:
        self.settings.set("pet_type", pet_type)
        self._load_pet(pet_type)
        self._move_to_den()

    def set_pet_size(self, size: int) -> None:
        self.settings.set("pet_size", size)
        self._load_pet(self._pet_type)

    def set_wander(self, enabled: bool) -> None:
        self.settings.set("pet_wander", enabled)
        if not enabled:
            self._state = S_IDLE
            self._move_to_den()

    def set_clothing(self, clothing: str) -> None:
        self.settings.set("pet_clothing", clothing)
        self._clothing = clothing
        self.update()

    def set_mood(self, mood: str) -> None:
        self.settings.set("pet_mood", mood)
        self._mood = mood
        self.update()

    def _is_wander_enabled(self) -> bool:
        return bool(self.settings.pet_wander) and not self._privacy_mode

    # ------------------------------------------------------------------
    # 位置
    # ------------------------------------------------------------------
    def _default_pos(self) -> QPoint:
        screen = (QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()).availableGeometry()
        return QPoint(screen.left() + 80, screen.bottom() - self.height() - 80)

    def _den_pos(self) -> QPoint:
        """窝的位置：屏幕右下角。"""
        screen = (QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()).availableGeometry()
        return QPoint(
            screen.right() - self.width() - 24,
            screen.bottom() - self.height() - 24,
        )

    def _move_to_den(self) -> None:
        self.move(self._den_pos())

    def _move_to_desktop_center(self) -> None:
        screen = (QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()).availableGeometry()
        self.move(
            screen.left() + (screen.width() - self.width()) // 2,
            screen.top() + (screen.height() - self.height()) // 2,
        )

    # ------------------------------------------------------------------
    # 状态机
    # ------------------------------------------------------------------
    def _tick_state(self) -> None:
        now = self._now_ms()
        # 气泡过期
        if self._bubble and now > self._bubble_until:
            self._bubble = None
            self.update()

        # 隐私屏模式优先
        if self._privacy_mode:
            self._state = S_PRIVACY
            # 主人有操作（鼠标/键盘）→ 跑出来
            if self._last_activity_ms < now:  # 有操作
                if not self._privacy_strict:
                    self._move_to_desktop_center()
                    self._speak(
                        random.choice([
                            "主人，你回来了吗？",
                            "咦？主人？快输入密码～",
                            "检测到有人操作！输入密码解锁",
                        ])
                    )
            return

        # Independent life timers: inactivity never suppresses meals and games forever.
        if now > self._state_until:
            self._decide_next_state(now)

    def _decide_next_state(self, now: int) -> None:
        """Select independent life actions; wandering is an optional movement action."""
        r = random.random()
        if r < 0.30 and self._is_wander_enabled():
            # 偶尔逛逛（去一个随机点，慢走 30~60 秒才结束）
            self._state = S_WANDER
            screen = (QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()).availableGeometry()
            m = 12
            self._wander_target = QPoint(
                random.randint(screen.left() + m, max(screen.left() + m, screen.right() - self.width() - m)),
                random.randint(screen.top() + m, max(screen.top() + m, screen.bottom() - self.height() - m)),
            )
            self._state_until = now + random.randint(30_000, 60_000)
        elif r < 0.48:
            # 偶尔吃饭（不常见）
            self._enter_eat(now)
        elif r < 0.66:
            # 偶尔玩游戏（不常见）
            self._enter_play(now)
        elif r < 0.80:
            self._enter_sleep(now)
        else:
            # 回窝短暂休息后再决策
            self._enter_idle(now)

    def _enter_sleep(self, now: int) -> None:
        self._state = S_SLEEP
        self._move_to_den()
        # 睡一小会儿
        self._state_until = now + random.randint(15_000, 30_000)
        self._speak(random.choice(["zzZ…", "好困啊，先睡会儿…", "睡个午觉"]))

    def _enter_eat(self, now: int) -> None:
        self._state = S_EAT
        self._move_to_den()
        # 短时进食动画
        self._state_until = now + random.randint(8_000, 14_000)
        self._speak(random.choice(["开饭啦！", "饿死我了，吃饭吃饭～", "干饭时间！"]))

    def _enter_play(self, now: int) -> None:
        self._state = S_PLAY
        self._move_to_den()
        # 短时玩球动画
        self._state_until = now + random.randint(8_000, 14_000)
        self._speak(random.choice(["一起玩球～", "追个小球！"]))

    def _enter_idle(self, now: int) -> None:
        self._state = S_IDLE
        self._move_to_den()
        # 安静休息 15–30 秒
        self._state_until = now + random.randint(15_000, 30_000)
        self._bubble = None
        self.update()

    def _step_toward(self, target: QPoint) -> None:
        cur = self.pos()
        dx = target.x() - cur.x()
        dy = target.y() - cur.y()
        dist = math.hypot(dx, dy)
        if dist < 10:
            return
        step = 6  # 慢走（原来是 12，太快显得频繁）
        nx = cur.x() + int(dx / dist * step)
        ny = cur.y() + int(dy / dist * step)
        self._facing = 1 if dx >= 0 else -1
        self.move(nx, ny)
        self.update()

    # ------------------------------------------------------------------
    # 动画
    # ------------------------------------------------------------------
    def _tick_anim(self) -> None:
        # 走路帧 vs 站立帧 vs 睡觉帧
        if self._state == S_WANDER and self._drag_pos is None:
            self._step_toward(self._wander_target)
            if (self.pos() - self._wander_target).manhattanLength() < 12:
                self._enter_idle(self._now_ms())
            self._frame_index = (self._frame_index + 1) % 4
        elif self._state == S_SLEEP:
            self._frame_index = 0
        else:
            self._frame_index = 0
        if self._bubble and self._now_ms() > self._bubble_until:
            self._bubble = None
        self._game_tick += 1
        # 轻微浮动
        self._bob_phase += 0.1
        self.update()

    # ------------------------------------------------------------------
    # 说话
    # ------------------------------------------------------------------
    def _speak(self, text: str) -> None:
        self._bubble = text
        self._bubble_until = self._in_ms(self._SPEECH_MS)
        self.update()

    def _clear_bubble(self) -> None:
        self._bubble = None
        self.update()

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        body_size = w - 4
        bob_y = math.sin(self._bob_phase) * 2

        # 1) 画窝（睡觉/吃饭/游戏/待着 时）
        show_den = self._state in (S_SLEEP, S_IDLE, S_EAT, S_PLAY)
        if show_den:
            self._draw_den(p, w, h)

        # 2) 画动物
        if self._state == S_SLEEP:
            pm = self._sleeping
        elif self._state == S_WANDER:
            pm = self._frames[self._frame_index]
        else:
            pm = self._standing
        if not pm.isNull():
            scaled = pm.scaled(body_size, body_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            if self._facing == -1:
                from PySide6.QtGui import QTransform
                t = QTransform()
                t.scale(-1, 1)
                scaled = scaled.transformed(t)
            p.drawPixmap(int((w - scaled.width()) / 2), int((h - scaled.height()) / 2 + bob_y - 14), scaled)

        # Compact status labels stay inside even the smallest pet window.
        if self._state == S_SLEEP:
            p.setPen(QColor("#aabcf7"))
            p.setFont(QFont("Microsoft YaHei UI", 10))
            p.drawText(QRect(w - 40, 25, 38, 24), Qt.AlignCenter, "Zzz")
        if self._state in (S_EAT, S_PLAY):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(25, 32, 48, 210))
            p.drawRoundedRect(3, h - 23, w - 6, 22, 7, 7)
            p.setPen(QColor("#ffffff"))
            p.setFont(QFont("Microsoft YaHei UI", 9))
            label = "吃饭中" if self._state == S_EAT else "玩球中"
            p.drawText(QRect(3, h - 23, w - 6, 22), Qt.AlignCenter, label)

        if self._state == S_EAT:
            p.setPen(QPen(QColor("#de9442"), 2))
            p.setBrush(QColor("#f9d879"))
            p.drawRoundedRect(w // 2 - 24, h - 38, 48, 18, 8, 8)
            for i in range(5):
                p.setBrush(QColor("#a85f3b"))
                p.drawEllipse(w // 2 - 17 + i * 7, h - 42 + (self._game_tick + i) % 3, 6, 6)
        elif self._state == S_PLAY:
            x = 16 + int((w - 46) * (math.sin(self._bob_phase * 2) + 1) / 2)
            y = h - 48 - int(abs(math.sin(self._bob_phase * 4)) * 30)
            p.setPen(QPen(QColor("#2d6789"), 2))
            p.setBrush(QColor("#6bd2ea"))
            p.drawEllipse(x, y, 22, 22)
            p.drawLine(x + 11, y, x + 11, y + 22)

        # Clothes follow each sprite's head, size and facing direction.
        source_head = {"cat": (112, 100), "dog": (110, 100),
                       "cow": (116, 92), "rabbit": (120, 104)}[self._pet_type]
        scale = body_size / 256.0
        hx = source_head[0] if self._facing == 1 else 256 - source_head[0]
        p.save()
        p.translate(2 + hx * scale, (h - body_size) / 2 + bob_y - 14 + source_head[1] * scale)
        p.scale(scale, scale)
        p.setPen(Qt.NoPen)
        if self._clothing == "hat":
            p.setBrush(QColor("#f7b74c"))
            p.drawRoundedRect(-24, -48, 48, 20, 7, 7)
            p.setBrush(QColor("#ce8432"))
            p.drawRoundedRect(-32, -30, 64, 7, 3, 3)
        elif self._clothing == "scarf":
            p.setBrush(QColor("#e66977"))
            p.drawRoundedRect(-30, 25, 60, 12, 5, 5)
            p.drawRoundedRect(10, 28, 12, 30, 4, 4)
        elif self._clothing == "bow":
            p.setBrush(QColor("#ed8dd3"))
            p.drawEllipse(-24, 25, 24, 15)
            p.drawEllipse(0, 25, 24, 15)
            p.setBrush(QColor("#b65eab"))
            p.drawEllipse(-5, 28, 10, 10)
        if self._mood == "cool" and self._state != S_SLEEP:
            p.setBrush(QColor("#273443"))
            p.drawRoundedRect(-22, -10, 18, 12, 4, 4)
            p.drawRoundedRect(4, -10, 18, 12, 4, 4)
            p.drawRect(-4, -6, 8, 3)
        elif self._mood == "party" and self._state != S_SLEEP:
            for i, color in enumerate(("#ff7ca9", "#85dddb", "#ffd86c")):
                p.setBrush(QColor(color))
                p.drawEllipse(-45 + i * 40, -52 - int(math.sin(self._bob_phase + i) * 9), 7, 9)
        p.restore()

        # 4) 气泡
        if self._bubble:
            self._draw_bubble(p)
        p.end()

    def _draw_den(self, p: QPainter, w: int, h: int) -> None:
        """画一个简单的小窝（右下角）。"""
        den_x, den_y = w - 46, h - 52
        # 窝底
        p.setBrush(QBrush(QColor(120, 80, 50, 200)))
        p.setPen(QPen(QColor(80, 50, 30), 2))
        p.drawRoundedRect(den_x, den_y, 40, 34, 14, 14)
        # 窝垫
        p.setBrush(QBrush(QColor(180, 130, 90, 220)))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(den_x + 6, den_y + 6, 28, 22, 10, 10)

    def _draw_bubble(self, p: QPainter) -> None:
        from PySide6.QtCore import QRect
        p.setFont(QFont("Microsoft YaHei", 9))
        box = QRect(3, 0, self.width() - 6, 48)
        p.setPen(QPen(QColor("#dddded"), 1))
        p.setBrush(QColor(255, 255, 255, 240))
        p.drawRoundedRect(box, 10, 10)
        p.setPen(QColor("#303545"))
        p.drawText(box.adjusted(6, 3, -6, -3), Qt.AlignCenter | Qt.TextWordWrap, self._bubble)

    # ------------------------------------------------------------------
    # 隐私屏联动 API（由 AppController 调用）
    # ------------------------------------------------------------------
    def on_privacy_screen_active(self, active: bool) -> None:
        """隐私屏开/关时调用。"""
        self._privacy_mode = active
        self._privacy_strict = False
        if active:
            self._state = S_PRIVACY
            self._move_to_desktop_center()
            self._speak("主人开隐私屏了～")
        else:
            self._state = S_IDLE
            self._move_to_den()

    def on_privacy_password_ok(self) -> None:
        """密码正确 → 撒欢。"""
        self._privacy_strict = False
        self._speak(random.choice([
            "耶！主人回来啦～",
            "密码对！欢迎回来！",
            "终于啦！抱抱～",
        ]))
        self._frame_index = 0
        self._state = S_IDLE
        # 撒欢：快速走路帧
        for _ in range(4):
            self._frame_index = (self._frame_index + 1) % 4

    def on_privacy_password_wrong(self, consecutive: int) -> None:
        """密码错误 → 严肃。"""
        self._privacy_strict = True
        if consecutive >= 3:
            self._speak("还需要正确密码哦")
        else:
            self._speak(random.choice([
                "密码错啦，你是谁？",
                "你是谁？在干什么，赶紧走！",
                "请确认你的隐私密码",
            ]))
        self._frame_index = 0
        self._state = S_PRIVACY

    def on_user_activity(self) -> None:
        """用户有鼠标/键盘操作（隐私屏下触发互动）。"""
        self._last_activity_ms = self._now_ms()
        if self._privacy_mode:
            self._state = S_PRIVACY
            if not self._privacy_strict:
                self._move_to_desktop_center()
                self._speak(random.choice([
                    "主人，你回来了吗？",
                    "咦？主人？快输入密码～",
                    "检测到有人，输入密码解锁",
                ]))

    def raise_to_top(self) -> None:
        self.raise_()

    # ------------------------------------------------------------------
    # 鼠标交互
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._drag_pos = event.globalPosition().toPoint() - self.pos()
            event.accept()
        elif event.button() == Qt.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
        # 操作 → 记活跃度
        self._last_activity_ms = self._now_ms()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._drag_pos is not None:
            moved = (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
            if moved < 6:
                self._speak(random.choice([
                    f"{_PET_NAMES.get(self._pet_type, '')}：戳我一下～",
                    "别戳我啦！",
                    "好痒！",
                ]))
        self._drag_pos = None
        self._last_activity_ms = self._now_ms()
        super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:  # type: ignore
        self._speak("看我～")
        self._last_activity_ms = self._now_ms()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore
        self._last_activity_ms = self._now_ms()
        self.update()
        super().leaveEvent(event)

    # ------------------------------------------------------------------
    # 右键菜单
    # ------------------------------------------------------------------
    def _build_context_menu(self) -> None:
        self._ctx_menu = QMenu()
        sub = QMenu("切换动物", self._ctx_menu)
        for t in _PET_TYPES:
            act = QAction(f"{_PET_NAMES[t]} ({t})", sub)
            act.triggered.connect(lambda checked=False, ty=t: self._change_pet_type(ty))
            sub.addAction(act)
        self._ctx_menu.addMenu(sub)

        act_wander = QAction("自动巡视", self._ctx_menu)
        act_wander.setCheckable(True)
        act_wander.setChecked(self.settings.pet_wander)
        act_wander.toggled.connect(self._on_wander_toggled)
        self._ctx_menu.addAction(act_wander)

        sub_size = QMenu("宠物大小", self._ctx_menu)
        for s in (96, 128, 160, 200, 240):
            act = QAction(f"{s}px", sub_size)
            act.triggered.connect(lambda checked=False, ss=s: self.set_pet_size(ss))
            sub_size.addAction(act)
        self._ctx_menu.addMenu(sub_size)

        act_home = QAction("回窝", self._ctx_menu)
        act_home.triggered.connect(self._return_home)
        self._ctx_menu.addAction(act_home)

        for label, action in (("喂饭", "eat"), ("玩球", "play")):
            item = QAction(label, self._ctx_menu)
            item.triggered.connect(lambda checked=False, a=action: self.perform_action(a))
            self._ctx_menu.addAction(item)
        clothes = QMenu("换装", self._ctx_menu)
        for key, label in (("none", "自然装扮"), ("hat", "小帽子"), ("scarf", "围巾"), ("bow", "蝴蝶结")):
            item = QAction(label, clothes)
            item.triggered.connect(lambda checked=False, k=key: self.set_clothing(k))
            clothes.addAction(item)
        self._ctx_menu.addMenu(clothes)
        act_sleep = QAction("让它睡觉", self._ctx_menu)
        act_sleep.triggered.connect(lambda: self._enter_sleep(self._now_ms()))
        self._ctx_menu.addAction(act_sleep)

    def _show_context_menu(self, pos: QPoint) -> None:
        self._ctx_menu.exec(pos)

    def _change_pet_type(self, t: str) -> None:
        self.set_pet_type(t)
        self._speak(f"换成{_PET_NAMES[t]}啦！")

    def _on_wander_toggled(self, checked: bool) -> None:
        self.set_wander(checked)

    def _return_home(self) -> None:
        self._enter_idle(self._now_ms())

    def perform_action(self, action):
        handlers = {"eat": self._enter_eat, "play": self._enter_play, "sleep": self._enter_sleep,
                    "home": self._enter_idle}
        if action in handlers:
            handlers[action](self._now_ms())
            self.update()

    def _screen_changed(self, *args):
        self._move_to_den()
        self._enter_idle(self._now_ms())

    def hideEvent(self, event):
        self._anim_timer.stop()
        self._state_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        self._anim_timer.start()
        self._state_timer.start()
        super().showEvent(event)

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        self._anim_timer.stop()
        self._state_timer.stop()
        # 等待后台线程结束
        for attr in ("_anim_timer", "_state_timer"):
            t = getattr(self, attr, None)
            if t:
                t.stop()
        super().closeEvent(event)

    def wait_threads(self) -> None:
        """供外部清理时调用：确保定时器停止。"""
        self._anim_timer.stop()
        self._state_timer.stop()
