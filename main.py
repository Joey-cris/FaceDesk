"""Entry point.  Usage:  python main.py [--auto | --attendance]

--auto       : Windows startup mode. Waits a few seconds after login, greets whoever is recognised
               once, then closes itself (and optionally shows the desktop).
--attendance : opens and immediately starts attendance mode (kiosk style, keeps running).
--selfcheck  : prints a health report (add --camera to test the webcam) and exits.
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import sys

import config


def _fatal(title: str, message: str) -> None:
    logging.getLogger("assistant").error("%s: %s", title, message)
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        print(f"{title}\n{message}", file=sys.stderr)
    sys.exit(1)


def _check_dependencies() -> None:
    try:
        import numpy  # noqa: F401
        import cv2
    except ImportError as exc:
        _fatal("Dependencies missing",
               f"{exc}\n\nRun setup.bat again (or: .venv\\Scripts\\pip install -r requirements.txt).")
    if not hasattr(cv2, "face"):
        _fatal("OpenCV Contrib missing",
               "The 'cv2.face' module is not available. Plain opencv-python is installed instead of "
               "opencv-contrib-python.\n\nFix:\n  .venv\\Scripts\\pip uninstall -y opencv-python "
               "opencv-contrib-python\n  .venv\\Scripts\\pip install opencv-contrib-python==4.10.0.84")
    for mod, why in (("pyttsx3", "will use the fallback voice only"), ("openpyxl", "Excel export disabled (CSV still works)")):
        try:
            __import__(mod)
        except ImportError:
            logging.getLogger("assistant").warning("%s not installed - %s", mod, why)


def _single_instance():
    """Hold a localhost port so two copies (e.g. startup + manual) cannot fight over the webcam."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 47653))
    except OSError:
        return None
    s.listen(1)
    return s


def main() -> int:
    parser = argparse.ArgumentParser(description=config.APP_NAME)
    parser.add_argument("--auto", action="store_true", help="startup mode (greet once, then close)")
    parser.add_argument("--selfcheck", action="store_true", help="print a health report and exit")
    parser.add_argument("--camera", action="store_true", help="with --selfcheck: also test the webcam")
    parser.add_argument("--attendance", action="store_true", help="start attendance mode immediately")
    args = parser.parse_args()

    os.chdir(config.BASE_DIR)               # works no matter where we were launched from
    config.ensure_dirs()
    config.setup_logging()
    log = logging.getLogger("assistant")
    log.info("---- starting (auto=%s attendance=%s) ----", args.auto, args.attendance)

    sys.excepthook = lambda t, v, tb: log.error("Uncaught exception", exc_info=(t, v, tb))

    if args.selfcheck:
        import selfcheck
        checks = selfcheck.run_checks(include_camera=args.camera)
        print(selfcheck.format_report(checks))
        return 0 if all(c.ok for c in checks) else 1

    _check_dependencies()
    lock = _single_instance()
    if lock is None:
        log.warning("Another instance is already running - exiting")
        if not args.auto:
            _fatal("Already running", "The assistant is already open (check the taskbar).")
        return 0

    try:
        import tkinter  # noqa: F401
    except ImportError:
        _fatal("Tkinter missing", "Python's Tkinter component is missing. Reinstall Python 3.12 from "
                                  "python.org and keep 'tcl/tk and IDLE' ticked.")

    if os.name == "nt":                     # crisp text on high-DPI Lenovo screens
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    try:
        settings = config.load_settings()
        from ui import App
        App(settings, auto=args.auto, attendance=args.attendance).run()
    except Exception as exc:
        log.exception("Startup failure")
        _fatal("Application startup failure", f"{type(exc).__name__}: {exc}\n\nSee logs\\assistant.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
