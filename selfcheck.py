"""Self-check: tests the parts of FaceDesk that usually go wrong and says how to fix them in plain words."""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass

import config


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    warn: bool = False      # ok=True but worth attention


def _try(name: str, fn) -> Check:
    try:
        return fn()
    except Exception as exc:                       # a broken check must never crash the app
        return Check(name, False, f"{type(exc).__name__}: {exc}", "See logs\\assistant.log or send this text for help.")


def check_python() -> Check:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) == (3, 12):
        return Check("Python", True, ver)
    return Check("Python", True, f"{ver} (tested with 3.12)", "Install Python 3.12 if you see strange errors.", warn=True)


def check_packages() -> list[Check]:
    out = []
    for mod, fix in (("numpy", "Run setup.bat again."), ("cv2", "Run setup.bat again."),
                     ("tkinter", "Reinstall Python 3.12 and keep 'tcl/tk and IDLE' ticked."),
                     ("pyttsx3", "Run setup.bat again (voice will use the backup engine meanwhile)."),
                     ("openpyxl", "Run setup.bat again (CSV export still works).")):
        try:
            m = __import__(mod)
            out.append(Check(f"Package {mod}", True, getattr(m, "__version__", "installed")))
        except ImportError as exc:
            out.append(Check(f"Package {mod}", False, str(exc), fix))
    try:
        import cv2
        if hasattr(cv2, "face"):
            out.append(Check("OpenCV face module", True, "available"))
        else:
            out.append(Check("OpenCV face module", False, "cv2.face missing",
                             "Run: .venv\\Scripts\\pip uninstall -y opencv-python opencv-contrib-python  then  "
                             ".venv\\Scripts\\pip install opencv-contrib-python==4.10.0.84"))
    except ImportError:
        pass
    return out


def check_detector() -> Check:
    from face_engine import FaceDetector
    FaceDetector(100)
    return Check("Face detector files", True, "loaded")


def check_storage() -> Check:
    config.ensure_dirs()
    probe = config.DATA_DIR / ".write_test"
    probe.write_text("ok")
    probe.unlink()
    free = shutil.disk_usage(config.DATA_DIR).free / 1e9
    if free < 0.2:
        return Check("Storage", True, f"writable, only {free:.2f} GB free", "Free up disk space.", warn=True)
    return Check("Storage", True, f"writable, {free:.1f} GB free")


def check_database() -> Check:
    import sqlite3
    from db import Database
    db = Database()
    with db._tx() as c:
        res = c.execute("PRAGMA integrity_check").fetchone()[0]
        n = c.execute("SELECT COUNT(*) FROM people").fetchone()[0]
        a = c.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
    if res != "ok":
        return Check("Database", False, f"integrity check: {res}", "Restore the latest backup (Admin page).")
    return Check("Database", True, f"OK - {n} people, {a} attendance records")


def check_faces() -> Check:
    from face_engine import FaceModel, ModelError, NotRegisteredError
    try:
        FaceModel.load()
    except NotRegisteredError:
        return Check("Registered faces", True, "nobody registered yet", "People page > Register Face.", warn=True)
    except ModelError as exc:
        return Check("Registered faces", False, str(exc), "People page > Delete ALL Face Data, then register again.")
    n = len(FaceModel.load_samples())
    return Check("Registered faces", True, f"model loads - {n} person(s) have face data")


def check_settings() -> Check:
    s = config.load_settings()
    return Check("Settings", True, f"config.json OK (threshold {s.recognition_threshold:g}, late after {s.late_after})")


def check_voice() -> Check:
    try:
        import pyttsx3
        engine = pyttsx3.init()
        try:
            voices = engine.getProperty("voices") or []
        finally:
            try:
                engine.stop()
            except Exception:
                pass
    except Exception as exc:
        return Check("Voice", False, f"{type(exc).__name__}: {exc}",
                     "Settings > Time & language > Speech: make sure a voice is installed. FaceDesk also "
                     "tries a backup Windows voice engine automatically - use Test Voice to hear it.")
    if not voices:
        return Check("Voice", False, "no voices found",
                     "Settings > Time & language > Speech > add a voice. A backup voice engine is tried automatically.")
    return Check("Voice", True, f"{len(voices)} voice(s) available (use Test Voice to hear it)")


def check_camera(settings: config.Settings) -> Check:
    from camera import Camera, CameraError
    cam = Camera(settings.camera_index, settings.frame_width, settings.frame_height)
    try:
        cam.open()
        frame = cam.read()
        if frame is None:
            return Check("Camera", False, "opened but no picture", "Close other apps using the camera.")
        h, w = frame.shape[:2]
        return Check("Camera", True, f"working ({w}x{h})")
    except CameraError as exc:
        return Check("Camera", False, str(exc))
    finally:
        cam.release()


def run_checks(include_camera: bool = False) -> list[Check]:
    results: list[Check] = []
    results.append(_try("Python", check_python))
    try:
        results.extend(check_packages())
    except Exception as exc:
        results.append(Check("Packages", False, str(exc)))
    for name, fn in (("Face detector files", check_detector), ("Storage", check_storage),
                     ("Database", check_database), ("Settings", check_settings),
                     ("Registered faces", check_faces), ("Voice", check_voice)):
        results.append(_try(name, fn))
    if include_camera:
        results.append(_try("Camera", lambda: check_camera(config.load_settings())))
    return results


def format_report(checks: list[Check]) -> str:
    lines = [f"{config.APP_SHORT} {config.VERSION} - self-check", ""]
    for c in checks:
        mark = "OK  " if c.ok and not c.warn else "WARN" if c.ok else "FAIL"
        lines.append(f"[{mark}] {c.name}: {c.detail}")
        if c.fix and (not c.ok or c.warn):
            lines.append(f"       -> {c.fix}")
    bad = sum(1 for c in checks if not c.ok)
    lines += ["", "All good." if not bad else f"{bad} problem(s) found - see the lines marked FAIL."]
    return "\n".join(lines)
