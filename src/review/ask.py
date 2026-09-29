"""
ask.py — the questions a filing asks Sunil mid-run, and the notices it leaves him.

    ask(key, question, ...)   a portal wants something only he has now — the code Greenhouse
                              emails before it takes a submission, a Workday email
                              verification: written to data/asks/<key>.json, a short Telegram
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
import json
import os
import time

from jobpilot.core.paths import DATA

ASK_DIR = os.path.join(DATA, "asks")


def _path(key):
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in key)
    return os.path.join(ASK_DIR, f"{safe}.json")


def load(key):
    p = _path(key)
    return json.load(open(p)) if os.path.isfile(p) else None


def answer(key, text):
    q = load(key) or {"key": key}
    q["answer"] = (text or "").strip()
    q["answered_at"] = dt.datetime.now().isoformat(timespec="seconds")
    os.makedirs(ASK_DIR, exist_ok=True)
    json.dump(q, open(_path(key), "w"), indent=2)
    return q


def _all():
    out = []
    for f in sorted(os.listdir(ASK_DIR)) if os.path.isdir(ASK_DIR) else []:
        try:
            out.append(json.load(open(os.path.join(ASK_DIR, f))))
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


def note(key, question, about="", hint="", link=""):
    """A notice for the review list — a captcha, say: nothing waits on it, Telegram is told."""
    os.makedirs(ASK_DIR, exist_ok=True)
    json.dump({"key": key, "kind": "notice", "question": question, "hint": hint, "about": about, "link": link,
               "asked_at": dt.datetime.now().isoformat(timespec="seconds")}, open(_path(key), "w"), indent=2)
    _tell(about)


def ask(key, question, hint="", timeout=900, poll=3, about=""):
    """Post the question (the review list's "Questions from filing"), send Telegram one line
    with the list's link, wait for the answer. Returns '' on timeout. `about`: the job."""
    os.makedirs(ASK_DIR, exist_ok=True)
    until = dt.datetime.now() + dt.timedelta(seconds=timeout)
    json.dump({"key": key, "question": question, "hint": hint, "about": about, "answer": None,
               "asked_at": dt.datetime.now().isoformat(timespec="seconds"),
               "until": until.isoformat(timespec="seconds")},
              open(_path(key), "w"), indent=2)
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
