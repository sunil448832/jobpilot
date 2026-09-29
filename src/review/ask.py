"""
ask.py — the questions a filing asks Sunil mid-run, and the notices it leaves him.

    ask(key, question, ...)   a portal wants something only he has now — the code Greenhouse
                              emails before it takes a submission, a Workday email
                              verification: written to applications/<slug>/asks/<key>.json (the
                              job it is about; data/asks/ when there is none), a short Telegram
                              message with the questions page's link (/questions) and the review
                              list's, and the caller polls the file until he answers or the wait
                              runs out
    note(key, question, ...)  something a filing cannot pass and nobody can answer by typing —
                              a captcha: listed the same way, nothing waits on it
    recent(days)              every question and notice of the last days, newest first: the
                              questions page (review/serve.py questions_page)

Nothing here submits anything.
"""
import datetime as dt
import glob
import json
import os
import time

from jobpilot.core.paths import APPLICATIONS, DATA

ASK_DIR = os.path.join(DATA, "asks")          # only for a question about no one job


def _safe(key):
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in key)


def _new_path(key, slug=None):
    """Where a question is written: with the job it is about, else data/asks/."""
    d = os.path.join(APPLICATIONS, slug, "asks") if slug else ASK_DIR
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{_safe(key)}.json")


def _files(name="*.json"):
    return glob.glob(os.path.join(APPLICATIONS, "*", "asks", name)) + glob.glob(os.path.join(ASK_DIR, name))


def _path(key):
    """An existing question's file, wherever it was written; None when there is none."""
    hit = _files(f"{_safe(key)}.json")
    return hit[0] if hit else None


def load(key):
    p = _path(key)
    return json.load(open(p)) if p else None


def answer(key, text):
    p = _path(key) or _new_path(key)
    q = load(key) or {"key": key}
    q["answer"] = (text or "").strip()
    q["answered_at"] = dt.datetime.now().isoformat(timespec="seconds")
    json.dump(q, open(p, "w"), indent=2)
    return q


def _all():
    out = []
    for f in sorted(_files(), key=os.path.basename):
        try:
            out.append(json.load(open(f)))
        except (OSError, ValueError):
            continue
    return out


def state(q, now=None):
    """waiting / answered / expired / notice."""
    now = now or dt.datetime.now().isoformat(timespec="seconds")
    if q.get("kind") == "notice":
        return "notice"
    if q.get("answer"):
        return "answered"
    return "waiting" if (q.get("until") or "") > now else "expired"


def open_asks():
    """The questions still waiting for his answer, oldest first."""
    return sorted([q for q in _all() if state(q) == "waiting"], key=lambda q: q.get("asked_at") or "")


def recent(days=7):
    """Every question and notice of the last `days`, newest first."""
    since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
    return sorted([q for q in _all() if (q.get("asked_at") or "") >= since],
                  key=lambda q: q.get("asked_at") or "", reverse=True)


def _tell(about):
    """One short Telegram message: input is needed — the questions page to give it on, and the
    review list."""
    from jobpilot.core.daily import form_link, telegram
    link = form_link()
    telegram("🔐 <b>Filing needs your input</b>" + (f" — {about}" if about else "")
             + f"\nAnswer here: {link.replace('/?', '/questions?')}"
             + f"\nReview list: {link}")


def note(key, question, about="", hint="", link="", slug=None):
    """A notice for the review list — a captcha, say: nothing waits on it, Telegram is told.
    `slug`: the job it is about (its applications/ folder keeps it)."""
    json.dump({"key": key, "kind": "notice", "question": question, "hint": hint, "about": about, "link": link,
               "asked_at": dt.datetime.now().isoformat(timespec="seconds")}, open(_new_path(key, slug), "w"), indent=2)
    _tell(about)


def ask(key, question, hint="", timeout=900, poll=3, about="", slug=None):
    """Post the question (the review list's "Questions from filing"), send Telegram one line
    with the list's link, wait for the answer. Returns '' on timeout. `about`: the job;
    `slug`: its applications/ folder, which keeps the question."""
    until = dt.datetime.now() + dt.timedelta(seconds=timeout)
    json.dump({"key": key, "question": question, "hint": hint, "about": about, "answer": None,
               "asked_at": dt.datetime.now().isoformat(timespec="seconds"),
               "until": until.isoformat(timespec="seconds")},
              open(_new_path(key, slug), "w"), indent=2)
    _tell(about)
    print(f"  [ask] waiting up to {timeout}s for: {question[:70]}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        q = load(key)
        if q and q.get("answer"):
            print(f"  [ask] answered after {int(timeout - (deadline - time.time()))}s")
            return q["answer"]
        time.sleep(poll)
    print("  [ask] no answer in time")
    return ""
