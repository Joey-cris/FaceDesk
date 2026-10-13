"""Session logic: runs camera + recognition/attendance/registration on a worker thread.

The UI never touches the camera. It reads `controller.snapshot()` and drains `controller.events`.

Modes
  welcome     : greet the first recognised person, then stop (used at Windows startup)
  attendance  : keep running; log Time In / Time Out for every recognised person
  registering : capture face samples for one person and train the shared model
  accuracy    : run N recognition trials for a known truth (genuine / impostor) and record the outcomes
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import cv2

import config
from camera import Camera, CameraError
from db import Database
from face_engine import (ConsentError, FaceDetector, FaceModel, LivenessTracker, ModelError,
                         NotRegisteredError)
from face_registration import FaceRegistration
from voice_assistant import VoiceAssistant

log = logging.getLogger("assistant.controller")

BLUE = (255, 180, 0)    # BGR
GREEN = (120, 220, 60)
RED = (60, 60, 240)
AMBER = (0, 190, 255)


@dataclass
class Snapshot:
    frame: object = None            # latest BGR frame with face boxes (un-mirrored)
    status: str = "Initializing Assistant"
    level: str = "info"             # info | ok | warn | error
    detail: str = ""
    score: Optional[float] = None   # LBPH distance of the current face (lower = closer)
    progress: float = 0.0           # 0..1
    banner: Optional[dict] = None   # {"title","subtitle","level"} drawn over the video

    @property
    def welcome(self) -> bool:      # kept for convenience / tests
        return self.banner is not None and self.banner.get("kind") == "welcome"


def greeting_for(person: dict, kind: str, now: datetime) -> str:
    """Text to speak. The owner's welcome is the exact sentence required by the spec."""
    name = person["name"]
    owner = bool(person.get("is_owner")) and name == config.USER_DISPLAY_NAME
    if kind in ("welcome", "time_in") and owner:
        return config.GREETING_TEXT
    if kind == "time_out":
        return f"Goodbye, {name}. Time out recorded."
    part = "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 18 else "Good evening"
    if kind == "welcome":
        return f"{part}, {name}."
    return f"{part}, {name}. Time in recorded."


class Controller:
    def __init__(self, settings: config.Settings, voice: VoiceAssistant, events: "queue.Queue",
                 db: Database, camera_factory: Callable[..., Camera] = Camera,
                 detector_factory: Callable[..., FaceDetector] = FaceDetector,
                 clock: Callable[[], datetime] = datetime.now):
        self.settings = settings
        self.voice = voice
        self.events = events
        self.db = db
        self._camera_factory = camera_factory
        self._detector_factory = detector_factory
        self._clock = clock
        self._snap = Snapshot()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.mode = "idle"

    # ------------------------------------------------------------ public API
    def snapshot(self) -> Snapshot:
        return self._snap

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start_welcome(self) -> None:
        self._start(lambda: self._recognition_session(continuous=False), "welcome")

    def start_attendance(self, section: str = "") -> None:
        """Class mode: with a section, ONLY that section's people are matched (and counted)."""
        self._start(lambda: self._recognition_session(continuous=True, section=section), "attendance")

    def start_accuracy_test(self, truth_person_id, truth_label: str, conditions: dict, n_trials: int,
                            section: str = "") -> None:
        """truth_person_id = a registered person (genuine trials) or None (impostor trials)."""
        self._start(lambda: self._accuracy_session(truth_person_id, truth_label, conditions, n_trials, section),
                    "accuracy")

    def start_registration(self, person_id: int) -> None:
        self._start(lambda: self._registration_session(person_id), "registering")

    def stop(self, wait: float = 4.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=wait)

    # ------------------------------------------------------------ internals
    def _start(self, target, mode: str) -> None:
        self.stop()
        self._stop.clear()
        self.mode = mode
        self._snap = Snapshot()
        self._thread = threading.Thread(target=self._wrap, args=(target,), name=mode, daemon=True)
        self._thread.start()

    def _wrap(self, target) -> None:
        try:
            target()
        except CameraError as exc:
            self._set("Camera Unavailable", "error", str(exc))
            self.events.put(("error", "Camera Unavailable", str(exc)))
        except ConsentError as exc:
            self._set("Consent Required", "warn", str(exc))
            self.events.put(("error", "Consent Required", str(exc)))
        except ModelError as exc:
            self._set("Face Model Problem", "error", str(exc))
            self.events.put(("error", "Face Model Problem", str(exc)))
        except Exception as exc:
            log.exception("Session crashed")
            msg = f"Unexpected error: {type(exc).__name__}: {exc}. See logs/assistant.log"
            self._set("Error", "error", msg)
            self.events.put(("error", "Unexpected Error", msg))
        finally:
            self.mode = "idle"
            self.events.put(("stopped",))

    def _set(self, status, level="info", detail="", frame=None, score=None, progress=0.0,
             banner=None) -> None:
        if frame is None:
            frame = self._snap.frame
        self._snap = Snapshot(frame, status, level, detail, score, progress, banner)

    def _open_camera(self) -> Camera:
        s = self.settings
        self._set("Initializing Assistant", "info", "Starting camera...")
        cam = self._camera_factory(s.camera_index, s.frame_width, s.frame_height)
        cam.open()                                # raises CameraError
        self._set("Camera Ready", "ok")
        return cam

    @staticmethod
    def _box(img, face, color) -> None:
        cv2.rectangle(img, (face.x, face.y), (face.x + face.w, face.y + face.h), color, 2)

    # ------------------------------------------------------------ recognition
    def _identify(self, model, detector, gray, faces, allowed):
        """Compare the largest face with ALL registered people, then (Class mode) require the winner to be
        in the chosen section. Returns a dict: face, label, score, runner, person (accepted person or None),
        not_in_class (True if recognised but from another section)."""
        s = self.settings
        face = faces[0]
        probe = detector.preprocess(gray, face)
        if probe is None:
            return {"face": face, "label": -1, "score": 999.0, "runner": None, "person": None}
        label, score, runner = model.predict(probe)          # ranks ALL registered people
        ok = FaceModel.accept(score, runner, s.recognition_threshold, s.recognition_margin)
        person = self.db.get_person(label) if ok else None
        not_in_class = False
        if person is not None and allowed is not None and label not in allowed:
            person, not_in_class = None, True               # recognised, but belongs to another section
        return {"face": face, "label": label, "score": score, "runner": runner, "person": person,
                "not_in_class": not_in_class}

    def _recognition_session(self, continuous: bool, section: str = "") -> None:
        s = self.settings
        self._set("Initializing Assistant", "info", "Loading face model...")
        try:
            model = FaceModel.load()
        except NotRegisteredError as exc:
            self._set("No Registered Face", "warn", str(exc))
            self.events.put(("no_registration", str(exc)))
            return
        allowed = {p["id"] for p in self.db.list_people(section)} if section else None
        detector = self._detector_factory(s.min_face_size)
        liveness = LivenessTracker(s.liveness_motion_check, s.liveness_motion_min,
                                   s.liveness_blink_check)
        need = s.required_consecutive_matches
        idle_text = (f"Class {section}: waiting for the next student" if section else "Ready - waiting for the next person") \
            if continuous else "Looking for Master Joey"
        cam = self._open_camera()
        try:
            started = last_ok = time.monotonic()
            streak_label: Optional[int] = None
            consecutive = 0
            banner: Optional[dict] = None
            banner_until = 0.0
            cool: dict = {}
            while not self._stop.is_set():
                frame = cam.read()
                now = time.monotonic()
                if frame is None:
                    if now - last_ok > 3.0:
                        raise CameraError("The camera stopped delivering pictures (unplugged, "
                                          "shutter closed, or taken by another app).")
                    time.sleep(0.01)
                    continue
                last_ok = now

                if banner is not None:                       # result banner is on screen
                    if now < banner_until:
                        self._set(banner["status"], banner["level"], banner["detail"], frame.copy(),
                                  None, 1.0, banner)
                        continue
                    banner = None

                if (not continuous and s.recognition_timeout_seconds
                        and now - started > s.recognition_timeout_seconds):
                    self._set("Recognition Timed Out", "warn",
                              "No confirmed match before the timeout. Click Start to retry.", frame)
                    self.events.put(("timeout",))
                    return

                gray = detector.to_gray(frame)
                faces = detector.detect(gray)
                shown = frame.copy()

                if not faces:
                    streak_label, consecutive = None, 0
                    liveness.reset()
                    self._set(idle_text, "info", "No face detected", shown)
                    continue

                r = self._identify(model, detector, gray, faces, allowed)
                face, label, score, person = r["face"], r["label"], r["score"], r["person"]

                if person is None:
                    streak_label, consecutive = None, 0
                    liveness.reset()
                    self._box(shown, face, AMBER if r["not_in_class"] else RED)
                    if r["not_in_class"]:
                        self._set("Not In This Class", "warn", "Recognised, but not a member of this class - not logged",
                                  shown, score)
                    else:
                        self._set("Unknown Face", "error", "Not recognised - no greeting", shown, score)
                    continue

                if label != streak_label:                    # a different person: start over
                    streak_label, consecutive = label, 0
                    liveness.reset()
                eyes = detector.count_eyes(gray, face) if liveness.needs_eyes else None
                liveness.update(face, eyes)
                consecutive += 1

                if consecutive >= need and liveness.is_live():
                    self._box(shown, face, GREEN)
                    self._set("Identity Confirmed", "ok", person["name"], shown, score, 1.0)
                    stamp = self._clock()
                    if not continuous:
                        self.voice.speak(greeting_for(person, "welcome", stamp))
                        banner = {"kind": "welcome", "title": "WELCOME BACK", "subtitle": person["name"],
                                  "level": "ok", "status": config.WELCOME_BANNER if person["is_owner"]
                                  else f"Welcome, {person['name']}", "detail": "Recognition stopped."}
                        self._set(banner["status"], "ok", banner["detail"], shown, score, 1.0, banner)
                        self.events.put(("welcome", person["name"]))
                        return
                    # ---- attendance mode
                    if now < cool.get(label, 0.0):
                        banner = {"kind": "dup", "title": person["name"], "subtitle": "Already recorded",
                                  "level": "info", "status": "Already Recorded", "detail": person["name"]}
                    else:
                        res = self.db.log_scan(label, stamp, s.late_after, s.min_minutes_between_scans)
                        cool[label] = now + s.scan_cooldown_seconds
                        banner = self._attendance_banner(person, res)
                        if res["kind"] != "duplicate":
                            self.voice.speak(greeting_for(person, res["kind"], stamp))
                            self.events.put(("attendance", {
                                "person_id": label, "name": person["name"], "kind": res["kind"],
                                "time": res["time"], "late": res["late"]}))
                    banner_until = now + s.banner_seconds
                    streak_label, consecutive = None, 0
                    liveness.reset()
                    self._set(banner["status"], banner["level"], banner["detail"], shown, score, 1.0, banner)
                    continue

                progress = min(consecutive / need, 1.0)
                self._box(shown, face, BLUE)
                detail = liveness.hint() if consecutive >= need else \
                    f"{person['name']}: verifying {consecutive}/{need}"
                self._set("Face Detected", "info", detail, shown, score, progress)
            self._set("Recognition Stopped", "info", "")
        finally:
            cam.release()

    @staticmethod
    def _attendance_banner(person: dict, res: dict) -> dict:
        t = res["time"].strftime("%I:%M %p").lstrip("0")
        if res["kind"] == "time_in":
            sub, lvl = f"TIME IN  {t}" + ("  (LATE)" if res["late"] else ""), "warn" if res["late"] else "ok"
            status = "Time In Recorded"
        elif res["kind"] == "time_out":
            sub, lvl, status = f"TIME OUT  {t}", "ok", "Time Out Recorded"
        else:
            sub, lvl, status = f"Already recorded at {t}", "info", "Already Recorded"
        return {"kind": res["kind"], "title": person["name"], "subtitle": sub, "level": lvl,
                "status": status, "detail": person["name"]}

    # ------------------------------------------------------------ registration
    def _registration_session(self, person_id: int) -> None:
        s = self.settings
        person = self.db.get_person(person_id)
        if person is None:
            raise ModelError("That person no longer exists.")
        if s.require_consent and not person["is_owner"] and person.get("consent") != "granted":
            raise ConsentError(f"No consent is recorded for {person['name']}. Record the parent/guardian's "
                               "consent first (People page > Consent...). Learners without consent use manual attendance.")
        self._set("Initializing Assistant", "info", "Loading face detector...")
        detector = self._detector_factory(s.min_face_size)
        reg = FaceRegistration(detector, s.registration_samples, person_id)
        cam = self._open_camera()
        try:
            reg.begin()
            last_save = 0.0
            last_ok = time.monotonic()
            detail = reg.current_pose()
            while not self._stop.is_set() and not reg.done:
                frame = cam.read()
                now = time.monotonic()
                if frame is None:
                    if now - last_ok > 3.0:
                        raise CameraError("The camera stopped delivering pictures.")
                    time.sleep(0.01)
                    continue
                last_ok = now
                gray = detector.to_gray(frame)
                faces = detector.detect(gray)
                shown = frame.copy()
                if now - last_save >= s.registration_interval_seconds:
                    saved, msg = reg.try_add_sample(gray, faces)
                    if saved:
                        last_save = now
                        detail = reg.current_pose()
                    else:
                        detail = msg
                for f in faces[:1]:
                    self._box(shown, f, BLUE if len(faces) == 1 else AMBER)
                self._set(f"Registering {person['name']} ({reg.count}/{reg.target})", "info", detail,
                          shown, None, reg.count / reg.target)
            if not reg.done:
                FaceRegistration.delete_person_faces(person_id)
                self._set("Registration Cancelled", "warn", "Partial samples were discarded.")
                return
            self._set("Training Face Model", "info", "Please wait...", progress=1.0)
            n = reg.train()
            self._set("Face Registered", "ok", f"{person['name']}: saved {n} samples.", progress=1.0)
            self.events.put(("registration_done", n, person_id))
        finally:
            cam.release()

    # ------------------------------------------------------------ accuracy test (Chapter 4 data)
    def _accuracy_session(self, truth_pid, truth_label: str, cond: dict, n_trials: int, section: str) -> None:
        """Run n trials. Each trial starts when a face first appears and ends when the system ACCEPTS someone
        (same rules as attendance: threshold, margin, consecutive matches, liveness) or after the timeout.
        Between trials the tester must step out of view. Nothing is spoken or logged as attendance."""
        s = self.settings
        self._set("Initializing Assistant", "info", "Loading face model...")
        try:
            model = FaceModel.load()
        except NotRegisteredError as exc:
            self._set("No Registered Face", "warn", str(exc))
            self.events.put(("no_registration", str(exc)))
            return
        allowed = {p["id"] for p in self.db.list_people(section)} if section else None
        detector = self._detector_factory(s.min_face_size)
        liveness = LivenessTracker(s.liveness_motion_check, s.liveness_motion_min, s.liveness_blink_check)
        need = s.required_consecutive_matches
        kind = "genuine" if truth_pid is not None else "impostor"
        session = datetime.now().strftime("%Y%m%d-%H%M%S")
        who = truth_label or ("registered person" if truth_pid else "impostor")
        cam = self._open_camera()
        done = 0
        last_ok = time.monotonic()

        def frame_and_faces():
            nonlocal last_ok
            frame = cam.read()
            now = time.monotonic()
            if frame is None:
                if now - last_ok > 3.0:
                    raise CameraError("The camera stopped delivering pictures.")
                time.sleep(0.01)
                return None, None, None
            last_ok = now
            gray = detector.to_gray(frame)
            return frame, gray, detector.detect(gray)

        try:
            for trial_no in range(1, n_trials + 1):
                if self._stop.is_set():
                    break
                head = f"Trial {trial_no}/{n_trials} - {who}"
                if trial_no > 1:                              # tester must leave the view first
                    empty_since, waited = None, time.monotonic()
                    while not self._stop.is_set():
                        frame, gray, faces = frame_and_faces()
                        if frame is None:
                            continue
                        now = time.monotonic()
                        if faces:
                            empty_since = None
                            self._set(head, "warn", "Step out of view, then step back in.", frame.copy())
                        else:
                            empty_since = empty_since or now
                            if now - empty_since >= 1.0:
                                break
                            self._set(head, "info", "Good - hold on...", frame.copy())
                        if now - waited > 60:
                            raise ModelError("The tester did not leave the camera view. Test stopped.")
                if self._stop.is_set():
                    break
                t0 = None                                     # trial clock starts at the first face
                waited = time.monotonic()
                streak_label, consecutive, blocked = None, 0, False
                min_dist, min_label, min_runner = None, None, None
                accepted = None
                liveness.reset()
                while not self._stop.is_set():
                    frame, gray, faces = frame_and_faces()
                    if frame is None:
                        continue
                    now = time.monotonic()
                    shown = frame.copy()
                    if t0 is None:
                        if not faces:
                            self._set(head, "info", "Present your face to the camera.", shown)
                            if now - waited > 60:
                                raise ModelError("No face appeared for 60 seconds. Test stopped.")
                            continue
                        t0 = now
                    if now - t0 > s.trial_timeout_seconds:
                        break
                    if not faces:
                        streak_label, consecutive = None, 0
                        liveness.reset()
                        self._set(head, "info", "Face lost - keep looking at the camera.", shown)
                        continue
                    r = self._identify(model, detector, gray, faces, allowed)
                    if r["label"] != -1 and (min_dist is None or r["score"] < min_dist):
                        min_dist, min_label, min_runner = r["score"], r["label"], r["runner"]
                    if r["person"] is None:
                        streak_label, consecutive = None, 0
                        liveness.reset()
                        self._box(shown, r["face"], RED)
                        self._set(head, "error", "Not accepted yet...", shown, r["score"])
                        continue
                    if r["label"] != streak_label:
                        streak_label, consecutive = r["label"], 0
                        liveness.reset()
                    liveness.update(r["face"], detector.count_eyes(gray, r["face"]) if liveness.needs_eyes else None)
                    consecutive += 1
                    if consecutive >= need and not liveness.is_live():
                        blocked = True
                    if consecutive >= need and liveness.is_live():
                        accepted = r["label"]
                        self._box(shown, r["face"], GREEN)
                        self._set(head, "ok", f"Accepted as {r['person']['name']}", shown, r["score"], 1.0)
                        break
                    self._box(shown, r["face"], BLUE)
                    self._set(head, "info", f"Verifying {consecutive}/{need}", shown, r["score"], min(consecutive / need, 1.0))
                if self._stop.is_set() or t0 is None:
                    break
                secs = time.monotonic() - t0
                correct = (accepted is not None and accepted == truth_pid) if kind == "genuine" else accepted is None
                rec = {"session": session, "ts": self._clock().strftime("%Y-%m-%d %H:%M:%S"), "truth_kind": kind,
                       "truth_person_id": truth_pid, "truth_label": truth_label, "lighting": cond.get("lighting", ""),
                       "distance": cond.get("distance", ""), "accessory": cond.get("accessory", ""),
                       "note": cond.get("note", ""), "trial_no": trial_no,
                       "outcome": "accepted" if accepted is not None else "timeout",
                       "accepted_person_id": accepted, "correct": 1 if correct else 0, "min_dist": min_dist,
                       "min_label": min_label, "runner_up": min_runner, "seconds": round(secs, 2),
                       "liveness_blocked": 1 if blocked and accepted is None else 0,
                       "threshold": s.recognition_threshold, "margin": s.recognition_margin}
                self.db.add_trial(rec)
                done += 1
                self.events.put(("trial", rec))
            self._set("Accuracy test finished" if done == n_trials else "Accuracy test stopped", "ok" if done == n_trials else "warn",
                      f"{done}/{n_trials} trial(s) recorded.", progress=1.0)
            self.events.put(("trials_done", session, done))
        finally:
            cam.release()
