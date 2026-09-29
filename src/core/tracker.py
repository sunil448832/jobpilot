#!/usr/bin/env python3
"""
tracker.py — where each sent application stands after it went: follow-ups, from the queue.

A submitted card (core/cards.py) is due a follow-up 5 BUSINESS days after its
submitted_at (config followups.job_days). On the review list's Submitted table Sunil marks
what happened since; the card keeps it as `after` = {"status": ..., "at": ...}:

  followed_up   he chased it
  heard_back    they answered (a screen, an interview, a question)
  rejected      a no
  (unset)       nothing yet — the daily digest lists it once it is due

Any mark ends the reminder. This replaced the sync into tracking/job-tracker.xlsx
(2026-09-29): nothing read that sheet but these reminders and nobody updated its Stage,
so a reminder could never be closed. The sheet and the tracking/ folder are gone.

    python -m jobpilot.core.tracker --followups     # what is due today (./jobpilot follow)
"""
import argparse
import datetime as dt
import sys

from jobpilot.core import cards as CD  # noqa: E402
from jobpilot.core.config import cfg  # noqa: E402
AFTER = {"followed_up": "Followed up", "heard_back": "Heard back", "rejected": "Rejected"}


def business_days_from(d, n=None):
    n = n if n is not None else cfg("followups.job_days", 5)
    cur, added = d, 0
    while added < n:
        cur += dt.timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    return cur


def due_date(item):
    """The day a submitted card is due its follow-up, or None (not sent, no date)."""
    if item.get("status") != "submitted" or not item.get("submitted_at"):
        return None
    try:
        return business_days_from(dt.date.fromisoformat(item["submitted_at"][:10]))
    except ValueError:
        return None


def followups(days_ahead=0):
    """Submitted cards past their follow-up day with nothing marked since, oldest first."""
    today = dt.date.today()
    due = []
    for d in CD.cards():
        day = due_date(d)
        if day is None or (d.get("after") or {}).get("status"):
            continue
        if day <= today + dt.timedelta(days=days_ahead):
            due.append({"id": d.get("id"), "company": d.get("company"), "role": d.get("role"),
                        "due": day.isoformat(), "overdue": (today - day).days})
    return sorted(due, key=lambda x: x["due"])


def mark(item_id, status):
    """What happened after a submitted card was sent (AFTER); an empty status clears it."""
    if status and status not in AFTER:
        raise ValueError(f"unknown status {status!r}; one of {', '.join(AFTER)}")
    d = CD.load(item_id)
    if d.get("status") != "submitted":
        raise ValueError(f"{item_id} is {d.get('status')}, not submitted")
    if status:
        d["after"] = {"status": status, "at": dt.datetime.now().isoformat(timespec="seconds")}
    else:
        d.pop("after", None)
    CD.save(d)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--followups", action="store_true", help="what is due today")
    ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    due = followups()
    if not due:
        print("  nothing due")
    for d in due:
        tag = f"{d['overdue']}d overdue" if d["overdue"] > 0 else "due today"
        print(f"  [{tag:>12}] {d['company']} — {d['role']}")
    if due:
        print("\n  mark each on the review list (Submitted → After): followed up, heard back, rejected")


if __name__ == "__main__":
    main()
