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


def ask(key, question, hint="", timeout=900, poll=3):
    """Post the question, tell Telegram, wait for the answer. Returns '' on timeout."""
    from jobpilot.core.daily import form_link, telegram
    os.makedirs(ASK_DIR, exist_ok=True)
    json.dump({"key": key, "question": question, "hint": hint, "answer": None,
               "asked_at": dt.datetime.now().isoformat(timespec="seconds")},
              open(_path(key), "w"), indent=2)
    link = form_link().replace("/?", f"/ask/{key}?")
    telegram(f"❓ <b>Need one thing from you</b>\n\n{question}\n\n{link}\n\n"
             f"<i>The browser is holding the form open for {timeout // 60} min.</i>")
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
