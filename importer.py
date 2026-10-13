"""Import students/people from a CSV file (exported from Excel: File > Save As > CSV)."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from db import Database

ALIASES = {
    "name": {"name", "full name", "fullname", "student name", "student", "pangalan"},
    "student_id": {"id", "student id", "student_id", "studentid", "lrn", "student no", "student number", "id number"},
    "section": {"section", "class", "grade", "grade section", "year section", "course", "group"},
    "role": {"role", "type"},
}
TEMPLATE = [["Name", "Student ID", "Section", "Role"],
            ["Maria Santos", "2026-0001", "Grade 7-Newton", "Student"],
            ["Juan Dela Cruz", "2026-0002", "Grade 7-Newton", "Student"]]


@dataclass
class ImportResult:
    added: int = 0
    skipped: list = field(default_factory=list)


def write_template(path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(TEMPLATE)


def _map_headers(headers: list[str]) -> dict:
    mapping = {}
    for i, h in enumerate(headers):
        key = h.strip().lower().replace("_", " ") if h else ""
        for field_name, names in ALIASES.items():
            if key in names or key.replace(" ", "_") in names:
                mapping.setdefault(field_name, i)
    return mapping


def import_people(db: Database, path) -> ImportResult:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp1252")        # files saved by older Excel versions
    except OSError as exc:
        raise ValueError(f"Cannot read the file: {exc}")
    if not text.strip():
        raise ValueError("The file is empty.")
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(text.splitlines(), dialect))
    mapping = _map_headers(rows[0])
    if "name" not in mapping:
        raise ValueError("No 'Name' column found. The first row must contain headers such as: "
                         "Name, Student ID, Section, Role.")
    res = ImportResult()
    for line_no, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue
        get = lambda k: row[mapping[k]].strip() if k in mapping and mapping[k] < len(row) else ""
        name = get("name")
        if not name:
            res.skipped.append(f"Line {line_no}: no name")
            continue
        try:
            db.add_person(name, get("role") or "Student", get("section"), get("student_id"))
            res.added += 1
        except ValueError as exc:
            res.skipped.append(f"Line {line_no}: {exc}")
    return res
