"""Admin PIN: salted PBKDF2 hash, lock-out after repeated failures, audit trail.

Honest scope: the PIN stops casual users from using the admin screens. It does NOT protect the
files from someone who can open the computer's folders - use Windows account security / BitLocker for that.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Callable

from db import Database

ITERATIONS = 200_000
MAX_FAILS = 5
LOCK_SECONDS = 300


def _hash(pin: str, salt: bytes, iterations: int = ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)


class AdminAuth:
    def __init__(self, db: Database, time_fn: Callable[[], float] = time.time):
        self.db, self._now = db, time_fn

    def has_pin(self) -> bool:
        return self.db.get_meta("pin_hash") is not None

    @staticmethod
    def validate_pin(pin: str) -> str:
        if len(pin) < 4:
            return "PIN must be at least 4 characters."
        if len(pin) > 32:
            return "PIN is too long (max 32)."
        if len(set(pin)) == 1:
            return "PIN cannot be a single repeated character."
        return ""

    def set_pin(self, pin: str) -> None:
        err = self.validate_pin(pin)
        if err:
            raise ValueError(err)
        salt = os.urandom(16)
        self.db.set_meta("pin_hash", f"{ITERATIONS}${salt.hex()}${_hash(pin, salt).hex()}")
        self.db.set_meta("pin_fails", "0")
        self.db.del_meta("pin_locked_until")

    def verify(self, pin: str) -> tuple[bool, str]:
        stored = self.db.get_meta("pin_hash")
        if stored is None:
            return True, ""
        until = float(self.db.get_meta("pin_locked_until", "0"))
        if self._now() < until:
            mins = int((until - self._now()) / 60) + 1
            return False, f"Too many wrong attempts. Try again in about {mins} minute(s)."
        iters, salt_hex, hash_hex = stored.split("$")
        ok = hmac.compare_digest(_hash(pin, bytes.fromhex(salt_hex), int(iters)).hex(), hash_hex)
        if ok:
            self.db.set_meta("pin_fails", "0")
            return True, ""
        fails = int(self.db.get_meta("pin_fails", "0")) + 1
        self.db.log_audit("pin_failed", f"wrong PIN attempt {fails}", actor="unknown")
        if fails >= MAX_FAILS:
            self.db.set_meta("pin_fails", "0")
            self.db.set_meta("pin_locked_until", str(self._now() + LOCK_SECONDS))
            self.db.log_audit("pin_locked", f"locked for {LOCK_SECONDS // 60} minutes", actor="unknown")
            return False, f"Wrong PIN. Admin is locked for {LOCK_SECONDS // 60} minutes."
        self.db.set_meta("pin_fails", str(fails))
        return False, f"Wrong PIN ({MAX_FAILS - fails} attempt(s) left)."

    def change_pin(self, old: str, new: str) -> tuple[bool, str]:
        ok, msg = self.verify(old)
        if not ok:
            return False, msg
        err = self.validate_pin(new)
        if err:
            return False, err
        self.set_pin(new)
        self.db.log_audit("pin_changed")
        return True, ""

    def remove_pin(self, current: str) -> tuple[bool, str]:
        ok, msg = self.verify(current)
        if not ok:
            return False, msg
        for k in ("pin_hash", "pin_fails", "pin_locked_until"):
            self.db.del_meta(k)
        self.db.log_audit("pin_removed")
        return True, ""
