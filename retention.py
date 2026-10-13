"""Data-retention helpers for schools: end-of-year clean-up and stale face-data detection."""
from __future__ import annotations

from datetime import datetime

from db import Database
from face_registration import FaceRegistration


def end_school_year(db: Database) -> dict:
    """Delete the face data of every student (everyone except the owner profile) and reset their
    consent to 'none'. People and attendance records are KEPT (export/back them up first)."""
    people = [p for p in db.list_people() if not p["is_owner"]]
    ids = [p["id"] for p in people]
    faces = FaceRegistration.delete_faces_for(ids)
    db.reset_consent(ids)
    db.log_audit("end_school_year", f"{len(ids)} people, face data deleted for {faces}, consent reset")
    return {"people": len(ids), "faces_deleted": faces}


def stale_people(db: Database, days: int, now: datetime | None = None) -> list[int]:
    """Ids whose face data is older than `days` (0 = feature off) - they should be re-registered."""
    if days <= 0:
        return []
    out = []
    for p in db.list_people():
        age = FaceRegistration.age_days(p["id"], now)
        if age is not None and age >= days:
            out.append(p["id"])
    return out
