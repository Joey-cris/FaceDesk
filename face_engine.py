"""Local face detection, LBPH recognition and basic liveness safeguards.

NOTE: This file is deliberately NOT called face_recognition.py. A popular PyPI package
with that exact name exists and would conflict with it.

Everything here runs locally with OpenCV. No image or model ever leaves the computer.
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

import config

log = logging.getLogger("assistant.engine")

FACE_SIZE = 200  # every face is normalised to FACE_SIZE x FACE_SIZE


class ModelError(Exception):
    """Face model is missing or corrupted (message is user-readable)."""


class NotRegisteredError(ModelError):
    pass


class ConsentError(ModelError):
    """Registration refused because no consent is recorded."""


# ------------------------------------------------------------------ file helpers
def imread_gray(path: Path):
    """cv2.imread fails on non-ASCII Windows paths; this does not."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    except OSError:
        return None


def imwrite_png(path: Path, image) -> bool:
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        return False
    buf.tofile(str(path))
    return True


def _load_cascade(filename: str) -> cv2.CascadeClassifier:
    path = Path(cv2.data.haarcascades) / filename
    cascade = cv2.CascadeClassifier(str(path))
    if not cascade.empty():
        return cascade
    # Fallback for non-ASCII install paths: parse the XML from memory.
    try:
        xml = path.read_text(encoding="utf-8")
        fs = cv2.FileStorage(xml, cv2.FILE_STORAGE_READ | cv2.FILE_STORAGE_MEMORY)
        cascade = cv2.CascadeClassifier()
        if cascade.read(fs.getFirstTopLevelNode()):
            return cascade
    except (OSError, cv2.error):
        pass
    raise ModelError(f"OpenCV resource '{filename}' could not be loaded. Re-run setup.bat.")


# ------------------------------------------------------------------ detection
@dataclass
class Face:
    x: int
    y: int
    w: int
    h: int

    @property
    def area(self) -> int:
        return self.w * self.h


class FaceDetector:
    def __init__(self, min_size: int = 100):
        try:
            self._face = _load_cascade("haarcascade_frontalface_alt2.xml")
        except ModelError:
            self._face = _load_cascade("haarcascade_frontalface_default.xml")
        self._eye = _load_cascade("haarcascade_eye.xml")
        self.min_size = min_size
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    @staticmethod
    def to_gray(frame):
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame

    def detect(self, gray) -> list[Face]:
        eq = cv2.equalizeHist(gray)
        rects = self._face.detectMultiScale(
            eq, scaleFactor=1.1, minNeighbors=6,
            minSize=(self.min_size, self.min_size),
        )
        faces = [Face(int(x), int(y), int(w), int(h)) for (x, y, w, h) in rects]
        faces.sort(key=lambda f: f.area, reverse=True)
        return faces

    def preprocess(self, gray, face: Face):
        """Consistent normalisation used for BOTH registration and recognition."""
        H, W = gray.shape[:2]
        x0, y0 = max(face.x, 0), max(face.y, 0)
        x1, y1 = min(face.x + face.w, W), min(face.y + face.h, H)
        crop = gray[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        crop = cv2.resize(crop, (FACE_SIZE, FACE_SIZE), interpolation=cv2.INTER_AREA)
        return self._clahe.apply(crop)

    def count_eyes(self, gray, face: Face) -> int:
        roi = gray[face.y: face.y + int(face.h * 0.6), face.x: face.x + face.w]
        if roi.size == 0:
            return 0
        eyes = self._eye.detectMultiScale(
            cv2.equalizeHist(roi), scaleFactor=1.1, minNeighbors=5,
            minSize=(max(face.w // 10, 12), max(face.w // 10, 12)),
        )
        return len(eyes)

    @staticmethod
    def sharpness(face_img) -> float:
        return float(cv2.Laplacian(face_img, cv2.CV_64F).var())


# ------------------------------------------------------------------ model
def _augment(img):
    """Yield lighting / pose variations of a normalised face for more robust training."""
    yield img
    for alpha, beta in ((0.8, -15), (1.2, 15), (1.0, 25), (1.0, -25)):
        yield cv2.convertScaleAbs(img, alpha=alpha, beta=beta)
    c = FACE_SIZE / 2
    for angle in (-6, 6):
        m = cv2.getRotationMatrix2D((c, c), angle, 1.0)
        yield cv2.warpAffine(img, m, (FACE_SIZE, FACE_SIZE), borderMode=cv2.BORDER_REPLICATE)
    yield cv2.GaussianBlur(img, (3, 3), 0)


def _new_recognizer():
    if not hasattr(cv2, "face"):
        raise ModelError(
            "OpenCV 'face' module missing. Install opencv-contrib-python (and uninstall "
            "opencv-python): pip uninstall opencv-python && pip install opencv-contrib-python"
        )
    return cv2.face.LBPHFaceRecognizer_create(radius=1, neighbors=8, grid_x=8, grid_y=8)


class FaceModel:
    """Wraps the LBPH recogniser. Labels are person ids (data/faces/<id>/)."""

    def __init__(self):
        self._rec = None

    # -- samples on disk
    @staticmethod
    def load_samples() -> dict:
        """Return {person_id: [normalised face images]} from data/faces/<id>/sample_*.png."""
        out: dict = {}
        root = config.FACES_DIR
        if not root.exists():
            return out
        for d in sorted(root.iterdir()):
            if d.is_dir() and d.name.isdigit():
                imgs = [i for i in (imread_gray(p) for p in sorted(d.glob("sample_*.png")))
                        if i is not None and i.shape == (FACE_SIZE, FACE_SIZE)]
                if imgs:
                    out[int(d.name)] = imgs
        return out

    # -- training
    @staticmethod
    def train_from_samples(samples: dict) -> "FaceModel":
        total = sum(len(v) for v in samples.values())
        if total < 5:
            raise ModelError("Not enough face samples to train (need at least 5).")
        imgs, labels = [], []
        for label, person_imgs in samples.items():
            for img in person_imgs:
                for a in _augment(img):
                    imgs.append(a)
                    labels.append(int(label))
        rec = _new_recognizer()
        rec.train(imgs, np.array(labels, dtype=np.int32))
        m = FaceModel()
        m._rec = rec
        return m

    def save(self, sample_count: int, people: int = 1) -> None:
        config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
        tmp = config.MODEL_FILE.with_suffix(".tmp")
        try:
            self._rec.write(str(tmp))
            if tmp.exists():
                tmp.replace(config.MODEL_FILE)
        except cv2.error as exc:
            # e.g. non-ASCII path on Windows; the model is rebuilt from samples on load.
            log.warning("Could not write model file (%s); will rebuild from samples on load", exc)
        meta = {
            "created": datetime.now().isoformat(timespec="seconds"),
            "samples": sample_count,
            "people": people,
            "opencv": cv2.__version__,
            "face_size": FACE_SIZE,
        }
        config.MODEL_META_FILE.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # -- loading
    @staticmethod
    def load() -> "FaceModel":
        samples = FaceModel.load_samples()
        if not config.MODEL_FILE.exists() and not samples:
            raise NotRegisteredError("No face is registered yet. Add a person and register a face first.")
        m = FaceModel()
        if config.MODEL_FILE.exists():
            try:
                rec = _new_recognizer()
                rec.read(str(config.MODEL_FILE))
                rec.predict(np.zeros((FACE_SIZE, FACE_SIZE), np.uint8))   # sanity check
                m._rec = rec
                return m
            except (cv2.error, ModelError) as exc:
                if isinstance(exc, ModelError) and "face' module" in str(exc):
                    raise
                log.error("Model file unreadable/corrupted: %s", exc)
        # Model missing or corrupted -> rebuild from the stored samples if possible.
        if sum(len(v) for v in samples.values()) < 5:
            raise ModelError("The face model is corrupted and cannot be rebuilt. "
                             "Delete the face data and register again.")
        log.warning("Rebuilding model from %d people", len(samples))
        rebuilt = FaceModel.train_from_samples(samples)
        rebuilt.save(sum(len(v) for v in samples.values()), len(samples))
        return rebuilt

    # -- prediction
    def predict(self, face_img) -> tuple:
        """Return (person_id, best_distance, runner_up_distance).

        Always compares against EVERY registered person. (Restricting the comparison to one class would
        remove the runner-up evidence the margin rule relies on and make strangers easier to accept.)

        Distances are LBPH distances (lower = more similar). `runner_up_distance` is the best
        distance to ANY OTHER registered person (None if only one person is registered).
        """
        collector = cv2.face.StandardCollector_create()
        self._rec.predict_collect(face_img, collector)
        best: dict = {}
        for label, dist in collector.getResults(False):
            if dist < best.get(label, 1e18):
                best[label] = dist
        if not best:
            return -1, 1e9, None
        ranked = sorted(best.items(), key=lambda kv: kv[1])
        label, dist = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else None
        return int(label), float(dist), (float(runner_up) if runner_up is not None else None)

    @staticmethod
    def accept(dist: float, runner_up, threshold: float, margin: float) -> bool:
        """Accept a match only if it is close enough AND clearly better than every other person.

        A stranger is typically about equally far from everybody (ratio ~1.0), while the real
        person is much closer to their own samples than to anybody else's.
        """
        if dist > threshold:
            return False
        if runner_up is not None and margin > 1.0 and runner_up < dist * margin:
            return False
        return True


# ------------------------------------------------------------------ liveness
class LivenessTracker:
    """BASIC liveness safeguards. They raise the bar slightly; they do NOT stop a
    determined attacker (e.g. a video of you, or a photo being moved by hand).

    * motion check : the face must move a little (head drift, breathing). A photo or
                     screen lying perfectly still on a desk fails.
    * blink check  : optional; the eyes must be seen open, then closed, then open.
    """

    def __init__(self, motion_check: bool, motion_min: float, blink_check: bool):
        self.motion_check, self.motion_min, self.blink_check = motion_check, motion_min, blink_check
        self.reset()

    def reset(self) -> None:
        self._centers: deque = deque(maxlen=90)
        self._open = 0
        self._closed = 0
        self._blinked = False

    def update(self, face: Face, eyes: int | None = None) -> None:
        self._centers.append((face.x + face.w / 2, face.y + face.h / 2, face.w))
        if eyes is not None:
            if eyes >= 2:
                if self._open >= 2 and 1 <= self._closed <= 8:
                    self._blinked = True
                self._open += 1
                self._closed = 0
            elif eyes == 0 and self._open >= 2:
                self._closed += 1

    def motion(self) -> float:
        if len(self._centers) < 4:
            return 0.0
        a = np.array(self._centers, dtype=float)
        ref = float(np.median(a[:, 2]))
        spread = max(np.ptp(a[:, 0]), np.ptp(a[:, 1]), np.ptp(a[:, 2]))
        return float(spread / ref) if ref > 0 else 0.0

    @property
    def needs_eyes(self) -> bool:
        return self.blink_check

    def is_live(self) -> bool:
        if self.motion_check and self.motion() < self.motion_min:
            return False
        if self.blink_check and not self._blinked:
            return False
        return True

    def hint(self) -> str:
        if self.motion_check and self.motion() < self.motion_min:
            return "Verifying you are present - please move your head slightly"
        if self.blink_check and not self._blinked:
            return "Verifying you are present - please blink"
        return ""
