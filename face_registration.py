"""Face registration for any number of people; training; deleting biometric data."""
from __future__ import annotations

import logging
import shutil

import config
from face_engine import (FACE_SIZE, Face, FaceDetector, FaceModel, ModelError,
                         imread_gray, imwrite_png)

log = logging.getLogger("assistant.registration")

POSES = [
    "Look straight at the camera",
    "Turn your head slightly LEFT",
    "Turn your head slightly RIGHT",
    "Tilt your head slightly UP",
    "Tilt your head slightly DOWN",
    "Look straight and relax / smile a little",
]


class FaceRegistration:
    def __init__(self, detector: FaceDetector, target_samples: int, person_id: int):
        self.detector = detector
        self.target = target_samples
        self.person_id = person_id
        self._count = 0

    # -- samples
    def begin(self) -> None:
        """Start a new registration for this person: wipe THEIR old samples only."""
        d = config.person_dir(self.person_id)
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    @property
    def done(self) -> bool:
        return self._count >= self.target

    def current_pose(self) -> str:
        per_pose = max(self.target // len(POSES), 1)
        return POSES[min(self._count // per_pose, len(POSES) - 1)]

    def try_add_sample(self, gray, faces: list[Face]) -> tuple[bool, str]:
        if not faces:
            return False, "No face visible - move into view"
        if len(faces) > 1:
            return False, "More than one face visible - only one person should be in view"
        img = self.detector.preprocess(gray, faces[0])
        if img is None:
            return False, "Face partly out of frame"
        mean = float(img.mean())
        if mean < 40 or mean > 215:
            return False, "Lighting too dark or too bright"
        if self.detector.sharpness(img) < 20:
            return False, "Image too blurry - hold still"
        self._count += 1
        path = config.person_dir(self.person_id) / f"sample_{self._count:04d}.png"
        if not imwrite_png(path, img):
            self._count -= 1
            return False, "Could not save sample (disk error)"
        return True, f"Sample {self._count}/{self.target}"

    # -- training (all people share one model)
    def train(self) -> int:
        samples = FaceModel.load_samples()
        mine = len(samples.get(self.person_id, []))
        if mine < 10:
            raise ModelError(f"Only {mine} usable samples were collected - need at least 10.")
        model = FaceModel.train_from_samples(samples)
        model.save(sum(len(v) for v in samples.values()), len(samples))
        log.info("Trained model: %d people, person %s has %d samples", len(samples), self.person_id, mine)
        return mine

    # -- static helpers
    @staticmethod
    def sample_count(person_id: int) -> int:
        d = config.person_dir(person_id)
        return len(list(d.glob("sample_*.png"))) if d.exists() else 0

    @staticmethod
    def registered_on(person_id: int):
        """When this person's face data was captured (oldest sample file), or None."""
        d = config.person_dir(person_id)
        files = list(d.glob("sample_*.png")) if d.exists() else []
        if not files:
            return None
        from datetime import datetime
        return datetime.fromtimestamp(min(f.stat().st_mtime for f in files))

    @staticmethod
    def age_days(person_id: int, now=None):
        from datetime import datetime
        t = FaceRegistration.registered_on(person_id)
        return None if t is None else (( now or datetime.now()) - t).days

    @staticmethod
    def delete_faces_for(person_ids) -> int:
        n = 0
        for pid in person_ids:
            d = config.person_dir(pid)
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        FaceRegistration.rebuild_model()
        return n

    @staticmethod
    def rebuild_model() -> None:
        """Retrain from whatever samples remain; remove the model if none remain."""
        samples = FaceModel.load_samples()
        if sum(len(v) for v in samples.values()) >= 5:
            FaceModel.train_from_samples(samples).save(sum(len(v) for v in samples.values()), len(samples))
        else:
            shutil.rmtree(config.MODEL_DIR, ignore_errors=True)
            config.MODEL_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def delete_person_faces(person_id: int) -> None:
        shutil.rmtree(config.person_dir(person_id), ignore_errors=True)
        FaceRegistration.rebuild_model()
        log.info("Deleted face data of person %s", person_id)

    @staticmethod
    def delete_all_faces() -> None:
        for d in (config.FACES_DIR, config.MODEL_DIR):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
        log.info("All biometric data deleted")

    @staticmethod
    def has_any_registration() -> bool:
        if config.MODEL_FILE.exists():
            return True
        return bool(FaceModel.load_samples())

    @staticmethod
    def migrate_legacy_samples(owner_id: int) -> int:
        """Upgrade from the single-user version: data/faces/sample_*.png -> data/faces/<owner>/."""
        legacy = sorted(config.FACES_DIR.glob("sample_*.png")) if config.FACES_DIR.exists() else []
        if not legacy:
            return 0
        dest = config.person_dir(owner_id)
        dest.mkdir(parents=True, exist_ok=True)
        for p in legacy:
            shutil.move(str(p), str(dest / p.name))
        if config.MODEL_FILE.exists():
            config.MODEL_FILE.unlink()                # old model used a different label; rebuild
        log.info("Migrated %d legacy samples to owner %s", len(legacy), owner_id)
        return len(legacy)
