"""Configuration, paths and constants for the Master Joey Galolo AI Welcome Assistant.

Everything is stored locally inside the project folder. Nothing is ever uploaded.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

log = logging.getLogger("assistant.config")

# ---------------------------------------------------------------- constants
APP_NAME = "FaceDesk: Smart Welcome and Attendance System"
APP_SHORT = "FaceDesk"
VERSION = "2.1"
USER_DISPLAY_NAME = "Master Joey Galolo"
# The exact sentence required by the specification. Intentionally NOT a setting.
GREETING_TEXT = "Welcome back, Master Joey Galolo."
WELCOME_BANNER = "Welcome Back, Master Joey Galolo"

# ---------------------------------------------------------------- paths
if getattr(sys, "frozen", False):          # running as a PyInstaller .exe
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data"
FACES_DIR = DATA_DIR / "faces"
MODEL_DIR = DATA_DIR / "model"
MODEL_FILE = MODEL_DIR / "lbph_model.yml"
MODEL_META_FILE = MODEL_DIR / "model_meta.json"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "assistant.log"
CONFIG_FILE = BASE_DIR / "config.json"
DB_FILE = DATA_DIR / "facedesk.db"
EXPORT_DIR = BASE_DIR / "exports"
BACKUP_DIR = BASE_DIR / "backups"


def person_dir(person_id: int) -> Path:
    """Folder holding one person's face samples (data/faces/<id>/)."""
    return FACES_DIR / str(int(person_id))


# ---------------------------------------------------------------- settings
@dataclass
class Settings:
    # camera
    camera_index: int = 0
    frame_width: int = 640
    frame_height: int = 480
    # recognition
    recognition_timeout_seconds: int = 60       # 0 = never time out
    required_consecutive_matches: int = 8
    recognition_threshold: float = 60.0         # LBPH distance; LOWER = stricter
    recognition_margin: float = 1.25            # best person must beat the runner-up by this ratio (1.0 = off)
    min_face_size: int = 100                    # pixels; ignore tiny/far faces
    # liveness (basic safeguards only - see README)
    liveness_motion_check: bool = True
    liveness_motion_min: float = 0.06           # fraction of face width
    liveness_blink_check: bool = False
    # registration
    registration_samples: int = 40
    registration_interval_seconds: float = 0.35
    # automatic startup behaviour (only used with --auto)
    startup_delay_seconds: int = 5
    auto_close_seconds: int = 10                # close window after welcome; 0 = stay open
    # attendance
    late_after: str = "08:30"                   # HH:MM (24h) - Time In after this is marked Late
    min_minutes_between_scans: int = 30         # min gap before a scan counts as Time Out
    scan_cooldown_seconds: int = 15             # ignore the same person this long after a scan
    banner_seconds: int = 4                     # how long the result banner stays on screen
    # school
    holidays: str = ""                          # comma-separated YYYY-MM-DD no-class days
    # privacy / school
    require_consent: bool = False               # block face registration until consent is recorded (owner exempt)
    reregister_after_days: int = 180            # flag face data older than this (children's faces change); 0 = off
    trial_timeout_seconds: int = 8              # accuracy test: max seconds per trial
    # security
    admin_session_minutes: int = 10             # admin unlock expires after this idle time
    auto_backup_keep: int = 14                  # daily automatic backups (database only) to keep
    show_desktop_after_welcome: bool = True     # startup mode: reveal the desktop (Win+D) after closing
    # voice
    voice_rate: int = 170
    voice_volume: float = 1.0
    voice_name: str = ""                        # substring of a Windows voice name, optional

    def validate(self) -> "Settings":
        def clamp(v, lo, hi, cast):
            try:
                return cast(min(max(cast(v), lo), hi))
            except (TypeError, ValueError):
                return cast(lo)

        self.camera_index = clamp(self.camera_index, 0, 9, int)
        self.frame_width = clamp(self.frame_width, 320, 1920, int)
        self.frame_height = clamp(self.frame_height, 240, 1080, int)
        self.recognition_timeout_seconds = clamp(self.recognition_timeout_seconds, 0, 3600, int)
        self.required_consecutive_matches = clamp(self.required_consecutive_matches, 3, 60, int)
        self.recognition_threshold = clamp(self.recognition_threshold, 20.0, 120.0, float)
        self.recognition_margin = clamp(self.recognition_margin, 1.0, 3.0, float)
        self.min_face_size = clamp(self.min_face_size, 40, 400, int)
        self.liveness_motion_min = clamp(self.liveness_motion_min, 0.0, 0.5, float)
        self.registration_samples = clamp(self.registration_samples, 15, 200, int)
        self.registration_interval_seconds = clamp(self.registration_interval_seconds, 0.1, 3.0, float)
        self.startup_delay_seconds = clamp(self.startup_delay_seconds, 0, 120, int)
        self.auto_close_seconds = clamp(self.auto_close_seconds, 0, 600, int)
        self.voice_rate = clamp(self.voice_rate, 80, 300, int)
        self.voice_volume = clamp(self.voice_volume, 0.1, 1.0, float)
        self.voice_name = str(self.voice_name or "")
        self.liveness_motion_check = bool(self.liveness_motion_check)
        self.liveness_blink_check = bool(self.liveness_blink_check)
        self.show_desktop_after_welcome = bool(self.show_desktop_after_welcome)
        self.min_minutes_between_scans = clamp(self.min_minutes_between_scans, 0, 720, int)
        self.scan_cooldown_seconds = clamp(self.scan_cooldown_seconds, 0, 600, int)
        self.banner_seconds = clamp(self.banner_seconds, 1, 30, int)
        self.require_consent = bool(self.require_consent)
        self.reregister_after_days = clamp(self.reregister_after_days, 0, 3650, int)
        self.trial_timeout_seconds = clamp(self.trial_timeout_seconds, 2, 60, int)
        self.admin_session_minutes = clamp(self.admin_session_minutes, 1, 120, int)
        self.auto_backup_keep = clamp(self.auto_backup_keep, 1, 90, int)
        good = []
        for part in str(self.holidays or "").replace(";", ",").split(","):
            part = part.strip()
            if part:
                try:
                    from datetime import date as _d
                    good.append(_d.fromisoformat(part).isoformat())
                except ValueError:
                    pass
        self.holidays = ",".join(sorted(set(good)))
        import re
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(self.late_after).strip())
        if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
            self.late_after = f"{int(m.group(1)):02d}:{m.group(2)}"
        else:
            self.late_after = "08:30"
        return self


def load_settings() -> Settings:
    """Load config.json. Recreate defaults if it is missing; back it up if corrupted."""
    if not CONFIG_FILE.exists():
        log.warning("config.json missing - creating defaults")
        s = Settings().validate()
        save_settings(s)
        return s
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("config.json must contain a JSON object")
        known = {f.name for f in fields(Settings)}
        s = Settings(**{k: v for k, v in raw.items() if k in known}).validate()
        return s
    except (ValueError, TypeError, OSError) as exc:
        log.error("config.json unreadable (%s) - backing up and using defaults", exc)
        try:
            shutil.copy2(CONFIG_FILE, CONFIG_FILE.with_suffix(".json.bad"))
        except OSError:
            pass
        s = Settings().validate()
        save_settings(s)
        return s


def save_settings(settings: Settings) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    except OSError as exc:
        log.error("Could not save config.json: %s", exc)
        raise


def ensure_dirs() -> None:
    for d in (DATA_DIR, FACES_DIR, MODEL_DIR, LOG_DIR, EXPORT_DIR, BACKUP_DIR):
        d.mkdir(parents=True, exist_ok=True)


def setup_logging() -> None:
    from logging.handlers import RotatingFileHandler

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=512_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)
    if sys.stderr is not None and not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        for h in root.handlers
    ):
        root.addHandler(logging.StreamHandler())


def holiday_set(settings: "Settings") -> set:
    from datetime import date as _d
    return {_d.fromisoformat(x) for x in settings.holidays.split(",") if x}
