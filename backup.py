"""Backup & restore. Backups are .zip files: facedesk.db (+ optionally face samples and model).

Face samples are BIOMETRIC data. They are only included if you tick the option - store such
backups somewhere secure. Automatic daily backups never include faces.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import config
from db import Database


class BackupError(Exception):
    pass


def create_backup(db: Database, dest, include_faces: bool = False) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / "facedesk.db"
        db.backup_to(db_copy)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps({
                "app": "FaceDesk", "version": config.VERSION, "include_faces": bool(include_faces),
                "created": datetime.now().isoformat(timespec="seconds")}))
            z.write(db_copy, "facedesk.db")
            if include_faces:
                for root in (config.FACES_DIR, config.MODEL_DIR):
                    if root.exists():
                        for f in root.rglob("*"):
                            if f.is_file():
                                z.write(f, f"{root.name}/{f.relative_to(root).as_posix()}")
    return dest


def auto_backup(db: Database, keep: int = 14, now: datetime | None = None) -> Path | None:
    """One database-only backup per day; prune old ones."""
    now = now or datetime.now()
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = config.BACKUP_DIR / f"auto_{now:%Y-%m-%d}.zip"
    made = None
    if not target.exists():
        made = create_backup(db, target, include_faces=False)
    old = sorted(config.BACKUP_DIR.glob("auto_*.zip"))
    for f in old[:-keep]:
        try:
            f.unlink()
        except OSError:
            pass
    return made


def _safe(name: str) -> bool:
    p = Path(name)
    return not p.is_absolute() and ".." not in p.parts and name != ""


def restore_backup(db: Database, src) -> dict:
    """Replace the current database (and faces, if included) with the backup's. A safety copy of
    the current state is saved first to backups/pre_restore_*.zip."""
    src = Path(src)
    try:
        z = zipfile.ZipFile(src)
    except (zipfile.BadZipFile, OSError) as exc:
        raise BackupError(f"Not a valid backup file: {exc}")
    with z:
        names = z.namelist()
        if "manifest.json" not in names or "facedesk.db" not in names:
            raise BackupError("This zip is not a FaceDesk backup (manifest.json / facedesk.db missing).")
        if not all(_safe(n) for n in names):
            raise BackupError("The backup contains unsafe file paths and was rejected.")
        manifest = json.loads(z.read("manifest.json"))
        with tempfile.TemporaryDirectory() as tmp:
            cand = Path(tmp) / "facedesk.db"
            cand.write_bytes(z.read("facedesk.db"))
            try:
                con = sqlite3.connect(str(cand))
                ok = con.execute("PRAGMA integrity_check").fetchone()[0]
                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                con.close()
            except sqlite3.DatabaseError as exc:
                raise BackupError(f"The backup database is damaged: {exc}")
            if ok != "ok" or not {"people", "attendance"} <= tables:
                raise BackupError("The backup database failed its integrity check.")
            config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            current_audit = db.list_audit(10 ** 9)          # the audit trail must survive a restore
            safety = create_backup(db, config.BACKUP_DIR / f"pre_restore_{datetime.now():%Y%m%d_%H%M%S}.zip",
                                   include_faces=True)
            shutil.copyfile(cand, config.DB_FILE)
            have = {(e["ts"], e["actor"], e["action"], e["detail"]) for e in db.list_audit(10 ** 9)}
            with db._tx() as c:
                for e in reversed(current_audit):
                    if (e["ts"], e["actor"], e["action"], e["detail"]) not in have:
                        c.execute("INSERT INTO audit(ts, actor, action, detail) VALUES(?,?,?,?)",
                                  (e["ts"], e["actor"], e["action"], e["detail"]))
        restored_faces = False
        if manifest.get("include_faces"):
            for d in (config.FACES_DIR, config.MODEL_DIR):
                shutil.rmtree(d, ignore_errors=True)
                d.mkdir(parents=True, exist_ok=True)
            for n in names:
                if n.startswith(("faces/", "model/")) and not n.endswith("/"):
                    out = config.DATA_DIR / n
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(z.read(n))
            restored_faces = True
    return {"safety_backup": str(safety), "faces_restored": restored_faces,
            "created": manifest.get("created", "")}
