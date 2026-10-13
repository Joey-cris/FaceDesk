"""FaceDesk interface: sidebar + Camera / Dashboard / People / Attendance Log / Admin pages.

All heavy work happens in controller.py's thread; this file only draws and reacts to clicks.
Protected pages (People, Attendance Log, Admin) and Settings ask for the admin PIN when one is set.
"""
from __future__ import annotations

import base64
import logging
import queue
import threading
import time
import tkinter as tk
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2

import backup
import config
import exporter
import importer
import metrics
import retention
import selfcheck
from camera import Camera
from controller import Controller
from db import Database
from face_engine import FaceDetector
from face_registration import FaceRegistration
from security import AdminAuth
from voice_assistant import VoiceAssistant

log = logging.getLogger("assistant.ui")

BG, PANEL, PANEL2, ACCENT = "#05080f", "#0b1220", "#0f1b2d", "#00b4ff"
TEXT, MUTED, BORDER = "#e6f1ff", "#6f87a6", "#17314f"
LEVEL_COLORS = {"info": "#5cc8ff", "ok": "#3ddc97", "warn": "#ffb84d", "error": "#ff5c6c"}
FONT = "Segoe UI"
VIEW_W, VIEW_H = 560, 420
ALL = "All sections"
PROTECTED = {"people", "attendance", "accuracy", "admin"}
IMPOSTOR = "Unregistered person (impostor)"
CONSENT_TEXT = {"granted": "✔ Granted", "declined": "✖ Declined", "withdrawn": "Withdrawn", "none": "—"}


def _show_desktop() -> None:
    """Press Win+D so the desktop is revealed (Windows only). Does nothing else."""
    import os
    if os.name != "nt":
        return
    try:
        import ctypes
        u = ctypes.windll.user32
        VK_LWIN, VK_D, UP = 0x5B, 0x44, 0x2
        u.keybd_event(VK_LWIN, 0, 0, 0); u.keybd_event(VK_D, 0, 0, 0)
        u.keybd_event(VK_D, 0, UP, 0); u.keybd_event(VK_LWIN, 0, UP, 0)
    except Exception:
        log.exception("show desktop failed")


def _t12(ts) -> str:
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").strftime("%I:%M %p").lstrip("0") if ts else ""


class GlowButton(tk.Button):
    def __init__(self, master, text, command, accent=False, **kw):
        self._base = ACCENT if accent else PANEL2
        super().__init__(master, text=text, command=command, bg=self._base, fg=BG if accent else TEXT,
                         activebackground="#33c6ff", activeforeground=BG, relief="flat", bd=0,
                         font=(FONT, 10, "bold"), cursor="hand2", pady=7, padx=8, highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=ACCENT,
                         disabledforeground="#3b4a60", **kw)
        self.bind("<Enter>", lambda e: self["state"] == "normal" and self.config(bg="#33c6ff" if accent else BORDER))
        self.bind("<Leave>", lambda e: self.config(bg=self._base))


def make_tree(parent, columns, widths, height=10):
    """Dark-styled ttk.Treeview with a scrollbar. columns = [(id, heading), ...]"""
    frame = tk.Frame(parent, bg=PANEL)
    tree = ttk.Treeview(frame, columns=[c for c, _ in columns], show="headings", height=height, selectmode="browse")
    for (cid, head), w in zip(columns, widths):
        tree.heading(cid, text=head, anchor="w")
        tree.column(cid, width=w, anchor="w")
    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    tree.tag_configure("late", foreground="#ffb84d")
    tree.tag_configure("ok", foreground="#3ddc97")
    return frame, tree


def make_entry(parent, var, width=14, show=""):
    return tk.Entry(parent, textvariable=var, width=width, show=show, bg=PANEL2, fg=TEXT, insertbackground=TEXT,
                    relief="flat", highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                    font=(FONT, 10))


class App:
    def __init__(self, settings: config.Settings, auto: bool = False, attendance: bool = False,
                 camera_factory=Camera, detector_factory=FaceDetector):
        self.settings = settings
        self.auto, self.attendance_boot = auto, attendance
        self.events: queue.Queue = queue.Queue()
        self._voice_test_pending = False
        self._close_at = None
        self._auto_start_at = None
        self._last_drawn = None
        self._photo = None
        self._closing = False
        self._last_refresh = 0.0
        self._page = "camera"
        self._admin_until = None

        self.db = Database()
        owner = self.db.ensure_owner()
        FaceRegistration.migrate_legacy_samples(owner)
        self.auth = AdminAuth(self.db)
        try:
            backup.auto_backup(self.db, settings.auto_backup_keep)
        except Exception:
            log.exception("auto backup failed")
        self.voice = VoiceAssistant(settings.voice_rate, settings.voice_volume, settings.voice_name,
                                    on_result=lambda ok, msg: self.events.put(("voice", ok, msg)))
        self.controller = Controller(settings, self.voice, self.events, self.db, camera_factory, detector_factory)

        self.root = tk.Tk()
        self.root.title(f"{config.APP_NAME}  v{config.VERSION}")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.exit_app)
        self._style()
        self._build()
        self.show_page("camera")
        self._refresh_buttons()
        self._show_placeholder()
        self._update_lock_button()
        self.root.after(50, self._tick)

        if self.auto:
            self._set_status("Initializing Assistant", "info", f"Starting in {settings.startup_delay_seconds} s...")
            self._auto_start_at = time.monotonic() + settings.startup_delay_seconds
        elif self.attendance_boot:
            self._set_status("Initializing Assistant", "info", "Starting attendance mode...")
            self._auto_start_at = time.monotonic() + 1
        else:
            hint = "" if self.auth.has_pin() else "  Tip: set an Admin PIN on the Admin page."
            self._set_status("Ready", "ok", "Start Attendance to log people, or Welcome to just greet." + hint)

    # ================================================================== styling / layout
    def _style(self) -> None:
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure("Treeview", background=PANEL2, fieldbackground=PANEL2, foreground=TEXT, rowheight=25,
                     borderwidth=0, font=(FONT, 10), bordercolor=BORDER, lightcolor=PANEL2, darkcolor=PANEL2)
        st.configure("Treeview.Heading", background="#12233a", foreground=ACCENT, relief="flat", font=(FONT, 10, "bold"))
        st.map("Treeview", background=[("selected", BORDER)], foreground=[("selected", TEXT)])
        st.map("Treeview.Heading", background=[("active", "#17314f")])
        st.configure("Vertical.TScrollbar", background=PANEL2, troughcolor=PANEL, bordercolor=PANEL, arrowcolor=ACCENT)
        st.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2, foreground=TEXT, arrowcolor=ACCENT,
                     bordercolor=BORDER, lightcolor=PANEL2, darkcolor=PANEL2)
        st.map("TCombobox", fieldbackground=[("readonly", PANEL2)], foreground=[("readonly", TEXT)],
               selectbackground=[("readonly", PANEL2)], selectforeground=[("readonly", TEXT)])
        for k, v in (("background", PANEL2), ("foreground", TEXT), ("selectBackground", BORDER), ("selectForeground", TEXT)):
            self.root.option_add(f"*TCombobox*Listbox.{k}", v)

    def _build(self) -> None:
        r = self.root
        side = tk.Frame(r, bg=PANEL, width=200, highlightthickness=1, highlightbackground=BORDER)
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)
        tk.Label(side, text="◆ FaceDesk", bg=PANEL, fg=ACCENT, font=(FONT, 18, "bold")).pack(anchor="w", padx=18, pady=(18, 0))
        tk.Label(side, text=f"Smart Welcome and\nAttendance System  v{config.VERSION}", bg=PANEL, fg=MUTED,
                 font=(FONT, 9), justify="left").pack(anchor="w", padx=18, pady=(0, 16))
        self.nav = {}
        for key, label in (("camera", "📷  Camera"), ("dashboard", "📊  Dashboard"), ("people", "👥  People"),
                           ("attendance", "🗒  Attendance Log"), ("accuracy", "🧪  Accuracy Test"),
                           ("admin", "🛡  Admin")):
            b = tk.Button(side, text=label, anchor="w", relief="flat", bd=0, bg=PANEL, fg=TEXT, activebackground=BORDER,
                          activeforeground=TEXT, font=(FONT, 11), padx=18, pady=9, cursor="hand2",
                          highlightthickness=0, command=lambda k=key: self.show_page(k))
            b.pack(fill="x")
            self.nav[key] = b
        tk.Frame(side, bg=BORDER, height=1).pack(fill="x", padx=14, pady=8)
        self.btn_lock = tk.Button(side, text="", anchor="w", relief="flat", bd=0, bg=PANEL, fg=TEXT,
                                  activebackground=BORDER, activeforeground=TEXT, font=(FONT, 10), padx=18, pady=8,
                                  cursor="hand2", highlightthickness=0, command=self.toggle_lock)
        self.btn_lock.pack(fill="x")
        for label, cmd in (("⚙  Settings", self.open_settings), ("⏻  Exit", self.exit_app)):
            tk.Button(side, text=label, anchor="w", relief="flat", bd=0, bg=PANEL, fg=TEXT, activebackground=BORDER,
                      activeforeground=TEXT, font=(FONT, 11), padx=18, pady=9, cursor="hand2", highlightthickness=0,
                      command=cmd).pack(fill="x")
        tk.Label(side, text="LOCAL • OFFLINE • PRIVATE\nNot secure authentication.", bg=PANEL, fg="#3f526b",
                 font=(FONT, 8), justify="left").pack(side="bottom", anchor="w", padx=18, pady=14)

        self.content = tk.Frame(r, bg=BG)
        self.content.grid(row=0, column=1, sticky="nsew", padx=18, pady=14)
        self.pages = {k: tk.Frame(self.content, bg=BG) for k in self.nav}
        for f in self.pages.values():
            f.grid(row=0, column=0, sticky="nsew")
        self.v_sec_dash, self.v_sec_people, self.v_sec_att = (tk.StringVar(value=ALL) for _ in range(3))
        self.sec_boxes = []
        self._build_camera(self.pages["camera"])
        self._build_dashboard(self.pages["dashboard"])
        self._build_people(self.pages["people"])
        self._build_attendance(self.pages["attendance"])
        self._build_accuracy(self.pages["accuracy"])
        self._build_admin(self.pages["admin"])

    def _heading(self, parent, text, sub=""):
        tk.Label(parent, text=text, bg=BG, fg=TEXT, font=(FONT, 16, "bold")).pack(anchor="w")
        if sub:
            tk.Label(parent, text=sub, bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(0, 6))
        tk.Frame(parent, bg=ACCENT, height=2).pack(fill="x", pady=(2, 8))

    def _section_box(self, parent, var, command):
        tk.Label(parent, text="Section", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(0, 4))
        cb = ttk.Combobox(parent, textvariable=var, values=[ALL], state="readonly", width=16, font=(FONT, 10))
        cb.pack(side="left", padx=(0, 10), ipady=2)
        cb.bind("<<ComboboxSelected>>", lambda e: command())
        self.sec_boxes.append((cb, var))
        return cb

    def _sections(self):
        return [ALL] + self.db.list_sections()

    def _refresh_sections(self) -> None:
        values = self._sections()
        for cb, var in self.sec_boxes:
            cb.config(values=values)
            if var.get() not in values:
                var.set(ALL)

    @staticmethod
    def _sec(var) -> str:
        return "" if var.get() == ALL else var.get()

    # ================================================================== camera page
    def _build_camera(self, p) -> None:
        self._heading(p, "Camera", "Attendance logs people continuously. Welcome greets one person and stops.")
        row = tk.Frame(p, bg=BG)
        row.pack(fill="both")
        left = tk.Frame(row, bg=BG)
        left.pack(side="left", anchor="n")
        self.canvas = tk.Canvas(left, width=VIEW_W, height=VIEW_H, bg="#02040a", highlightthickness=2,
                                highlightbackground=ACCENT, bd=0)
        self.canvas.pack()
        self.progress = tk.Canvas(left, width=VIEW_W, height=8, bg=PANEL, highlightthickness=0)
        self.progress.pack(pady=(8, 0))
        self._bar = self.progress.create_rectangle(0, 0, 0, 8, fill=ACCENT, width=0)

        right = tk.Frame(row, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, width=300)
        right.pack(side="left", fill="y", padx=(14, 0))
        right.pack_propagate(False)
        tk.Label(right, text="STATUS", bg=PANEL, fg=MUTED, font=(FONT, 8, "bold")).pack(anchor="w", padx=14, pady=(12, 0))
        self.status_label = tk.Label(right, text="", bg=PANEL, fg=LEVEL_COLORS["info"], font=(FONT, 13, "bold"),
                                     wraplength=270, justify="left")
        self.status_label.pack(anchor="w", padx=14)
        self.detail_label = tk.Label(right, text="", bg=PANEL, fg=MUTED, font=(FONT, 9), wraplength=270,
                                     justify="left", height=3, anchor="nw")
        self.detail_label.pack(anchor="w", fill="x", padx=14)
        self.score_label = tk.Label(right, text="", bg=PANEL, fg=MUTED, font=(FONT, 8))
        self.score_label.pack(anchor="w", padx=14)
        cls = tk.Frame(right, bg=PANEL)
        cls.pack(fill="x", padx=14, pady=(2, 0))
        tk.Label(cls, text="Class", bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(0, 6))
        self.v_class = tk.StringVar(value=ALL)
        self.class_box = ttk.Combobox(cls, textvariable=self.v_class, values=[ALL], state="readonly", width=18, font=(FONT, 10))
        self.class_box.pack(side="left")
        self.sec_boxes.append((self.class_box, self.v_class))
        bt = tk.Frame(right, bg=PANEL)
        bt.pack(fill="x", padx=14, pady=(6, 4))
        self.btn_att = GlowButton(bt, "Start Attendance", self.start_attendance, accent=True)
        self.btn_welcome = GlowButton(bt, "Start Welcome (greet once)", self.start_welcome)
        self.btn_stop = GlowButton(bt, "Stop", self.stop)
        self.btn_voice = GlowButton(bt, "Test Voice", self.test_voice)
        self.btn_check = GlowButton(bt, "Self-check", self.run_selfcheck)
        for b in (self.btn_att, self.btn_welcome, self.btn_stop, self.btn_voice, self.btn_check):
            b.pack(fill="x", pady=2)
        tk.Label(right, text="RECENT SCANS", bg=PANEL, fg=MUTED, font=(FONT, 8, "bold")).pack(anchor="w", padx=14, pady=(6, 2))
        f, self.recent = make_tree(right, [("t", "Time"), ("n", "Name"), ("e", "Event")], (80, 110, 62), height=4)
        f.pack(fill="x", padx=14, pady=(0, 8))

    # ================================================================== dashboard page
    def _build_dashboard(self, p) -> None:
        self._heading(p, "Dashboard", "Today's attendance at a glance")
        top = tk.Frame(p, bg=BG)
        top.pack(fill="x", pady=(0, 6))
        self._section_box(top, self.v_sec_dash, self.refresh_dashboard)
        cards = tk.Frame(p, bg=BG)
        cards.pack(fill="x")
        self.cards = {}
        for i, (key, label, color) in enumerate((("registered", "REGISTERED", ACCENT), ("present", "PRESENT", "#3ddc97"),
                                                 ("late", "LATE", "#ffb84d"), ("absent", "ABSENT", "#ff5c6c"))):
            c = tk.Frame(cards, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
            c.grid(row=0, column=i, padx=(0 if i == 0 else 10, 0), sticky="ew")
            cards.grid_columnconfigure(i, weight=1)
            tk.Label(c, text=label, bg=PANEL, fg=MUTED, font=(FONT, 9, "bold")).pack(anchor="w", padx=14, pady=(8, 0))
            v = tk.Label(c, text="0", bg=PANEL, fg=color, font=(FONT, 26, "bold"))
            v.pack(anchor="w", padx=14, pady=(0, 6))
            self.cards[key] = v
        lower = tk.Frame(p, bg=BG)
        lower.pack(fill="both", expand=True, pady=(10, 0))
        left = tk.Frame(lower, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        self.dash_date = tk.Label(left, text="", bg=BG, fg=TEXT, font=(FONT, 11, "bold"))
        self.dash_date.pack(anchor="w", pady=(0, 4))
        f, self.today_tree = make_tree(left, [("name", "Name"), ("sec", "Section"), ("in", "Time In"),
                                              ("out", "Time Out"), ("st", "Status")], (190, 110, 80, 80, 70), height=11)
        f.pack(fill="both", expand=True)
        right = tk.Frame(lower, bg=BG)
        right.pack(side="left", padx=(14, 0), anchor="n")
        tk.Label(right, text="Present - last 7 days", bg=BG, fg=TEXT, font=(FONT, 11, "bold")).pack(anchor="w", pady=(0, 4))
        self.chart = tk.Canvas(right, width=300, height=235, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        self.chart.pack()

    def _draw_chart(self, data) -> None:
        c = self.chart
        c.delete("all")
        W, H, pad_b, pad_t = 300, 235, 28, 24
        top = max([n for _, n in data] + [1])
        bw = (W - 20) / len(data)
        for i, (d, n) in enumerate(data):
            x0, x1 = 10 + i * bw + 6, 10 + (i + 1) * bw - 6
            h = (H - pad_b - pad_t) * n / top
            y1 = H - pad_b
            c.create_rectangle(x0, y1 - h, x1, y1, fill=ACCENT if d != date.today() else "#3ddc97", width=0)
            c.create_text((x0 + x1) / 2, y1 - h - 10, text=str(n), fill=TEXT, font=(FONT, 9, "bold"))
            c.create_text((x0 + x1) / 2, y1 + 12, text=d.strftime("%a"), fill=MUTED, font=(FONT, 8))

    def refresh_dashboard(self) -> None:
        today, sec = date.today(), self._sec(self.v_sec_dash)
        registered = {p["id"] for p in self.db.list_people(sec) if FaceRegistration.sample_count(p["id"]) > 0}
        for k, v in self.db.stats(today, registered, sec).items():
            self.cards[k].config(text=str(v))
        self.dash_date.config(text=today.strftime("%A, %B %d, %Y"))
        self.today_tree.delete(*self.today_tree.get_children())
        for r in self.db.attendance_for_day(today, sec):
            self.today_tree.insert("", "end", tags=("late" if r["late"] else "ok",),
                                   values=(r["name"], r["section"], _t12(r["time_in"]), _t12(r["time_out"]),
                                           "Late" if r["late"] else "On time"))
        self._draw_chart(self.db.counts_per_day(7, today, sec))

    # ================================================================== people page
    def _build_people(self, p) -> None:
        self._heading(p, "People", "Add people (or import a CSV), then register each person's face (~20 seconds)")
        r1 = tk.Frame(p, bg=BG); r1.pack(fill="x", pady=(0, 6))
        self._section_box(r1, self.v_sec_people, self.refresh_people)
        for label, cmd, acc in (("+ Add Person", self.add_person, True), ("Edit", self.edit_person, False),
                                ("Delete Person", self.delete_person, False), ("Import CSV", self.import_csv, False),
                                ("CSV Template", self.save_template, False)):
            GlowButton(r1, label, cmd, accent=acc).pack(side="left", padx=(0, 6))
        r2 = tk.Frame(p, bg=BG); r2.pack(fill="x", pady=(0, 6))
        for label, cmd in (("Register / Re-register Face", self.register_selected),
                           ("Register next unregistered", self.register_next),
                           ("Consent...", self.consent_selected), ("Withdraw consent", self.withdraw_consent),
                           ("Delete ALL Face Data", self.delete_all_faces)):
            GlowButton(r2, label, cmd).pack(side="left", padx=(0, 6))
        f, self.people_tree = make_tree(p, [("id", "ID"), ("name", "Name"), ("sid", "Student ID"), ("sec", "Section"),
                                            ("consent", "Consent"), ("n", "Samples"), ("face", "Face")],
                                        (36, 180, 92, 100, 90, 60, 150), height=13)
        f.pack(fill="both", expand=True)

    def refresh_people(self) -> None:
        sel = self.people_tree.selection()
        keep = sel[0] if sel else None
        self.people_tree.delete(*self.people_tree.get_children())
        stale = set(retention.stale_people(self.db, self.settings.reregister_after_days))
        for pr in self.db.list_people(self._sec(self.v_sec_people)):
            n = FaceRegistration.sample_count(pr["id"])
            face = "○ Not registered" if not n else "⚠ Re-register (old)" if pr["id"] in stale else "● Registered"
            consent = "n/a (owner)" if pr["is_owner"] else CONSENT_TEXT.get(pr["consent"], "—")
            self.people_tree.insert("", "end", iid=str(pr["id"]), tags=("ok" if n and pr["id"] not in stale else "late",),
                                    values=(pr["id"], pr["name"], pr["student_id"], pr["section"], consent, n, face))
        if keep and self.people_tree.exists(keep):
            self.people_tree.selection_set(keep)

    def _selected_person(self):
        sel = self.people_tree.selection()
        if not sel:
            messagebox.showinfo("Select a person", "Click a person in the list first.", parent=self.root)
            return None
        return self.db.get_person(int(sel[0]))

    # ================================================================== attendance page
    def _build_attendance(self, p) -> None:
        self._heading(p, "Attendance Log", "Search, filter, correct and export attendance records")
        r1 = tk.Frame(p, bg=BG); r1.pack(fill="x", pady=(0, 6))
        today = date.today()
        self.v_from = tk.StringVar(value=(today - timedelta(days=30)).isoformat())
        self.v_to = tk.StringVar(value=today.isoformat())
        self.v_search = tk.StringVar()
        for label, var, w in (("From", self.v_from, 11), ("To", self.v_to, 11)):
            tk.Label(r1, text=label, bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(0, 4))
            e = make_entry(r1, var, w)
            e.pack(side="left", padx=(0, 8), ipady=3)
            e.bind("<Return>", lambda ev: self.refresh_attendance())
        self._section_box(r1, self.v_sec_att, self.refresh_attendance)
        tk.Label(r1, text="Search", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(0, 4))
        e = make_entry(r1, self.v_search, 14); e.pack(side="left", padx=(0, 8), ipady=3)
        e.bind("<Return>", lambda ev: self.refresh_attendance())
        GlowButton(r1, "Apply", self.refresh_attendance, accent=True).pack(side="left")
        r2 = tk.Frame(p, bg=BG); r2.pack(fill="x", pady=(0, 4))
        for label, cmd in (("Manual Entry", self.manual_entry), ("Export Excel", lambda: self.export("xlsx")),
                           ("Export CSV", lambda: self.export("csv")),
                           ("Summary → Excel", lambda: self.export_summary("xlsx")),
                           ("Summary → CSV", lambda: self.export_summary("csv"))):
            GlowButton(r2, label, cmd).pack(side="left", padx=(0, 6))
        self.att_count = tk.Label(p, text="", bg=BG, fg=MUTED, font=(FONT, 9))
        self.att_count.pack(anchor="w")
        f, self.att_tree = make_tree(p, [("d", "Date"), ("n", "Name"), ("i", "Student ID"), ("s", "Section"),
                                         ("a", "Time In"), ("o", "Time Out"), ("h", "Hrs"), ("st", "Status"),
                                         ("src", "Source"), ("note", "Note")],
                                     (90, 140, 92, 92, 78, 82, 48, 70, 70, 100), height=12)
        f.pack(fill="both", expand=True)

    @staticmethod
    def _parse_date(text: str):
        try:
            return datetime.strptime(text.strip(), "%Y-%m-%d").date()
        except ValueError:
            return None

    def _range(self):
        d1, d2 = self._parse_date(self.v_from.get()), self._parse_date(self.v_to.get())
        if d1 is None or d2 is None:
            messagebox.showerror("Invalid date", "Use the format YYYY-MM-DD, for example 2026-10-12.", parent=self.root)
            return None
        return (d1, d2) if d1 <= d2 else (d2, d1)

    def _attendance_records(self):
        rng = self._range()
        if rng is None:
            return None
        return self.db.attendance_range(rng[0], rng[1], self.v_search.get(), self._sec(self.v_sec_att))

    def refresh_attendance(self) -> None:
        recs = self._attendance_records()
        if recs is None:
            return
        self.att_tree.delete(*self.att_tree.get_children())
        for row, rec in zip(exporter.to_rows(recs), recs):
            # row = Date, Name, StudentID, Section, Role, In, Out, Hours, Status, Source, Note  (Role is hidden here)
            self.att_tree.insert("", "end", tags=("late" if rec["late"] else "ok",), values=row[:4] + row[5:])
        self.att_count.config(text=f"{len(recs)} record(s)")

    def _save_dialog(self, kind: str, name: str) -> str:
        config.EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        return filedialog.asksaveasfilename(parent=self.root, initialdir=str(config.EXPORT_DIR), initialfile=name,
                                            defaultextension=f".{kind}",
                                            filetypes=[("Excel workbook" if kind == "xlsx" else "CSV file", f"*.{kind}")])

    def export(self, kind: str) -> None:
        if not self.require_admin():
            return
        recs = self._attendance_records()
        if recs is None:
            return
        if not recs:
            messagebox.showinfo("Nothing to export", "No attendance records match the filter.", parent=self.root)
            return
        path = self._save_dialog(kind, f"attendance_{self.v_from.get()}_to_{self.v_to.get()}.{kind}")
        if not path:
            return
        self._write_export(lambda: (exporter.export_xlsx if kind == "xlsx" else exporter.export_csv)(recs, path),
                           path, len(recs), "attendance")

    def export_summary(self, kind: str) -> None:
        if not self.require_admin():
            return
        rng = self._range()
        if rng is None:
            return
        data = self.db.summary(rng[0], rng[1], self._sec(self.v_sec_att), config.holiday_set(self.settings), date.today())
        if not data:
            messagebox.showinfo("Nothing to export", "No people match the section filter.", parent=self.root)
            return
        path = self._save_dialog(kind, f"summary_{rng[0]}_to_{rng[1]}.{kind}")
        if path:
            self._write_export(lambda: (exporter.export_summary_xlsx if kind == "xlsx" else exporter.export_summary_csv)(data, path),
                               path, len(data), "summary")

    def _write_export(self, fn, path, n, what) -> None:
        try:
            fn()
        except (OSError, RuntimeError) as exc:
            messagebox.showerror("Export failed", f"{exc}\n\nIf the file is open in Excel, close it and try again.", parent=self.root)
            return
        self.db.log_audit("export", f"{what}: {n} row(s) -> {Path(path).name}")
        messagebox.showinfo("Exported", f"Saved {n} row(s) to:\n{path}", parent=self.root)

    def manual_entry(self) -> None:
        if not self.require_admin():
            return
        people = self.db.list_people()
        labels = {f"{p['name']}" + (f"  ({p['section']})" if p["section"] else ""): p for p in people}
        win = self._dialog("Manual Attendance Entry")
        vp, vd = tk.StringVar(), tk.StringVar(value=date.today().isoformat())
        vi, vo, vr = tk.StringVar(value="08:00"), tk.StringVar(), tk.StringVar()
        ttk.Combobox(win, textvariable=vp, values=list(labels), state="readonly", width=34).grid(row=0, column=1, padx=14, pady=(14, 4))
        tk.Label(win, text="Person", bg=PANEL, fg=TEXT).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 4))
        for i, (lbl, var) in enumerate((("Date (YYYY-MM-DD)", vd), ("Time In (HH:MM)", vi),
                                        ("Time Out (HH:MM, optional)", vo), ("Reason (required)", vr)), start=1):
            tk.Label(win, text=lbl, bg=PANEL, fg=TEXT).grid(row=i, column=0, sticky="w", padx=14, pady=4)
            make_entry(win, var, 36).grid(row=i, column=1, padx=14, pady=4, ipady=3)
        result = {}

        def save():
            try:
                self.apply_manual(labels[vp.get()], vd.get(), vi.get(), vo.get(), vr.get())
            except KeyError:
                messagebox.showerror("Manual entry", "Choose a person.", parent=win); return
            except ValueError as exc:
                messagebox.showerror("Manual entry", f"{exc}\n\nCheck the date/time formats and the reason.", parent=win); return
            result["ok"] = True
            win.destroy()

        bar = tk.Frame(win, bg=PANEL); bar.grid(row=6, column=0, columnspan=2, pady=12)
        GlowButton(bar, "Save", save, accent=True, width=10).pack(side="left", padx=6)
        GlowButton(bar, "Cancel", win.destroy, width=10).pack(side="left", padx=6)
        self.root.wait_window(win)
        if result:
            self.refresh_all()

    def apply_manual(self, person: dict, day_s: str, tin_s: str, tout_s: str, reason: str) -> str:
        """Validate + save a manual attendance record and write the audit entry. Raises ValueError."""
        day = datetime.strptime(day_s.strip(), "%Y-%m-%d").date()
        t_in = datetime.combine(day, datetime.strptime(tin_s.strip(), "%H:%M").time())
        t_out = datetime.combine(day, datetime.strptime(tout_s.strip(), "%H:%M").time()) if tout_s.strip() else None
        res = self.db.set_manual_attendance(person["id"], t_in, t_out, self.settings.late_after, reason)
        self.db.log_audit("manual_attendance", f"{person['name']} {day} {res}: {reason.strip()}")
        return res

    # ================================================================== admin page
    def _build_admin(self, p) -> None:
        self._heading(p, "Admin", "PIN, backup & restore, audit log, health check")
        top = tk.Frame(p, bg=BG); top.pack(fill="x")
        c1 = tk.Frame(top, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        c1.pack(side="left", fill="both", expand=True, padx=(0, 8))
        tk.Label(c1, text="ADMIN PIN", bg=PANEL, fg=MUTED, font=(FONT, 8, "bold")).pack(anchor="w", padx=12, pady=(8, 0))
        self.pin_label = tk.Label(c1, text="", bg=PANEL, fg=TEXT, font=(FONT, 10, "bold"))
        self.pin_label.pack(anchor="w", padx=12)
        b = tk.Frame(c1, bg=PANEL); b.pack(anchor="w", padx=12, pady=8)
        for label, cmd in (("Set / Change PIN", self.set_pin), ("Remove PIN", self.remove_pin), ("Lock now", self.lock_now)):
            GlowButton(b, label, cmd).pack(side="left", padx=(0, 6))
        c2 = tk.Frame(top, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        c2.pack(side="left", fill="both", expand=True)
        tk.Label(c2, text="BACKUP & RESTORE", bg=PANEL, fg=MUTED, font=(FONT, 8, "bold")).pack(anchor="w", padx=12, pady=(8, 0))
        self.v_faces = tk.BooleanVar(value=False)
        tk.Checkbutton(c2, text="Include face data (biometric - keep the file safe)", variable=self.v_faces, bg=PANEL,
                       fg=TEXT, selectcolor=PANEL2, activebackground=PANEL, activeforeground=TEXT,
                       font=(FONT, 9)).pack(anchor="w", padx=12)
        b = tk.Frame(c2, bg=PANEL); b.pack(anchor="w", padx=12, pady=8)
        GlowButton(b, "Backup now", self.backup_now, accent=True).pack(side="left", padx=(0, 6))
        GlowButton(b, "Restore backup...", self.restore_now).pack(side="left", padx=(0, 6))
        tk.Label(p, text=f"Automatic daily backups (database only) are kept in: {config.BACKUP_DIR}", bg=BG, fg=MUTED,
                 font=(FONT, 8)).pack(anchor="w", pady=(6, 0))
        bar = tk.Frame(p, bg=BG); bar.pack(fill="x", pady=(8, 4))
        tk.Label(bar, text="Audit log", bg=BG, fg=TEXT, font=(FONT, 11, "bold")).pack(side="left", padx=(0, 12))
        self.v_audit = tk.StringVar()
        e = make_entry(bar, self.v_audit, 16); e.pack(side="left", padx=(0, 8), ipady=3)
        e.bind("<Return>", lambda ev: self.refresh_audit())
        GlowButton(bar, "Search", self.refresh_audit).pack(side="left", padx=(0, 6))
        GlowButton(bar, "Export CSV", self.export_audit).pack(side="left", padx=(0, 6))
        GlowButton(bar, "Run Self-check", self.run_selfcheck).pack(side="left", padx=(0, 6))
        GlowButton(bar, "End School Year...", self.end_school_year).pack(side="left")
        f, self.audit_tree = make_tree(p, [("t", "Time"), ("w", "Who"), ("a", "Action"), ("d", "Detail")],
                                       (150, 80, 150, 450), height=11)
        f.pack(fill="both", expand=True)

    def refresh_admin(self) -> None:
        self.pin_label.config(text="PIN is set" if self.auth.has_pin() else "No PIN set - anyone can open admin screens",
                              fg=LEVEL_COLORS["ok"] if self.auth.has_pin() else LEVEL_COLORS["warn"])
        self.refresh_audit()

    def refresh_audit(self) -> None:
        self.audit_tree.delete(*self.audit_tree.get_children())
        for e in self.db.list_audit(300, self.v_audit.get()):
            self.audit_tree.insert("", "end", values=(e["ts"], e["actor"], e["action"], e["detail"]))

    def export_audit(self) -> None:
        if not self.require_admin():
            return
        path = self._save_dialog("csv", f"audit_log_{date.today()}.csv")
        if path:
            self._write_export(lambda: exporter.export_audit_csv(self.db.list_audit(100000), path), path,
                               len(self.db.list_audit(100000)), "audit log")

    # -- PIN
    def _pin_dialog(self, title, labels):
        win = self._dialog(title)
        vars_ = [tk.StringVar() for _ in labels]
        for i, (lbl, v) in enumerate(zip(labels, vars_)):
            tk.Label(win, text=lbl, bg=PANEL, fg=TEXT, font=(FONT, 10)).grid(row=i, column=0, sticky="w", padx=14, pady=(14 if i == 0 else 4, 0))
            e = make_entry(win, v, 20, show="•")
            e.grid(row=i, column=1, padx=14, pady=(14 if i == 0 else 4, 0), ipady=3)
            if i == 0:
                e.focus_set()
        out = {}
        def ok():
            out["v"] = [v.get() for v in vars_]; win.destroy()
        bar = tk.Frame(win, bg=PANEL); bar.grid(row=len(labels), column=0, columnspan=2, pady=12)
        GlowButton(bar, "OK", ok, accent=True, width=10).pack(side="left", padx=6)
        GlowButton(bar, "Cancel", win.destroy, width=10).pack(side="left", padx=6)
        win.bind("<Return>", lambda e: ok())
        self.root.wait_window(win)
        return out.get("v")

    def _dialog(self, title):
        win = tk.Toplevel(self.root)
        win.title(title); win.configure(bg=PANEL); win.transient(self.root); win.grab_set(); win.resizable(False, False)
        return win

    def require_admin(self) -> bool:
        """True if no PIN is set, or the admin session is unlocked (and extend it). Otherwise ask for the PIN."""
        if not self.auth.has_pin():
            return True
        now = time.monotonic()
        if self._admin_until is not None and now < self._admin_until:
            self._admin_until = now + self.settings.admin_session_minutes * 60
            return True
        v = self._pin_dialog("Admin PIN", ["Enter admin PIN"])
        if not v:
            return False
        ok, msg = self.auth.verify(v[0])
        if ok:
            self._admin_until = time.monotonic() + self.settings.admin_session_minutes * 60
            self._update_lock_button()
            return True
        messagebox.showerror("Admin PIN", msg, parent=self.root)
        return False

    def _update_lock_button(self) -> None:
        if not self.auth.has_pin():
            self.btn_lock.config(text="🔓  No PIN set", fg=LEVEL_COLORS["warn"])
        elif self._admin_until is not None and time.monotonic() < self._admin_until:
            self.btn_lock.config(text="🔓  Admin unlocked (lock)", fg=LEVEL_COLORS["ok"])
        else:
            self.btn_lock.config(text="🔒  Locked (unlock)", fg=TEXT)

    def toggle_lock(self) -> None:
        if self.auth.has_pin() and self._admin_until is not None and time.monotonic() < self._admin_until:
            self.lock_now()
        elif self.auth.has_pin():
            self.require_admin()
        else:
            self.show_page("admin")

    def lock_now(self) -> None:
        self._admin_until = None
        self._update_lock_button()
        if self._page in PROTECTED and self.auth.has_pin():
            self.show_page("camera")

    def set_pin(self) -> None:
        if self.auth.has_pin():
            if not self.require_admin():
                return
            v = self._pin_dialog("Change PIN", ["Current PIN", "New PIN", "Repeat new PIN"])
            if not v:
                return
            if v[1] != v[2]:
                messagebox.showerror("PIN", "The new PINs do not match.", parent=self.root); return
            ok, msg = self.auth.change_pin(v[0], v[1])
        else:
            v = self._pin_dialog("Set Admin PIN", ["New PIN (4+ characters)", "Repeat PIN"])
            if not v:
                return
            if v[0] != v[1]:
                messagebox.showerror("PIN", "The PINs do not match.", parent=self.root); return
            try:
                self.auth.set_pin(v[0]); self.db.log_audit("pin_set"); ok, msg = True, ""
            except ValueError as exc:
                ok, msg = False, str(exc)
        if not ok:
            messagebox.showerror("PIN", msg, parent=self.root); return
        self._admin_until = time.monotonic() + self.settings.admin_session_minutes * 60
        self._update_lock_button(); self.refresh_admin()
        messagebox.showinfo("PIN", "Admin PIN saved.\n\nWrite it down somewhere safe. If it is lost, an administrator "
                            "with access to this PC must reset it (see README).", parent=self.root)

    def remove_pin(self) -> None:
        if not self.auth.has_pin():
            return
        v = self._pin_dialog("Remove PIN", ["Current PIN"])
        if not v:
            return
        ok, msg = self.auth.remove_pin(v[0])
        if not ok:
            messagebox.showerror("PIN", msg, parent=self.root); return
        self._admin_until = None; self._update_lock_button(); self.refresh_admin()

    # -- backup
    def backup_now(self) -> None:
        if not self.require_admin():
            return
        if self.v_faces.get() and not messagebox.askyesno(
                "Include face data?", "The backup will contain face samples (biometric data).\n"
                "Store it somewhere secure and do not share it. Continue?", icon="warning", parent=self.root):
            return
        config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        path = filedialog.asksaveasfilename(parent=self.root, initialdir=str(config.BACKUP_DIR),
                                            initialfile=f"facedesk_backup_{datetime.now():%Y%m%d_%H%M}.zip",
                                            defaultextension=".zip", filetypes=[("FaceDesk backup", "*.zip")])
        if not path:
            return
        try:
            backup.create_backup(self.db, path, self.v_faces.get())
        except (OSError, backup.BackupError) as exc:
            messagebox.showerror("Backup failed", str(exc), parent=self.root); return
        self.db.log_audit("backup", f"{Path(path).name} faces={self.v_faces.get()}")
        messagebox.showinfo("Backup", f"Backup saved:\n{path}", parent=self.root)

    def restore_now(self) -> None:
        if not self.require_admin() or not self._idle_or_warn():
            return
        path = filedialog.askopenfilename(parent=self.root, initialdir=str(config.BACKUP_DIR),
                                          filetypes=[("FaceDesk backup", "*.zip")])
        if not path:
            return
        if not messagebox.askyesno("Restore backup", "This REPLACES the current people and attendance records "
                                   "with the backup.\nA safety copy of the current data is saved first.\n\nContinue?",
                                   icon="warning", parent=self.root):
            return
        try:
            info = backup.restore_backup(self.db, path)
        except (backup.BackupError, OSError) as exc:
            messagebox.showerror("Restore failed", f"{exc}\n\nNothing was changed.", parent=self.root); return
        self.db.log_audit("restore", f"{Path(path).name} faces={info['faces_restored']}")
        self._admin_until = None
        self._update_lock_button(); self._refresh_sections(); self.refresh_all()
        messagebox.showinfo("Restore complete", "Backup restored.\nA safety copy of the previous data is in:\n"
                            f"{info['safety_backup']}\n\nThe PIN from the backup (if any) now applies.", parent=self.root)

    # -- self-check
    def run_selfcheck(self) -> None:
        win = self._dialog("Self-check")
        txt = tk.Text(win, width=88, height=22, bg=PANEL2, fg=TEXT, relief="flat", font=("Consolas", 10), wrap="word")
        txt.pack(padx=14, pady=(14, 6))
        for tag, color in (("ok", LEVEL_COLORS["ok"]), ("warn", LEVEL_COLORS["warn"]), ("fail", LEVEL_COLORS["error"]),
                           ("fix", MUTED)):
            txt.tag_configure(tag, foreground=color)
        bar = tk.Frame(win, bg=PANEL); bar.pack(pady=(0, 12))
        state = {"running": False}

        def start(with_camera):
            if state["running"]:
                return
            if with_camera and self.controller.is_running():
                with_camera = False
                messagebox.showinfo("Camera busy", "Stop the camera first to include the camera test.", parent=win)
            state["running"] = True
            txt.config(state="normal"); txt.delete("1.0", "end"); txt.insert("end", "Checking...\n"); txt.config(state="disabled")
            holder = {}
            threading.Thread(target=lambda: holder.update(r=selfcheck.run_checks(with_camera)), daemon=True).start()
            def poll():
                if "r" not in holder:
                    win.after(150, poll); return
                state["running"] = False
                txt.config(state="normal"); txt.delete("1.0", "end")
                for c in holder["r"]:
                    tag = "fail" if not c.ok else "warn" if c.warn else "ok"
                    mark = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}[tag]
                    txt.insert("end", f"{mark} {c.name}: {c.detail}\n", tag)
                    if c.fix and tag != "ok":
                        txt.insert("end", f"       -> {c.fix}\n", "fix")
                bad = sum(1 for c in holder["r"] if not c.ok)
                txt.insert("end", "\n" + ("All good." if not bad else f"{bad} problem(s) found - see FAIL lines."))
                txt.config(state="disabled")
            poll()

        GlowButton(bar, "Run again", lambda: start(False), accent=True).pack(side="left", padx=6)
        GlowButton(bar, "Run + test camera", lambda: start(True)).pack(side="left", padx=6)
        GlowButton(bar, "Close", win.destroy).pack(side="left", padx=6)
        start(False)

    # ================================================================== navigation / helpers
    def show_page(self, key: str) -> None:
        if key in PROTECTED and not self.require_admin():
            return
        self._page = key
        self.pages[key].tkraise()
        for k, b in self.nav.items():
            b.config(bg=BORDER if k == key else PANEL, fg=ACCENT if k == key else TEXT)
        self._refresh_sections()
        self.refresh_all()

    def refresh_all(self) -> None:
        try:
            {"dashboard": self.refresh_dashboard, "people": self.refresh_people,
             "attendance": self.refresh_attendance, "accuracy": self.refresh_accuracy,
             "admin": self.refresh_admin}.get(self._page, lambda: None)()
        except Exception:
            log.exception("refresh failed")

    def _set_status(self, status: str, level: str = "info", detail: str = "", score=None) -> None:
        self.status_label.config(text=status, fg=LEVEL_COLORS.get(level, TEXT))
        self.detail_label.config(text=detail)
        self.score_label.config(text="" if score is None else
                                f"Match distance {score:.1f}  (accepted if ≤ {self.settings.recognition_threshold:.0f})")

    def _refresh_buttons(self) -> None:
        running = self.controller.is_running()
        for b in (self.btn_att, self.btn_welcome):
            b.config(state="disabled" if running else "normal")
        self.btn_stop.config(state="normal" if running else "disabled")

    def _show_placeholder(self) -> None:
        self.canvas.delete("all")
        self.canvas.create_text(VIEW_W // 2, VIEW_H // 2 - 10, text="CAMERA OFF", fill="#1d3b5e", font=(FONT, 24, "bold"))
        self.canvas.create_text(VIEW_W // 2, VIEW_H // 2 + 26, text="Press Start Attendance", fill="#1d3b5e", font=(FONT, 11))

    def _draw_frame(self, frame, banner) -> None:
        h, w = frame.shape[:2]
        scale = min(VIEW_W / w, VIEW_H / h)
        view = cv2.resize(cv2.flip(frame, 1), (int(w * scale), int(h * scale)))   # mirror: natural preview
        self._photo = None
        try:
            ok, buf = cv2.imencode(".ppm", view)
            if ok:
                self._photo = tk.PhotoImage(data=buf.tobytes())
        except (cv2.error, tk.TclError):
            self._photo = None
        if self._photo is None:
            ok, buf = cv2.imencode(".png", view)
            self._photo = tk.PhotoImage(data=base64.b64encode(buf.tobytes()))
        self.canvas.delete("all")
        self.canvas.create_image(VIEW_W // 2, VIEW_H // 2, image=self._photo)
        if banner:
            color = LEVEL_COLORS.get(banner.get("level", "ok"), ACCENT)
            y = VIEW_H - 112
            self.canvas.create_rectangle(0, y, VIEW_W, VIEW_H, fill="#02121f", outline="")
            self.canvas.create_line(0, y, VIEW_W, y, fill=color, width=2)
            self.canvas.create_text(VIEW_W // 2, y + 38, text=banner["title"], fill=TEXT, font=(FONT, 24, "bold"))
            self.canvas.create_text(VIEW_W // 2, y + 80, text=banner["subtitle"], fill=color, font=(FONT, 16, "bold"))

    # ================================================================== main loop
    def _tick(self) -> None:
        if self._closing:
            return
        try:
            self._drain_events()
            snap = self.controller.snapshot()
            if snap is not self._last_drawn:
                self._last_drawn = snap
                if snap.frame is not None and (self.controller.is_running() or snap.banner is not None
                                               or snap.status == "Recognition Timed Out") and self._page == "camera":
                    self._draw_frame(snap.frame, snap.banner)
                if self._auto_start_at is None:
                    self._set_status(snap.status, snap.level, snap.detail, snap.score)
                self.progress.coords(self._bar, 0, 0, VIEW_W * snap.progress, 8)
            now = time.monotonic()
            if self._auto_start_at is not None:
                left = self._auto_start_at - now
                if left <= 0:
                    self._auto_start_at = None
                    self.start_welcome() if self.auto else self.start_attendance()
                else:
                    self._set_status("Initializing Assistant", "info", f"Starting in {int(left) + 1} s...")
            if self._close_at is not None:
                left = self._close_at - now
                if left <= 0 and (not self.voice.is_busy() or left < -20):
                    self.exit_app(show_desktop=self.settings.show_desktop_after_welcome)
                    return
                elif left > 0:
                    self.detail_label.config(text=f"Closing in {int(left) + 1} s...")
            if self._admin_until is not None and now >= self._admin_until:      # idle timeout -> lock
                self._admin_until = None
                if self._page in PROTECTED:
                    self.show_page("camera")
                    self._set_status("Admin locked", "warn", "Admin session expired. Unlock again to continue.")
                self._update_lock_button()
            if now - self._last_refresh > 4.0:
                self._last_refresh = now
                if self._page in ("dashboard", "people"):
                    self.refresh_all()
        except Exception:
            log.exception("UI tick failed")
        self.root.after(33, self._tick)

    def _quiet(self) -> bool:
        return self.auto or self.attendance_boot

    def _drain_events(self) -> None:
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                return
            kind = ev[0]
            if kind == "stopped":
                self._refresh_buttons()
                if self.controller.snapshot().frame is None:
                    self._show_placeholder()
            elif kind == "welcome":
                if self.auto and self.settings.auto_close_seconds > 0:
                    self._close_at = time.monotonic() + self.settings.auto_close_seconds
            elif kind == "attendance":
                info = ev[1]
                label = {"time_in": "TIME IN", "time_out": "TIME OUT"}.get(info["kind"], info["kind"])
                self.recent.insert("", 0, values=(info["time"].strftime("%I:%M %p").lstrip("0"), info["name"], label))
                for extra in self.recent.get_children()[5:]:
                    self.recent.delete(extra)
                if self._page == "dashboard":
                    self.refresh_all()
            elif kind == "trial":
                t = ev[1]
                self.recent.insert("", 0, values=(f"#{t['trial_no']}", t["truth_label"][:14],
                                                  "OK" if t["correct"] else "WRONG"))
                for extra in self.recent.get_children()[5:]:
                    self.recent.delete(extra)
            elif kind == "trials_done":
                _, session, n = ev
                m = metrics.summarize(self.db.list_trials(session))
                self.db.log_audit("accuracy_test_done", f"session {session}: {n} trial(s)")
                self._show_placeholder()
                if self._page == "accuracy":
                    self.refresh_accuracy()
                messagebox.showinfo("Accuracy test finished", f"{n} trial(s) recorded (session {session}).\n"
                                    f"Correct: {sum(1 for t in self.db.list_trials(session) if t['correct'])}/{n}.\n\n"
                                    "Open the Accuracy Test page to see the totals and export them.", parent=self.root)
            elif kind == "error":
                _, title, msg = ev
                if not self._quiet():
                    messagebox.showerror(title, msg + "\n\nTip: use Self-check, or see README.md > Troubleshooting.", parent=self.root)
            elif kind == "no_registration":
                if not self._quiet() and messagebox.askyesno(
                        "No registered face", "Nobody has a registered face yet.\nOpen the People page to register?", parent=self.root):
                    self.show_page("people")
            elif kind == "registration_done":
                self._show_placeholder()
                p = self.db.get_person(ev[2])
                self.db.log_audit("face_register", f"{p['name'] if p else ev[2]} ({ev[1]} samples)")
                self.refresh_all()
                messagebox.showinfo("Registration complete", f"{p['name'] if p else 'Person'}: saved {ev[1]} face samples.\n\n"
                                    "Click Start Attendance (or Welcome) to test it.", parent=self.root)
            elif kind == "voice":
                _, ok, msg = ev
                if not ok:
                    self.detail_label.config(text=msg)
                    if self._voice_test_pending and not self._quiet():
                        messagebox.showwarning("Voice problem", msg, parent=self.root)
                self._voice_test_pending = False

    # ================================================================== actions
    def _idle_or_warn(self) -> bool:
        if self.controller.is_running():
            messagebox.showinfo("Camera is running", "Click Stop on the Camera page first.", parent=self.root)
            return False
        return True

    def start_attendance(self) -> None:
        if not self.controller.is_running():
            self._close_at = None
            self.controller.start_attendance(self._sec(self.v_class))
            self._refresh_buttons()

    def start_welcome(self) -> None:
        if not self.controller.is_running():
            self._close_at = None
            self.controller.start_welcome()
            self._refresh_buttons()

    def stop(self) -> None:
        self.controller.stop()
        self._refresh_buttons()
        self._show_placeholder()
        self._last_drawn = None
        self._set_status("Stopped", "info", "")
        self.progress.coords(self._bar, 0, 0, 0, 8)

    def test_voice(self) -> None:
        self._voice_test_pending = True
        self.detail_label.config(text="Speaking test greeting...")
        self.voice.speak(config.GREETING_TEXT)

    # -- people
    def _person_dialog(self, title, name="", role="", section="", sid=""):
        win = self._dialog(title)
        vars_ = [tk.StringVar(value=v) for v in (name, sid, section, role)]
        labels = ("Full name", "Student / ID number (optional)", "Section / Grade (optional)", "Role (optional)")
        for i, (lbl, var) in enumerate(zip(labels, vars_)):
            tk.Label(win, text=lbl, bg=PANEL, fg=TEXT, font=(FONT, 10)).grid(row=i, column=0, sticky="w", padx=16, pady=(14 if i == 0 else 5, 0))
            make_entry(win, var, 30).grid(row=i, column=1, padx=16, pady=(14 if i == 0 else 5, 0), ipady=3)
        out = {}
        def ok():
            n, s, sec, r = (v.get() for v in vars_)
            out["v"] = (n, r, sec, s); win.destroy()          # -> (name, role, section, student_id)
        bar = tk.Frame(win, bg=PANEL); bar.grid(row=4, column=0, columnspan=2, pady=14)
        GlowButton(bar, "Save", ok, accent=True, width=10).pack(side="left", padx=6)
        GlowButton(bar, "Cancel", win.destroy, width=10).pack(side="left", padx=6)
        win.bind("<Return>", lambda e: ok())
        self.root.wait_window(win)
        return out.get("v")

    def add_person(self) -> None:
        if not self.require_admin():
            return
        v = self._person_dialog("Add Person", section=self._sec(self.v_sec_people))
        if not v:
            return
        try:
            pid = self.db.add_person(*v)
        except ValueError as exc:
            messagebox.showerror("Cannot add", str(exc), parent=self.root); return
        self.db.log_audit("person_add", v[0])
        self._refresh_sections(); self.refresh_people()
        if messagebox.askyesno("Register face", f"Register {v[0]}'s face now?", parent=self.root):
            self._register(pid)

    def edit_person(self) -> None:
        if not self.require_admin():
            return
        p = self._selected_person()
        if not p:
            return
        v = self._person_dialog("Edit Person", p["name"], p["role"], p["section"], p["student_id"])
        if v:
            try:
                self.db.update_person(p["id"], *v)
            except ValueError as exc:
                messagebox.showerror("Cannot save", str(exc), parent=self.root); return
            self.db.log_audit("person_edit", f"{p['name']} -> {v[0]}")
            self._refresh_sections(); self.refresh_people()

    def register_selected(self) -> None:
        if not self.require_admin():
            return
        p = self._selected_person()
        if p:
            self._register(p["id"])

    def register_next(self) -> None:
        if not self.require_admin():
            return
        for p in self.db.list_people(self._sec(self.v_sec_people)):
            if FaceRegistration.sample_count(p["id"]) == 0:
                self._register(p["id"]); return
        messagebox.showinfo("All registered", "Everyone in this list already has a face registered.", parent=self.root)

    def _register(self, pid: int) -> None:
        if not self._idle_or_warn():
            return
        p, s = self.db.get_person(pid), self.settings
        if s.require_consent and not p["is_owner"] and p["consent"] != "granted":
            if messagebox.askyesno("Consent required", f"No consent is recorded for {p['name']}.\n\nRecord it now?",
                                   parent=self.root):
                self._consent_dialog(p)
            return
        msg = (f"Capture {s.registration_samples} pictures of {p['name']}.\n\n• Even lighting, facing the camera\n"
               "• Follow the prompts (slightly left/right/up/down)\n• Only this person in view\n")
        if FaceRegistration.sample_count(pid):
            msg += "\nThis REPLACES their existing face data."
        if not messagebox.askokcancel("Register Face", msg, parent=self.root):
            return
        self.show_page("camera")
        self.controller.start_registration(pid)
        self._refresh_buttons()

    def delete_person(self) -> None:
        if not self.require_admin():
            return
        p = self._selected_person()
        if not p or not self._idle_or_warn():
            return
        if p["is_owner"]:
            messagebox.showinfo("Owner profile", "The owner profile cannot be deleted.\n"
                                "Use 'Delete ALL Face Data' to remove its face.", parent=self.root); return
        if messagebox.askyesno("Delete Person", f"Delete {p['name']}, their face data AND all their attendance "
                               "records?\nThis cannot be undone.", icon="warning", parent=self.root):
            FaceRegistration.delete_person_faces(p["id"])
            self.db.delete_person(p["id"])
            self.db.log_audit("person_delete", p["name"])
            self._refresh_sections(); self.refresh_people()

    def delete_all_faces(self) -> None:
        if not self.require_admin() or not self._idle_or_warn():
            return
        if messagebox.askyesno("Delete ALL Face Data", "Permanently delete every stored face sample and the "
                               "trained model?\n(People and attendance records are kept.)", icon="warning", parent=self.root):
            FaceRegistration.delete_all_faces()
            self.db.log_audit("faces_delete_all", "all face samples and model deleted")
            self.refresh_people()
            self._set_status("Face Data Deleted", "warn", "All biometric data was removed.")

    def import_csv(self) -> None:
        if not self.require_admin():
            return
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("CSV file", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            res = importer.import_people(self.db, path)
        except ValueError as exc:
            messagebox.showerror("Import failed", str(exc), parent=self.root); return
        self.db.log_audit("csv_import", f"{Path(path).name}: {res.added} added, {len(res.skipped)} skipped")
        self._refresh_sections(); self.refresh_people()
        msg = f"Added {res.added} people.\nSkipped {len(res.skipped)} row(s)."
        if res.skipped:
            msg += "\n\n" + "\n".join(res.skipped[:12]) + ("\n..." if len(res.skipped) > 12 else "")
        msg += "\n\nNext: select a person and click Register Face (or 'Register next unregistered')."
        messagebox.showinfo("Import finished", msg, parent=self.root)

    def save_template(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.root, initialfile="students_template.csv",
                                            defaultextension=".csv", filetypes=[("CSV file", "*.csv")])
        if path:
            importer.write_template(path)
            messagebox.showinfo("Template saved", f"Open it in Excel, fill in your students, save as CSV, "
                                f"then click Import CSV.\n\n{path}", parent=self.root)

    # -- consent / retention
    def apply_consent(self, person: dict, status: str, by: str, ref: str, day_s: str) -> None:
        """Validate and save consent (raises ValueError) and write the audit entry."""
        day = datetime.strptime(day_s.strip(), "%Y-%m-%d").date() if day_s.strip() else date.today()
        self.db.set_consent(person["id"], status, by, ref, day)
        self.db.log_audit("consent_" + status, f"{person['name']} by '{by.strip()}' ref '{ref.strip()}'")

    def _consent_dialog(self, p: dict) -> None:
        win = self._dialog(f"Consent - {p['name']}")
        vs = tk.StringVar(value="Granted")
        vb, vr, vd = tk.StringVar(value=p["consent_by"]), tk.StringVar(value=p["consent_ref"]), tk.StringVar(value=date.today().isoformat())
        tk.Label(win, text="Decision", bg=PANEL, fg=TEXT).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 4))
        ttk.Combobox(win, textvariable=vs, values=["Granted", "Declined"], state="readonly", width=30).grid(row=0, column=1, padx=14, pady=(14, 4))
        for i, (lbl, var) in enumerate((("Parent / guardian name", vb), ("Consent form no. / reference", vr), ("Date (YYYY-MM-DD)", vd)), start=1):
            tk.Label(win, text=lbl, bg=PANEL, fg=TEXT).grid(row=i, column=0, sticky="w", padx=14, pady=4)
            make_entry(win, var, 32).grid(row=i, column=1, padx=14, pady=4, ipady=3)
        tk.Label(win, text="Record only AFTER you hold the signed consent form. Declined = manual attendance, no penalty.",
                 bg=PANEL, fg=MUTED, font=(FONT, 8), wraplength=420, justify="left").grid(row=4, column=0, columnspan=2, padx=14, pady=(2, 0))
        def save():
            try:
                self.apply_consent(p, vs.get().lower(), vb.get(), vr.get(), vd.get())
            except ValueError as exc:
                messagebox.showerror("Consent", str(exc), parent=win); return
            win.destroy(); self.refresh_people()
        bar = tk.Frame(win, bg=PANEL); bar.grid(row=5, column=0, columnspan=2, pady=12)
        GlowButton(bar, "Save", save, accent=True, width=10).pack(side="left", padx=6)
        GlowButton(bar, "Cancel", win.destroy, width=10).pack(side="left", padx=6)
        self.root.wait_window(win)

    def consent_selected(self) -> None:
        if not self.require_admin():
            return
        p = self._selected_person()
        if p and p["is_owner"]:
            messagebox.showinfo("Consent", "The owner profile does not need consent.", parent=self.root)
        elif p:
            self._consent_dialog(p)

    def withdraw_consent(self) -> None:
        if not self.require_admin() or not self._idle_or_warn():
            return
        p = self._selected_person()
        if not p or p["is_owner"]:
            return
        if messagebox.askyesno("Withdraw consent", f"Withdraw consent for {p['name']}?\n\nTheir face data will be "
                               "DELETED now. Attendance records are kept; they use manual attendance from now on.",
                               icon="warning", parent=self.root):
            FaceRegistration.delete_person_faces(p["id"])
            self.db.set_consent(p["id"], "withdrawn")
            self.db.log_audit("consent_withdrawn", f"{p['name']}: face data deleted")
            self.refresh_people()

    def end_school_year(self) -> None:
        if not self.require_admin() or not self._idle_or_warn():
            return
        n = len([x for x in self.db.list_people() if not x["is_owner"]])
        if not messagebox.askyesno("End school year", f"This will DELETE the face data of all {n} people (everyone except the "
                                   "owner profile) and reset their consent.\n\nPeople and attendance records are kept.\n\n"
                                   "Export your reports and make a backup first. Continue?", icon="warning", parent=self.root):
            return
        res = retention.end_school_year(self.db)
        self.refresh_all()
        messagebox.showinfo("School year ended", f"Face data deleted for {res['faces_deleted']} people; consent reset for "
                            f"{res['people']}.\nRe-register and collect new consent next year.", parent=self.root)

    # ================================================================== accuracy test page
    def _build_accuracy(self, p) -> None:
        self._heading(p, "Accuracy Test", "Measure TAR / FRR / FAR for your thesis. Test only people who gave consent.")
        self.v_truth, self.v_imp, self.v_trials = tk.StringVar(), tk.StringVar(value="Impostor-A"), tk.StringVar(value="10")
        self.v_light, self.v_dist, self.v_acc, self.v_note = (tk.StringVar(value=v) for v in ("Normal", "Near (~50 cm)", "None", ""))
        r1 = tk.Frame(p, bg=BG); r1.pack(fill="x", pady=(0, 6))
        tk.Label(r1, text="Tested person", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(0, 4))
        self.truth_box = ttk.Combobox(r1, textvariable=self.v_truth, values=[IMPOSTOR], state="readonly", width=26, font=(FONT, 10))
        self.truth_box.pack(side="left", padx=(0, 10), ipady=2)
        tk.Label(r1, text="Impostor code", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(0, 4))
        make_entry(r1, self.v_imp, 12).pack(side="left", padx=(0, 10), ipady=3)
        tk.Label(r1, text="Trials", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(0, 4))
        make_entry(r1, self.v_trials, 5).pack(side="left", ipady=3)
        r2 = tk.Frame(p, bg=BG); r2.pack(fill="x", pady=(0, 6))
        for lbl, var, vals, w in (("Lighting", self.v_light, ["Bright", "Normal", "Dim"], 9),
                                  ("Distance", self.v_dist, ["Near (~50 cm)", "Medium (~1 m)", "Far (~2 m)"], 14),
                                  ("Accessory", self.v_acc, ["None", "Glasses", "Mask", "Cap / hat", "Other"], 10)):
            tk.Label(r2, text=lbl, bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(0, 4))
            ttk.Combobox(r2, textvariable=var, values=vals, state="readonly", width=w, font=(FONT, 10)).pack(side="left", padx=(0, 10), ipady=2)
        tk.Label(r2, text="Note", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(0, 4))
        make_entry(r2, self.v_note, 22).pack(side="left", ipady=3)
        r3 = tk.Frame(p, bg=BG); r3.pack(fill="x", pady=(0, 6))
        for label, cmd, acc in (("Start Test", self.start_accuracy, True), ("Stop", self.stop, False),
                                ("Export Excel", lambda: self.export_accuracy("xlsx"), False),
                                ("Export CSV", lambda: self.export_accuracy("csv"), False),
                                ("Clear all results", self.clear_accuracy, False)):
            GlowButton(r3, label, cmd, accent=acc).pack(side="left", padx=(0, 6))
        tk.Label(p, text="Each trial: the tester steps out of view, then looks at the camera. The system decides under the "
                 "same rules as attendance. Nothing is spoken or logged as attendance.", bg=BG, fg=MUTED, font=(FONT, 8),
                 wraplength=860, justify="left").pack(anchor="w")
        self.acc_summary = tk.Label(p, text="", bg=PANEL, fg=ACCENT, font=(FONT, 11, "bold"), anchor="w", padx=12, pady=8,
                                    highlightthickness=1, highlightbackground=BORDER)
        self.acc_summary.pack(fill="x", pady=(6, 6))
        f, self.acc_tree = make_tree(p, [("s", "Session"), ("n", "#"), ("k", "Kind"), ("t", "Truth"), ("c", "Condition"),
                                         ("o", "Outcome"), ("a", "Accepted as"), ("d", "Dist"), ("sec", "Sec"), ("ok", "OK")],
                                     (140, 32, 70, 125, 165, 72, 120, 45, 42, 36), height=9)
        f.pack(fill="both", expand=True)

    def refresh_accuracy(self) -> None:
        people = {pr["name"]: pr for pr in self.db.list_people() if FaceRegistration.sample_count(pr["id"]) > 0}
        self.truth_people = people
        self.truth_box.config(values=list(people) + [IMPOSTOR])
        if self.v_truth.get() not in list(people) + [IMPOSTOR]:
            self.v_truth.set(IMPOSTOR)
        trials = self.db.list_trials()
        m = metrics.summarize(trials)
        f = lambda v: "—" if v is None else f"{v:g}%"
        self.acc_summary.config(text=f"TAR {f(m['TAR'])}   FRR {f(m['FRR'])}   Misid {f(m['MISID'])}   FAR {f(m['FAR'])}   "
                                     f"Accuracy {f(m['accuracy'])}    ({m['genuine']} genuine + {m['impostor']} impostor attempts)")
        names = {pr["id"]: pr["name"] for pr in self.db.list_people()}
        self.acc_tree.delete(*self.acc_tree.get_children())
        for t in reversed(trials[-300:]):
            self.acc_tree.insert("", "end", tags=("ok" if t["correct"] else "late",), values=(
                t["session"], t["trial_no"], t["truth_kind"], t["truth_label"],
                f"{t['lighting']} / {t['distance'].split(' ')[0]} / {t['accessory']}", t["outcome"],
                names.get(t["accepted_person_id"], "") if t["accepted_person_id"] is not None else "",
                "" if t["min_dist"] is None else f"{t['min_dist']:.0f}", f"{t['seconds']:.1f}", "✔" if t["correct"] else "✖"))

    def start_accuracy(self) -> None:
        if not self.require_admin() or not self._idle_or_warn():
            return
        try:
            n = int(self.v_trials.get())
            if not 1 <= n <= 200:
                raise ValueError
        except ValueError:
            messagebox.showerror("Accuracy Test", "Trials must be a whole number from 1 to 200.", parent=self.root); return
        truth = self.v_truth.get()
        if truth == IMPOSTOR or truth not in getattr(self, "truth_people", {}):
            pid, label = None, self.v_imp.get().strip()
            if not label:
                messagebox.showerror("Accuracy Test", "Enter an impostor code (e.g. Impostor-A).", parent=self.root); return
        else:
            pid, label = self.truth_people[truth]["id"], truth
        cond = {"lighting": self.v_light.get(), "distance": self.v_dist.get(), "accessory": self.v_acc.get(), "note": self.v_note.get()}
        if not messagebox.askokcancel("Accuracy Test", f"{n} trial(s) for: {label} ({'registered person' if pid else 'IMPOSTOR'}).\n\n"
                                      "Before each trial the tester must step OUT of the camera view, then look at the camera.\n"
                                      "Only distances and results are saved, never pictures.", parent=self.root):
            return
        self.db.log_audit("accuracy_test_start", f"{label} x{n} {cond['lighting']}/{cond['distance']}/{cond['accessory']}")
        self.show_page("camera")
        self.controller.start_accuracy_test(pid, label, cond, n)
        self._refresh_buttons()

    def export_accuracy(self, kind: str) -> None:
        if not self.require_admin():
            return
        trials = self.db.list_trials()
        if not trials:
            messagebox.showinfo("Nothing to export", "No trials recorded yet.", parent=self.root); return
        path = self._save_dialog(kind, f"accuracy_test_{date.today()}.{kind}")
        if path:
            self._write_export(lambda: (exporter.export_accuracy_xlsx if kind == "xlsx" else exporter.export_trials_csv)(trials, path),
                               path, len(trials), "accuracy trials")

    def clear_accuracy(self) -> None:
        if not self.require_admin():
            return
        if messagebox.askyesno("Clear results", "Delete ALL recorded accuracy trials? Export them first.", icon="warning", parent=self.root):
            n = self.db.clear_trials()
            self.db.log_audit("accuracy_clear", f"{n} trials deleted")
            self.refresh_accuracy()

    # -- settings
    def open_settings(self) -> None:
        if self.require_admin():
            SettingsDialog(self)

    def apply_settings(self, new: config.Settings) -> None:
        changed = [k for k, v in asdict(new).items() if v != getattr(self.settings, k)]
        self.settings = new
        self.controller.settings = new
        self.voice.rate, self.voice.volume, self.voice.voice_name = new.voice_rate, new.voice_volume, new.voice_name
        if changed:
            self.db.log_audit("settings_change", ", ".join(changed))

    def exit_app(self, show_desktop: bool = False) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.controller.stop()
            self.voice.shutdown(wait=2.0)
        finally:
            self.root.destroy()
        if show_desktop:
            _show_desktop()

    def run(self) -> None:
        self.root.mainloop()


# ---------------------------------------------------------------------- settings dialog
FIELDS = [
    ("camera_index", "Camera index", int, "0 = built-in webcam"),
    ("late_after", "Late after (HH:MM, 24h)", str, "e.g. 08:30"),
    ("min_minutes_between_scans", "Minutes before Time Out", int, "scans sooner are ignored"),
    ("scan_cooldown_seconds", "Ignore same person for (s)", int, "after a scan"),
    ("banner_seconds", "Result banner (s)", int, "how long it stays"),
    ("holidays", "Holidays / no-class days", str, "YYYY-MM-DD, comma-separated"),
    ("recognition_timeout_seconds", "Welcome timeout (s)", int, "0 = never"),
    ("required_consecutive_matches", "Consecutive matches needed", int, "higher = safer"),
    ("recognition_threshold", "Match threshold", float, "LOWER = stricter (45-70)"),
    ("recognition_margin", "Match margin", float, "1.0 = off; needs 2+ people"),
    ("min_face_size", "Minimum face size (px)", int, "ignore far faces"),
    ("registration_samples", "Samples when registering", int, "15-200"),
    ("liveness_motion_check", "Require slight head movement", bool, "basic anti-photo"),
    ("liveness_motion_min", "Movement needed (fraction)", float, "0.03-0.15"),
    ("liveness_blink_check", "Require a blink", bool, "glasses may interfere"),
    ("startup_delay_seconds", "Startup delay (s)", int, "after Windows login"),
    ("auto_close_seconds", "Auto-close after welcome (s)", int, "startup mode; 0 = stay"),
    ("show_desktop_after_welcome", "Show desktop after welcome", bool, "startup mode only"),
    ("require_consent", "Require consent to register", bool, "recommended for students"),
    ("reregister_after_days", "Flag face data older than (days)", int, "0 = off"),
    ("trial_timeout_seconds", "Accuracy test: seconds per trial", int, "2-60"),
    ("admin_session_minutes", "Admin unlock lasts (min)", int, "idle timeout"),
    ("auto_backup_keep", "Daily backups to keep", int, "database only"),
    ("voice_rate", "Voice speed", int, "~170"),
    ("voice_volume", "Voice volume", float, "0.1 - 1.0"),
    ("voice_name", "Voice name contains", str, "e.g. Zira (optional)"),
]


class SettingsDialog:
    def __init__(self, app: App):
        self.app = app
        self.win = app._dialog("Settings")
        self.vars: dict = {}
        tk.Label(self.win, text="SETTINGS", bg=PANEL, fg=ACCENT, font=(FONT, 13, "bold")).grid(
            row=0, column=0, columnspan=6, sticky="w", padx=18, pady=(14, 8))
        half = (len(FIELDS) + 1) // 2
        for i, (key, label, typ, hint) in enumerate(FIELDS):
            col, row = (0, i + 1) if i < half else (3, i - half + 1)
            tk.Label(self.win, text=label, bg=PANEL, fg=TEXT, font=(FONT, 10)).grid(row=row, column=col, sticky="w", padx=(18, 8), pady=2)
            val = getattr(app.settings, key)
            if typ is bool:
                v = tk.BooleanVar(value=bool(val))
                tk.Checkbutton(self.win, variable=v, bg=PANEL, activebackground=PANEL, selectcolor=PANEL2,
                               fg=TEXT).grid(row=row, column=col + 1, sticky="w")
            else:
                v = tk.StringVar(value=str(val))
                make_entry(self.win, v, 16).grid(row=row, column=col + 1, sticky="w", pady=1)
            tk.Label(self.win, text=hint, bg=PANEL, fg=MUTED, font=(FONT, 8)).grid(row=row, column=col + 2, sticky="w", padx=(6, 12))
            self.vars[key] = (v, typ)
        bar = tk.Frame(self.win, bg=PANEL)
        bar.grid(row=half + 2, column=0, columnspan=6, pady=12)
        GlowButton(bar, "Save", self.save, accent=True, width=12).pack(side="left", padx=6)
        GlowButton(bar, "Reset to defaults", self.reset, width=16).pack(side="left", padx=6)
        GlowButton(bar, "Cancel", self.win.destroy, width=12).pack(side="left", padx=6)

    def save(self) -> None:
        values = asdict(self.app.settings)
        for key, (var, typ) in self.vars.items():
            raw = var.get()
            try:
                values[key] = bool(raw) if typ is bool else typ(raw)
            except ValueError:
                messagebox.showerror("Invalid value", f"'{raw}' is not valid for {key}.", parent=self.win); return
        new = config.Settings(**values).validate()
        try:
            config.save_settings(new)
        except OSError as exc:
            messagebox.showerror("Could not save", str(exc), parent=self.win); return
        self.app.apply_settings(new)
        self.win.destroy()
        messagebox.showinfo("Settings", "Settings saved.", parent=self.app.root)

    def reset(self) -> None:
        d = config.Settings()
        for key, (var, typ) in self.vars.items():
            var.set(bool(getattr(d, key)) if typ is bool else str(getattr(d, key)))
