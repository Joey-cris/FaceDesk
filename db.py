"""Local SQLite database: people, attendance, audit log, settings (data/facedesk.db). Nothing is uploaded."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Optional

import config

TS = "%Y-%m-%d %H:%M:%S"


def _fmt(dt: datetime) -> str:
    return dt.strftime(TS)


class Database:
    def __init__(self, path=None):
        self.path = path or config.DB_FILE
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with self._tx() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS people(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT '',
                is_owner INTEGER NOT NULL DEFAULT 0,
                created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attendance(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                day TEXT NOT NULL,
                time_in TEXT NOT NULL,
                time_out TEXT,
                late INTEGER NOT NULL DEFAULT 0,
                UNIQUE(person_id, day));
            CREATE INDEX IF NOT EXISTS idx_att_day ON attendance(day);
            CREATE TABLE IF NOT EXISTS audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS accuracy_trial(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session TEXT NOT NULL, ts TEXT NOT NULL,
                truth_kind TEXT NOT NULL, truth_person_id INTEGER, truth_label TEXT NOT NULL DEFAULT '',
                lighting TEXT NOT NULL DEFAULT '', distance TEXT NOT NULL DEFAULT '',
                accessory TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
                trial_no INTEGER NOT NULL, outcome TEXT NOT NULL,
                accepted_person_id INTEGER, correct INTEGER NOT NULL,
                min_dist REAL, min_label INTEGER, runner_up REAL, seconds REAL NOT NULL,
                liveness_blocked INTEGER NOT NULL DEFAULT 0, threshold REAL, margin REAL);
            """)
            # upgrade databases created by older versions
            self._add_col(c, "people", "student_id", "TEXT NOT NULL DEFAULT ''")
            self._add_col(c, "people", "section", "TEXT NOT NULL DEFAULT ''")
            self._add_col(c, "people", "consent", "TEXT NOT NULL DEFAULT 'none'")
            self._add_col(c, "people", "consent_date", "TEXT NOT NULL DEFAULT ''")
            self._add_col(c, "people", "consent_by", "TEXT NOT NULL DEFAULT ''")
            self._add_col(c, "people", "consent_ref", "TEXT NOT NULL DEFAULT ''")
            self._add_col(c, "attendance", "note", "TEXT NOT NULL DEFAULT ''")
            self._add_col(c, "attendance", "source", "TEXT NOT NULL DEFAULT 'face'")

    @staticmethod
    def _add_col(c, table: str, col: str, decl: str) -> None:
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    @contextmanager
    def _tx(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------ meta / audit
    def get_meta(self, key: str, default=None):
        with self._tx() as c:
            r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return r["value"] if r else default

    def set_meta(self, key: str, value: str) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, str(value)))

    def del_meta(self, key: str) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM meta WHERE key=?", (key,))

    def log_audit(self, action: str, detail: str = "", actor: str = "admin", now: Optional[datetime] = None) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO audit(ts, actor, action, detail) VALUES(?,?,?,?)",
                      (_fmt(now or datetime.now()), actor, action, detail))

    def list_audit(self, limit: int = 300, search: str = "") -> list[dict]:
        q, args = "SELECT * FROM audit ", []
        if search.strip():
            q += "WHERE lower(action) LIKE ? OR lower(detail) LIKE ? "
            like = f"%{search.strip().lower()}%"
            args += [like, like]
        q += "ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._tx() as c:
            return [dict(r) for r in c.execute(q, args)]

    def backup_to(self, dest) -> None:
        """Consistent copy of the database using SQLite's online backup API."""
        src = sqlite3.connect(str(self.path))
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

    # ------------------------------------------------------------ people
    def ensure_owner(self) -> int:
        with self._tx() as c:
            row = c.execute("SELECT id FROM people WHERE is_owner=1 LIMIT 1").fetchone()
            if row:
                return int(row["id"])
            cur = c.execute("INSERT INTO people(name, role, is_owner, created) VALUES(?,?,1,?)",
                            (config.USER_DISPLAY_NAME, "Owner", _fmt(datetime.now())))
            return int(cur.lastrowid)

    @staticmethod
    def _clean(text) -> str:
        return " ".join(str(text or "").split())

    def add_person(self, name: str, role: str = "", section: str = "", student_id: str = "") -> int:
        name, role, section, student_id = map(self._clean, (name, role, section, student_id))
        if not name:
            raise ValueError("Name cannot be empty.")
        with self._tx() as c:
            if c.execute("SELECT 1 FROM people WHERE lower(name)=lower(?)", (name,)).fetchone():
                raise ValueError(f"'{name}' already exists.")
            if student_id and c.execute("SELECT 1 FROM people WHERE lower(student_id)=lower(?)",
                                        (student_id,)).fetchone():
                raise ValueError(f"Student ID '{student_id}' already exists.")
            cur = c.execute("INSERT INTO people(name, role, is_owner, created, student_id, section) "
                            "VALUES(?,?,0,?,?,?)", (name, role, _fmt(datetime.now()), student_id, section))
            return int(cur.lastrowid)

    def update_person(self, person_id: int, name: str, role: str, section=None, student_id=None) -> None:
        name, role = self._clean(name), self._clean(role)
        if not name:
            raise ValueError("Name cannot be empty.")
        with self._tx() as c:
            if c.execute("SELECT 1 FROM people WHERE lower(name)=lower(?) AND id<>?", (name, person_id)).fetchone():
                raise ValueError(f"'{name}' already exists.")
            if student_id is not None:
                student_id = self._clean(student_id)
                if student_id and c.execute("SELECT 1 FROM people WHERE lower(student_id)=lower(?) AND id<>?",
                                            (student_id, person_id)).fetchone():
                    raise ValueError(f"Student ID '{student_id}' already exists.")
                c.execute("UPDATE people SET student_id=? WHERE id=?", (student_id, person_id))
            if section is not None:
                c.execute("UPDATE people SET section=? WHERE id=?", (self._clean(section), person_id))
            c.execute("UPDATE people SET name=?, role=? WHERE id=?", (name, role, person_id))

    CONSENT_STATUSES = ("none", "granted", "declined", "withdrawn")

    def set_consent(self, person_id: int, status: str, by: str = "", ref: str = "", day=None) -> None:
        """Record parental / participant consent. status: granted | declined | withdrawn | none."""
        if status not in self.CONSENT_STATUSES:
            raise ValueError(f"Unknown consent status '{status}'.")
        if status == "granted" and not self._clean(by):
            raise ValueError("Enter the name of the parent/guardian (or the person) who gave consent.")
        with self._tx() as c:
            if not c.execute("SELECT 1 FROM people WHERE id=?", (person_id,)).fetchone():
                raise ValueError("That person does not exist.")
            c.execute("UPDATE people SET consent=?, consent_by=?, consent_ref=?, consent_date=? WHERE id=?",
                      (status, self._clean(by), self._clean(ref),
                       (day or date.today()).isoformat() if status != "none" else "", person_id))

    def reset_consent(self, person_ids) -> None:
        with self._tx() as c:
            for pid in person_ids:
                c.execute("UPDATE people SET consent='none', consent_by='', consent_ref='', consent_date='' WHERE id=?", (pid,))

    # ------------------------------------------------------------ accuracy-test trials
    TRIAL_FIELDS = ("session", "ts", "truth_kind", "truth_person_id", "truth_label", "lighting", "distance",
                    "accessory", "note", "trial_no", "outcome", "accepted_person_id", "correct", "min_dist",
                    "min_label", "runner_up", "seconds", "liveness_blocked", "threshold", "margin")

    def add_trial(self, t: dict) -> int:
        cols = [k for k in self.TRIAL_FIELDS if k in t]
        with self._tx() as c:
            cur = c.execute(f"INSERT INTO accuracy_trial({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                            [t[k] for k in cols])
            return int(cur.lastrowid)

    def list_trials(self, session: str = "") -> list[dict]:
        q, args = "SELECT * FROM accuracy_trial ", []
        if session:
            q += "WHERE session=? "
            args.append(session)
        with self._tx() as c:
            return [dict(r) for r in c.execute(q + "ORDER BY id", args)]

    def clear_trials(self) -> int:
        with self._tx() as c:
            return c.execute("DELETE FROM accuracy_trial").rowcount

    def delete_person(self, person_id: int) -> None:
        """Deletes the person AND their attendance records (cascade). The owner is protected."""
        with self._tx() as c:
            c.execute("DELETE FROM people WHERE id=? AND is_owner=0", (person_id,))

    def get_person(self, person_id: int) -> Optional[dict]:
        with self._tx() as c:
            r = c.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
            return dict(r) if r else None

    def list_people(self, section: str = "") -> list[dict]:
        q, args = "SELECT * FROM people ", []
        if section:
            q += "WHERE section=? "
            args.append(section)
        q += "ORDER BY is_owner DESC, lower(name)"
        with self._tx() as c:
            return [dict(r) for r in c.execute(q, args)]

    def list_sections(self) -> list[str]:
        with self._tx() as c:
            return [r["section"] for r in c.execute(
                "SELECT DISTINCT section FROM people WHERE section<>'' ORDER BY lower(section)")]

    # ------------------------------------------------------------ attendance
    def log_scan(self, person_id: int, now: datetime, late_after: str = "08:30",
                 min_gap_minutes: int = 30) -> dict:
        """First scan of the day = Time In; a scan >= gap later = Time Out; closer scans = duplicate."""
        day = now.strftime("%Y-%m-%d")
        hh, mm = (int(x) for x in late_after.split(":"))
        gap = timedelta(minutes=min_gap_minutes)
        with self._tx() as c:
            row = c.execute("SELECT * FROM attendance WHERE person_id=? AND day=?", (person_id, day)).fetchone()
            if row is None:
                late = 1 if (now.hour, now.minute) > (hh, mm) else 0
                c.execute("INSERT INTO attendance(person_id, day, time_in, late, source) VALUES(?,?,?,?, 'face')",
                          (person_id, day, _fmt(now), late))
                return {"kind": "time_in", "time": now, "late": bool(late)}
            t_in = datetime.strptime(row["time_in"], TS)
            last = datetime.strptime(row["time_out"], TS) if row["time_out"] else t_in
            if now - last >= gap:
                c.execute("UPDATE attendance SET time_out=? WHERE id=?", (_fmt(now), row["id"]))
                return {"kind": "time_out", "time": now, "late": bool(row["late"])}
            return {"kind": "duplicate", "time": last, "late": bool(row["late"]), "time_in": t_in}

    def set_manual_attendance(self, person_id: int, time_in: datetime, time_out: Optional[datetime],
                              late_after: str, note: str) -> str:
        """Create/replace the attendance record of that day by hand. A reason (note) is mandatory."""
        note = self._clean(note)
        if not note:
            raise ValueError("A reason is required for manual entries.")
        if time_out is not None and time_out < time_in:
            raise ValueError("Time Out cannot be earlier than Time In.")
        hh, mm = (int(x) for x in late_after.split(":"))
        late = 1 if (time_in.hour, time_in.minute) > (hh, mm) else 0
        day = time_in.strftime("%Y-%m-%d")
        with self._tx() as c:
            if not c.execute("SELECT 1 FROM people WHERE id=?", (person_id,)).fetchone():
                raise ValueError("That person does not exist.")
            existed = c.execute("SELECT 1 FROM attendance WHERE person_id=? AND day=?", (person_id, day)).fetchone()
            c.execute("""INSERT INTO attendance(person_id, day, time_in, time_out, late, note, source)
                         VALUES(?,?,?,?,?,?, 'manual')
                         ON CONFLICT(person_id, day) DO UPDATE SET time_in=excluded.time_in,
                         time_out=excluded.time_out, late=excluded.late, note=excluded.note, source='manual'""",
                      (person_id, day, _fmt(time_in), _fmt(time_out) if time_out else None, late, note))
        return "updated" if existed else "created"

    def attendance_range(self, start: date, end: date, search: str = "", section: str = "") -> list[dict]:
        q = ("SELECT a.id, a.day, p.id AS person_id, p.name, p.role, p.section, p.student_id, a.time_in, "
             "a.time_out, a.late, a.note, a.source "
             "FROM attendance a JOIN people p ON p.id=a.person_id WHERE a.day BETWEEN ? AND ? ")
        args: list = [start.isoformat(), end.isoformat()]
        if section:
            q += "AND p.section=? "
            args.append(section)
        if search.strip():
            q += "AND (lower(p.name) LIKE ? OR lower(p.role) LIKE ? OR lower(p.student_id) LIKE ?) "
            like = f"%{search.strip().lower()}%"
            args += [like, like, like]
        q += "ORDER BY a.day DESC, a.time_in DESC"
        with self._tx() as c:
            return [dict(r) for r in c.execute(q, args)]

    def attendance_for_day(self, day: date, section: str = "") -> list[dict]:
        return self.attendance_range(day, day, section=section)

    def stats(self, day: date, registered_ids: set, section: str = "") -> dict:
        rows = self.attendance_for_day(day, section)
        present_ids = {r["person_id"] for r in rows}
        return {"registered": len(registered_ids), "present": len(present_ids),
                "late": sum(1 for r in rows if r["late"]),
                "absent": len(set(registered_ids) - present_ids)}

    def counts_per_day(self, days: int, end: date, section: str = "") -> list[tuple]:
        start = end - timedelta(days=days - 1)
        q = ("SELECT a.day AS day, COUNT(*) AS n FROM attendance a JOIN people p ON p.id=a.person_id "
             "WHERE a.day BETWEEN ? AND ? ")
        args: list = [start.isoformat(), end.isoformat()]
        if section:
            q += "AND p.section=? "
            args.append(section)
        q += "GROUP BY a.day"
        with self._tx() as c:
            got = {r["day"]: r["n"] for r in c.execute(q, args)}
        return [((start + timedelta(days=i)), got.get((start + timedelta(days=i)).isoformat(), 0))
                for i in range(days)]

    def summary(self, start: date, end: date, section: str, holidays: set, today: date) -> list[dict]:
        """Per-person totals. School days = Mon-Fri, not holidays, not in the future, and not before
        the person was added. Absent = school days without any attendance record."""
        last = min(end, today)
        days = []
        d = start
        while d <= last:
            if d.weekday() < 5 and d not in holidays:
                days.append(d)
            d += timedelta(days=1)
        out = []
        rows = self.attendance_range(start, end, section=section)
        for p in self.list_people(section):
            created = datetime.strptime(p["created"], TS).date()
            mine = [x for x in days if x >= created]
            recs = {r["day"]: r for r in rows if r["person_id"] == p["id"]}
            present = sum(1 for x in mine if x.isoformat() in recs)
            late = sum(1 for x in mine if x.isoformat() in recs and recs[x.isoformat()]["late"])
            out.append({"name": p["name"], "student_id": p["student_id"], "section": p["section"],
                        "school_days": len(mine), "present": present, "late": late,
                        "absent": len(mine) - present,
                        "rate": round(100.0 * present / len(mine), 1) if mine else 0.0})
        return out
