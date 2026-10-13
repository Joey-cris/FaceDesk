# FaceDesk: Smart Welcome and Attendance System  (v2.1)

Local, offline face recognition for Windows 11, for a home, a classroom or a small school.

* **Welcome mode** - greets whoever sits down, once ("Welcome back, Master Joey Galolo."), then stops. Used at Windows startup.
* **Attendance mode** - keeps running; each recognised person gets **Time In**, and a later scan becomes **Time Out**.
* **Dashboard** - Registered / Present / Late / Absent, today's table, 7-day chart, **filter by section**.
* **People** - add people or **import a CSV**; sections and student IDs; register / delete faces.
* **Attendance Log** - filter by date, section, name; **manual entry with a mandatory reason**; export **Excel / CSV**; **summary report** (days present / late / absent / attendance %, weekends and holidays excluded).
* **Consent tracking** - record parental/participant consent; optional gate that blocks face registration without it; withdraw consent = face data deleted.
* **Class mode** - attendance for one section: a student from another section is recognised but **not logged**.
* **Accuracy Test** - runs genuine/impostor trials and computes **TAR / FRR / misidentification / FAR / accuracy**, by lighting, distance and accessory, with a threshold analysis. Exports an Excel workbook for **Chapter 4**.
* **Retention** - flags face data older than 180 days (children's faces change) and **End School Year** deletes all students' face data.
* **Admin** - **PIN lock**, **backup & restore**, **audit log**, **self-check**.

Everything stays on this computer. No network, no cloud.

> ## ⚠️ Read this first
> * Webcam face recognition can be fooled by a photo, a phone screen, a video or a look-alike. The liveness check (slight head
>   movement, optional blink) only raises the bar a little. **It is a convenience, not secure authentication.**
>   Do not use it as the only basis for grades, pay, discipline or building access. A person should review the records.
> * It does **not** unlock Windows. (This laptop's webcam has no IR sensor, so Windows Hello Face is unavailable.)
> * **Children's faces are biometric data.** Before using it with students you need the school's approval, written parental
>   consent, and a manual alternative for anyone who opts out. In the Philippines this falls under the Data Privacy Act -
>   involve the school's Data Protection Officer. (I am not a lawyer.)
> * The Admin PIN stops casual use of the admin screens. It does **not** protect files from someone who can open the PC's
>   folders - use a Windows password and BitLocker for that. The face data and database are **not encrypted** by FaceDesk.

---

## Tested vs. NOT tested

| Area | Status |
|---|---|
| Database: people, sections, student IDs, Time In / Late / duplicate / Time Out, stats, search, upgrade of an old database | ✅ Automated tests |
| Manual entries (reason required, marked "Manual"), summary report math (weekends, holidays, future days, join date) | ✅ Automated tests |
| Admin PIN: salted hash, weak PINs refused, lock-out after 5 wrong tries, change / remove | ✅ Automated tests |
| Audit log (incl. that it **survives a restore** - a bug found and fixed during testing) | ✅ Automated tests |
| Backup / restore (with and without faces), corrupt / malicious zip rejected, auto-backup once a day + pruning | ✅ Automated tests |
| CSV import: header aliases, `;` delimiter, Excel BOM, Windows-1252, bad rows reported, template round-trip | ✅ Automated tests |
| Excel / CSV exports (re-opened and checked) | ✅ Automated tests |
| Self-check (never crashes; reports failures with a fix) | ✅ Automated tests |
| Consent: 'granted' needs a guardian name; registration refused without it (owner exempt); withdraw deletes faces | ✅ Automated tests + GUI run |
| Class mode: other section's student recognised but not logged; a stranger is still rejected | ✅ Automated tests (a design flaw was found and fixed - see below) |
| Accuracy Test: genuine, impostor, misidentification and false-accept trials; **metrics checked against a hand calculation**; Excel export | ✅ Automated tests |
| End School Year, stale face data (180 days) | ✅ Automated tests |
| Multi-person recognition, stranger rejection (threshold + margin), attendance logic | ✅ Automated tests with synthetic faces |
| Real face detector + liveness on a real photo (single person) | ✅ Automated tests |
| Whole GUI: CSV import → register 4 people → PIN set/lock/wrong PIN/unlock → attendance → stranger → manual entry → exports → backup → restore → audit | ✅ Run under a virtual display with a simulated camera |
| **Real webcam, real people, real lighting, real children's faces** | ❌ **Not tested** |
| **Real Windows voices / speakers** | ❌ **Not tested** (the self-check Voice line is how you find out) |
| **All .bat files, startup after reboot, Win+D desktop reveal** | ❌ **Not tested** (never run on Windows) |
| **PyInstaller .exe** | ❌ **Not built or run - do not assume it works** |
| **Accuracy with a whole class or school** | ❌ Unknown. LBPH is best with ~20-30 people per camera; pilot it first |

**Why a margin rule exists:** in an early test a synthetic stranger scored *under* the distance threshold and was logged as
present. So FaceDesk also requires the best person to beat the runner-up (`recognition_margin`, 1.25). That works only with
**2+ people** registered. Real faces will behave differently from my test faces - test with real people and tune.

**A design flaw found while testing Class mode:** my first version compared a face only against that class's students. With a
class of one person there is no runner-up, so the margin rule switched off and a stranger could be accepted. Class mode now
ranks **everyone** registered and only then requires the winner to be in the chosen section. (An earlier message of mine
claimed class mode improves accuracy - that was wrong; it only prevents logging students in the wrong class.)

**⚠ About the Accuracy Test numbers in my tests:** they come from *synthetic* faces, so "TAR 100 %" there proves the
**calculation and the app work**, NOT that recognition is accurate. Your thesis numbers must come from real participants.

**Not built yet (honest list):** a newer, more accurate recognition model (YuNet + SFace - I could not download the model
files in my test environment, so I did not ship untested code), encryption of face data at rest, roles (admin / teacher /
guard, so a teacher sees only their section), a DepEd School Form 2 export (I need your school's sample to match the format), per-subject schedules, SMS notifications, multi-camera. See "What to do next" at the bottom.

Run the tests: `.venv\Scripts\pip install scikit-image` then `.venv\Scripts\python -m unittest discover -s tests -v`

---

## 1. Install (Windows 11)

1. Extract the ZIP so that `setup.bat` sits directly in `C:\FaceDesk` (not `C:\FaceDesk\FaceDesk`).
2. Python 3.12 (64-bit): `winget install -e --id Python.Python.3.12` (keep "tcl/tk and IDLE").
3. In **PowerShell** (note the `.\`):
   ```
   cd C:\FaceDesk
   .\setup.bat
   .\run.bat
   ```
   (`install_all.bat` also builds an exe and a Desktop shortcut - the exe is unverified.)
4. **First thing: click Self-check** (Camera page). Fix every FAIL line before going further.

**Upgrading from the previous FaceDesk / JoeyAssistant?** Copy the old `data` folder into the new folder before first run.
The database is upgraded automatically; old single-user face samples are moved to the owner profile.

## 2. First use

1. **Admin** page → **Set / Change PIN** (strongly recommended before anyone else uses the PC).
2. **People** → **+ Add Person**, or **Import CSV** (use **CSV Template** to get the right layout - in Excel: Save As → CSV).
3. Select a person → **Register / Re-register Face**, or **Register next unregistered** to go through a class one by one.
4. **Camera** → **Test Voice**, then **Start Attendance**. Look at the camera and move your head very slightly.
5. **Dashboard** (filter by section), **Attendance Log** (export), **Summary →** buttons for reports.

### How attendance is decided
* First confirmed scan of the day = **Time In** (after `late_after`, default 08:30 = **Late**).
* A scan at least `min_minutes_between_scans` (30) after the last event = **Time Out** (later scans update it).
* Scans closer together are ignored. Only the **largest face** in view is processed - people must come one at a time.
* **Manual Entry** (Attendance Log) fixes mistakes or records excused absences; the reason is mandatory, the row is marked
  "Manual", and the change is written to the audit log.
* **Summary report** counts school days = Mon-Fri, minus `holidays` (Settings, `YYYY-MM-DD` list), never in the future, and
  never before the person was added.

### Admin PIN
Pages People, Attendance Log, Accuracy Test, Admin, Settings, and all exports/deletes ask for the PIN. Camera and Dashboard stay open so
a guard can run attendance. Unlock lasts `admin_session_minutes` (10) of inactivity. Five wrong tries lock admin for 5 minutes.
**Forgot the PIN?** Close FaceDesk, then with someone who has access to the PC run in the project folder:
`.venv\Scripts\python -c "import db; d=db.Database(); [d.del_meta(k) for k in ('pin_hash','pin_fails','pin_locked_until')]; d.log_audit('pin_reset_offline','PIN cleared from the command line')"`
(this is logged). Anyone with access to the files can do this - see the warning above.

### Backups
* **Automatic:** one database-only backup per day in `backups\` (last 14 kept).
* **Manual:** Admin → Backup now. Tick *Include face data* only if you need it (it is biometric data - keep it safe).
* **Restore:** Admin → Restore backup. A safety copy of the current data is saved first; the audit log is kept.

## 2b. For a National High School pilot (minors)

1. Get the written approvals first (principal, school/division office as required, parents) and involve the school's Data
   Protection Officer. Face data of minors is sensitive personal data under the Data Privacy Act (RA 10173). I am not a lawyer.
2. **Settings → Require consent to register = on.** Then for every learner: People → select → **Consent...** → Granted
   (parent/guardian name + form number) *only after* you hold the signed form. Declined = manual attendance, no penalty.
3. Import the class list (CSV: Name, LRN as Student ID, Section). Register learners one by one.
4. Camera page → **Class** = the section → **Start Attendance**. Realistic scale: **one section (30-40 learners) per camera**, at the classroom door/homeroom, not a whole school at the gate.
5. A learner who withdraws consent: **Withdraw consent** (face data is deleted at once).
6. Re-register learners each semester (rows marked "Re-register (old)"). At the end of the year: export reports, back up, then **Admin → End School Year...**.
7. Keep a person in the loop: attendance must never be the only basis for grades or discipline.

## 2c. Using the Accuracy Test for Chapter 4

**Definitions (put these in your paper):** *Genuine attempt* = a registered person presents their own face. *Impostor attempt* =
an unregistered person presents their face. **TAR** = genuine attempts accepted as the correct person ÷ genuine attempts.
**FRR** = genuine attempts not accepted ÷ genuine. **Misid** = genuine accepted as a *different* person ÷ genuine (TAR + FRR + Misid = 100 %).
**FAR** = impostor attempts accepted as anyone ÷ impostor attempts. **Accuracy** = (correct accepts + correct rejects) ÷ all attempts.

**Procedure:** (a) register the participants (with consent); (b) Accuracy Test page → pick a registered person, set lighting /
distance / accessory, trials (e.g. 10) → **Start Test**; before every trial the tester steps out of view and looks at the camera;
(c) for **impostors** choose "Unregistered person" and give a code (Impostor-A...) - impostors also need consent; (d) repeat for each
condition; (e) **Export Excel**: sheets *Summary* (overall + by lighting / distance / accessory), *Trials* (raw data), *Threshold
analysis* (approximate TAR/FAR for thresholds 40-90 - use it to justify your threshold, then confirm with a fresh test).
Only distances and outcomes are stored, never pictures. To test *spoofing*, have a consenting participant hold up a photo of a
registered person as an "impostor" and report what happens, even if it is bad news - honest limitations are good research.
Use enough trials (dozens per condition, several participants) before drawing conclusions.

## 3. Automatic startup (optional, after everything works)
* `enable_startup.bat` - welcome mode after *you* log in (uses the exe if built, else Python). Greets, closes after
  `auto_close_seconds`, then (optionally) presses Win+D. No admin rights, no console window. `disable_startup.bat` undoes it.
* Attendance kiosk: `run_attendance.bat` (put a shortcut in `shell:startup` yourself if wanted).

## 4. Self-check from the command line
`selfcheck.bat` (add `camera` to test the webcam) or the **Self-check** button.

## Settings (button, or `config.json`)

| Setting | Default | Meaning |
|---|---|---|
| `recognition_threshold` | 60 | Max LBPH distance. **Lower = stricter** |
| `recognition_margin` | 1.25 | Best person must be this much closer than the runner-up (1.0 = off; needs 2+ people) |
| `required_consecutive_matches` | 8 | Frames in a row that must match the **same** person |
| `late_after` / `min_minutes_between_scans` | 08:30 / 30 | Attendance rules |
| `holidays` | empty | No-class days for the summary report |
| `admin_session_minutes` | 10 | Admin unlock idle timeout |
| `liveness_motion_check` / `_blink_check` | on / off | Basic anti-photo checks |

**Tuning with real people:** the Camera page shows the live *Match distance*. Have each person scan and note the numbers,
then have an unregistered person scan. Set the threshold a little above the highest genuine distance and below the strangers'.
Stranger accepted → lower the threshold or raise the margin (1.4). Real people rejected → re-register in their usual lighting
or raise the threshold.

## Troubleshooting
Run **Self-check** first. Then:

| Message | Fix |
|---|---|
| Camera Unavailable | Close Teams/Zoom/Camera app; try `camera_index` 1 |
| only black frames | Open the **Lenovo privacy shutter** / camera Fn key |
| no pictures | Settings → Privacy & security → Camera → allow desktop apps |
| Unknown Face for a real person | Re-register in normal lighting; raise `recognition_threshold` (70) |
| Stranger accepted | Lower threshold; raise `recognition_margin`; register more people |
| "please move your head slightly" | Move a little, or turn off `liveness_motion_check` |
| OpenCV Contrib missing | `.venv\Scripts\pip uninstall -y opencv-python opencv-contrib-python` then `.venv\Scripts\pip install opencv-contrib-python==4.10.0.84` |
| Excel export fails | Close the file in Excel, or use CSV |
| Audio unavailable | Check speakers; install a voice (Settings → Time & language → Speech) |
| Restore failed | Nothing was changed; check the file is a FaceDesk backup |
| Anything else | `logs\assistant.log` |

## Uninstall
`disable_startup.bat` → People → **Delete ALL Face Data** (or delete `data\`) → delete the folder (also `backups\` - old
backups may contain faces). Nothing is installed system-wide.

## Files
`main.py` entry • `ui.py` pages • `controller.py` camera thread • `face_engine.py` detection, LBPH, liveness (not named
`face_recognition.py` on purpose - a PyPI package of that name would clash) • `face_registration.py` • `db.py` SQLite •
`security.py` PIN • `backup.py` • `importer.py` CSV • `exporter.py` • `selfcheck.py` • `voice_assistant.py` • `camera.py` •
`metrics.py` accuracy maths • `retention.py` • `config.py` • `tests/` • `*.bat`

## What to do next (in order)
1. Run it on the real laptop with 2-3 real people; use Self-check; tune the threshold/margin.
2. Pilot with one class for 1-2 weeks, with consent, and check the records by hand.
3. Then: a more accurate recognition model, encryption at rest, roles, schedules per subject, multi-camera.
