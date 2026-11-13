"""Portable, validated settings and salted password verification (no Qt dependency)."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = {
    "pet_type": "cat", "pet_size": 160, "pet_wander": True,
    "pet_visible": True, "pet_clothing": "none", "pet_mood": "happy",
    "ui_theme": "dark", "privacy_bg_type": "color",
    "privacy_color": "#101827", "privacy_image": "",
    "password_protect_quit": True,
}
ITERATIONS = 260_000


def default_privacy_image() -> str:
    for name in ("terminal_ai.png", "ai_working.png"):
        path = ROOT / "photos" / name
        if path.is_file():
            return str(path)
    return ""


class SettingsManager:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or ROOT).resolve()
        self._path = self.root / "config" / "settings.json"
        self._data = dict(DEFAULTS)
        self.recovered = False
        if self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf-8"))
                if not isinstance(saved, dict):
                    raise ValueError("settings must be an object")
                self._data.update(saved)
            except (ValueError, OSError):
                self.recovered = True
                self._path.replace(self._path.with_suffix(".broken.json"))
        self._validate()
        legacy = self._data.pop("password", None)
        if isinstance(legacy, str) and legacy:
            self.set_password(legacy)

    def _validate(self) -> None:
        choices = {
            "pet_type": ("cat", "dog", "cow", "rabbit"),
            "pet_clothing": ("none", "hat", "scarf", "bow"),
            "pet_mood": ("happy", "cool", "party"),
            "ui_theme": ("dark", "light", "mint", "ocean"),
            "privacy_bg_type": ("color", "image"),
        }
        for key, values in choices.items():
            if self._data.get(key) not in values:
                self._data[key] = DEFAULTS[key]
        try:
            self._data["pet_size"] = max(80, min(260, int(self._data["pet_size"])))
        except (ValueError, TypeError):
            self._data["pet_size"] = 160
        for key in ("pet_visible", "pet_wander", "password_protect_quit"):
            if not isinstance(self._data.get(key), bool):
                self._data[key] = DEFAULTS[key]
        color = self._data.get("privacy_color")
        if not isinstance(color, str) or len(color) != 7 or color[0] != "#" or any(
            c not in "0123456789abcdefABCDEF" for c in color[1:]
        ):
            self._data["privacy_color"] = DEFAULTS["privacy_color"]
        if not isinstance(self._data.get("privacy_image"), str):
            self._data["privacy_image"] = ""

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(".tmp")
        temp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self._path)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def update(self, values: dict) -> None:
        previous = self._data.copy()
        try:
            self._data.update(values)
            self._validate()
            self._save()
        except OSError:
            self._data = previous
            raise

    def set(self, key: str, value) -> None:
        self.update({key: value})

    @property
    def has_password(self) -> bool:
        return bool(self._data.get("password_hash"))

    def set_password(self, value: str) -> None:
        if len(value) < 4 or len(value) > 128:
            raise ValueError("密码须为 4–128 个字符")
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"), bytes.fromhex(salt), ITERATIONS)
        self.update({"password_salt": salt, "password_hash": digest.hex(), "password_iterations": ITERATIONS})

    def verify_password(self, candidate: str) -> bool:
        if not self.has_password or len(candidate) > 128:
            return False
        try:
            rounds = int(self._data["password_iterations"])
            if not 100_000 <= rounds <= 1_000_000:
                return False
            salt = bytes.fromhex(self._data["password_salt"])
            if len(salt) != 16:
                return False
            digest = hashlib.pbkdf2_hmac("sha256", candidate.encode("utf-8"), salt, rounds)
            return hmac.compare_digest(digest.hex(), self._data["password_hash"])
        except (ValueError, KeyError, TypeError):
            return False

    @property
    def pet_type(self):
        return self.get("pet_type")

    @property
    def pet_size(self):
        return self.get("pet_size")

    @property
    def pet_wander(self):
        return self.get("pet_wander")

    @property
    def privacy_bg_type(self):
        return self.get("privacy_bg_type")

    @property
    def privacy_color(self):
        return self.get("privacy_color")

    @property
    def privacy_image(self):
        value = self.get("privacy_image")
        return str(self.root / value) if value and not Path(value).is_absolute() else value
