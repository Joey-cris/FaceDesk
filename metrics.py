"""Accuracy metrics for the Accuracy Test mode (pure functions - easy to verify by hand).

Definitions (state them in the paper):
  Genuine attempt  = a registered person presents their own face.
  Impostor attempt = an UNREGISTERED person presents their face.
  TAR   (true accept rate)  = genuine attempts accepted as the CORRECT person / genuine attempts
  FRR   (false reject rate) = genuine attempts not accepted (timeout)         / genuine attempts
  MISID (misidentification) = genuine attempts accepted as a DIFFERENT person / genuine attempts
  FAR   (false accept rate) = impostor attempts accepted as anyone            / impostor attempts
  TRR   (true reject rate)  = 1 - FAR
  Accuracy = (correct accepts + correct rejects) / all attempts
  TAR + FRR + MISID = 100 % of genuine attempts.
"""
from __future__ import annotations

from typing import Optional


def pct(n: int, d: int) -> Optional[float]:
    return None if d == 0 else round(100.0 * n / d, 1)


def summarize(trials: list[dict]) -> dict:
    gen = [t for t in trials if t["truth_kind"] == "genuine"]
    imp = [t for t in trials if t["truth_kind"] == "impostor"]
    ok = [t for t in gen if t["accepted_person_id"] is not None and t["accepted_person_id"] == t["truth_person_id"]]
    wrong = [t for t in gen if t["accepted_person_id"] is not None and t["accepted_person_id"] != t["truth_person_id"]]
    fa = [t for t in imp if t["accepted_person_id"] is not None]
    rejected = len(gen) - len(ok) - len(wrong)
    secs = [t["seconds"] for t in ok]
    return {
        "attempts": len(trials), "genuine": len(gen), "impostor": len(imp),
        "correct_accepts": len(ok), "false_rejects": rejected, "misidentified": len(wrong),
        "false_accepts": len(fa), "correct_rejects": len(imp) - len(fa),
        "TAR": pct(len(ok), len(gen)), "FRR": pct(rejected, len(gen)), "MISID": pct(len(wrong), len(gen)),
        "FAR": pct(len(fa), len(imp)), "TRR": pct(len(imp) - len(fa), len(imp)),
        "accuracy": pct(len(ok) + len(imp) - len(fa), len(trials)),
        "mean_seconds": round(sum(secs) / len(secs), 2) if secs else None,
        "liveness_blocked": sum(1 for t in trials if t.get("liveness_blocked")),
    }


def by_condition(trials: list[dict], key: str) -> list[dict]:
    """Summaries split by one condition column: 'lighting', 'distance' or 'accessory'."""
    out = []
    for value in sorted({t.get(key, "") or "(not set)" for t in trials}):
        subset = [t for t in trials if (t.get(key, "") or "(not set)") == value]
        out.append({"condition": key, "value": value, **summarize(subset)})
    return out


def threshold_table(trials: list[dict], thresholds) -> list[dict]:
    """APPROXIMATE: re-evaluates each trial using only its closest-match distance (ignores margin,
    consecutive-frame and liveness rules). Use it to choose a threshold, then confirm with a new test."""
    gen = [t for t in trials if t["truth_kind"] == "genuine"]
    imp = [t for t in trials if t["truth_kind"] == "impostor"]
    rows = []
    for th in thresholds:
        tar = sum(1 for t in gen if t["min_dist"] is not None and t["min_dist"] <= th and t["min_label"] == t["truth_person_id"])
        far = sum(1 for t in imp if t["min_dist"] is not None and t["min_dist"] <= th)
        rows.append({"threshold": th, "TAR": pct(tar, len(gen)), "FRR": None if not gen else round(100 - pct(tar, len(gen)), 1),
                     "FAR": pct(far, len(imp)), "genuine": len(gen), "impostor": len(imp)})
    return rows
