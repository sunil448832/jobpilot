"""
ask.py — a question the pipeline cannot answer itself, put to Sunil mid-run.

Used when a portal wants something only he has at that moment — the 8-character
verification code Greenhouse emails before it accepts a submission, for one.
The question is written to data/asks/<key>.json, a Telegram message carries a
link to /ask/<key> on the review server, and the caller polls the file until
he saves an answer or the timeout passes. Nothing here submits anything.
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


def open_asks():
    """The questions still waiting for his answer, oldest first: those unanswered whose wait
    has not run out."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    out = []
    for f in sorted(os.listdir(ASK_DIR)) if os.path.isdir(ASK_DIR) else []:
        try:
            q = json.load(open(os.path.join(ASK_DIR, f)))
        except (OSError, ValueError):
            continue
        if not q.get("answer") and (q.get("until") or "") > now:
            out.append(q)
    return sorted(out, key=lambda q: q.get("asked_at") or "")


def ask(key, question, hint="", timeout=900, poll=3, about=""):
    """Post the question (it shows in the review list's "Needs you now" section), tell
    Telegram as a card, wait for the answer. Returns '' on timeout. `about`: the job."""
    from jobpilot.core.daily import form_link, telegram
    os.makedirs(ASK_DIR, exist_ok=True)
    until = dt.datetime.now() + dt.timedelta(seconds=timeout)
    json.dump({"key": key, "question": question, "hint": hint, "about": about, "answer": None,
               "asked_at": dt.datetime.now().isoformat(timespec="seconds"),
               "until": until.isoformat(timespec="seconds")},
              open(_path(key), "w"), indent=2)
    link = form_link().replace("/?", f"/ask/{key}?")
    telegram("🔐 <b>Filing needs you</b>\n"
             + (f"<b>{about}</b>\n" if about else "")
             + f"\n{question}\n"
             + (f"<i>{hint}</i>\n" if hint else "")
             + f"\n👉 Answer here: {link}\n"
             + "(also at the top of the review list, under <b>Needs you now</b>)\n\n"
             + f"⏳ Waiting until <b>{until:%H:%M}</b> ({timeout // 60} min). "
             + "After that nothing is sent, and it is tried again on the next run.")
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
