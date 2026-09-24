#!/usr/bin/env python3
"""
bot.py — Telegram approval + clarification loop (Phase 3).

Two kinds of card reach the phone:

  QUESTION CARD  — for anything autofill could not answer. Shows 2-3 drafted
                   answers as buttons, plus "write my own". Picking one records
                   it; writing your own marks the item for revision so Claude can
                   rewrite the options and re-send.

  APPROVAL CARD  — only once every REQUIRED question is answered. Shows the form
                   screenshot and the values that will be sent.
                   [Approve] [Skip] [Fields]

Approve is deliberately unreachable while a required question is open: a
half-answered form should never be submittable by a single tap.

Dependency-free: plain Bot API over `requests`.
LinkedIn sending is never automated.

Usage:
    python jobs/bot.py --serve [--duration N]
    python jobs/bot.py --push <id>        # push an item (questions first)
    python jobs/bot.py --revise <id>      # re-send questions after new options
    python jobs/bot.py --status
    python jobs/bot.py --demo
"""
import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import sys
import time

import requests

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)
QUEUE_DIR = os.path.join(DATA, "queue")
REPLIES = os.path.join(QUEUE_DIR, "_replies.json")
OFFSET_F = os.path.join(QUEUE_DIR, "_offset.json")
ENV_PATH = os.path.expanduser("~/.config/jobbot/env")
API = "https://api.telegram.org/bot{token}/{method}"

PENDING, NEEDS_INPUT, NEEDS_REVISION = "pending", "needs_input", "needs_revision"
APPROVED, SKIPPED, SUBMITTED, FAILED = "approved", "skipped", "submitted", "failed"


# ---------------------------------------------------------------- config/queue

def load_creds():
    if not os.path.isfile(ENV_PATH):
        sys.exit(f"No credentials at {ENV_PATH}.\nRun: python jobs/telegram_setup.py")
    env = {}
    for line in open(ENV_PATH):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    tok, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        sys.exit(f"{ENV_PATH} missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
    return tok, chat


def sid_of(item_id):
    """Short id — Telegram callback_data is capped at 64 bytes."""
    return hashlib.md5(item_id.encode()).hexdigest()[:6]


def all_items():
    out = []
    for p in sorted(glob.glob(os.path.join(QUEUE_DIR, "*.json"))):
        if os.path.basename(p).startswith("_"):
            continue
        try:
            out.append(json.load(open(p)))
        except json.JSONDecodeError:
            print(f"  [warn] unreadable queue file: {p}")
    return out


def save_item(item):
    os.makedirs(QUEUE_DIR, exist_ok=True)
    with open(os.path.join(QUEUE_DIR, f"{item['id']}.json"), "w") as f:
        json.dump(item, f, indent=2)


def by_sid(sid):
    return next((i for i in all_items() if sid_of(i["id"]) == sid), None)


def load_replies():
    try:
        return json.load(open(REPLIES))
    except Exception:
        return {}


def save_replies(d):
    with open(REPLIES, "w") as f:
        json.dump(d, f)


def load_offset():
    try:
        return json.load(open(OFFSET_F)).get("offset")
    except Exception:
        return None


def save_offset(off, seen):
    with open(OFFSET_F, "w") as f:
        json.dump({"offset": off, "seen": sorted(seen)[-200:]}, f)


def load_seen():
    try:
        return set(json.load(open(OFFSET_F)).get("seen") or [])
    except Exception:
        return set()


def open_questions(item, required_only=True):
    qs = item.get("questions") or []
    return [q for q in qs
            if q.get("status") == "open" and (q.get("required") or not required_only)]


# ------------------------------------------------------------------- telegram

def call(token, method, **params):
    r = requests.post(API.format(token=token, method=method), json=params, timeout=60)
    return r.json()


def send_photo(token, chat, photo_path, caption, keyboard):
    with open(photo_path, "rb") as fh:
        r = requests.post(API.format(token=token, method="sendPhoto"),
                          data={"chat_id": chat, "caption": caption[:1024],
                                "parse_mode": "HTML",
                                "reply_markup": json.dumps(keyboard)},
                          files={"photo": fh}, timeout=120)
    return r.json()


# ---------------------------------------------------------------- card layout

def approval_kb(sid):
    return {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"a:{sid}"},
        {"text": "⏭ Skip", "callback_data": f"s:{sid}"},
        {"text": "📋 Fields", "callback_data": f"f:{sid}"},
    ]]}


def question_kb(sid, q):
    rows = []
    for i, opt in enumerate(q.get("options") or []):
        preview = opt if len(opt) <= 48 else opt[:45] + "..."
        rows.append([{"text": f"{i + 1}. {preview}", "callback_data": f"q:{sid}:{q['qid']}:{i}"}])
    rows.append([{"text": "✍️ Write my own", "callback_data": f"w:{sid}:{q['qid']}"}])
    return {"inline_keyboard": rows}


def question_text(item, q):
    n_open = len(open_questions(item, required_only=False))
    head = (f"❓ <b>{item.get('company','?')}</b> — {item.get('role','?')}\n"
            f"<i>Question ({n_open} left)</i>\n\n"
            f"<b>{q['label'][:320]}</b>\n")
    if q.get("feedback"):
        head += f"\n<i>Your note last time:</i> {q['feedback'][:200]}\n"
    if q.get("options"):
        head += "\nPick an answer, or write your own:\n\n"
        for i, opt in enumerate(q["options"]):
            head += f"<b>{i + 1}.</b> {opt}\n\n"
    else:
        head += "\n<i>No drafted options yet — tap “Write my own”.</i>"
    return head


def card_text(item):
    score = item.get("score")
    score_s = f"{score:.0f}%" if isinstance(score, (int, float)) else "n/a"
    lines = [f"<b>{item.get('role','(role)')}</b>",
             f"{item.get('company','(company)')} · {item.get('location') or 'location n/a'}",
             "",
             f"ATS match: <b>{score_s}</b>   ·   portal: {item.get('portal','?')}",
             f"asking: {item.get('salary_quoted','n/a')}"]
    answered = [q for q in (item.get("questions") or []) if q.get("status") == "answered"]
    if answered:
        lines.append("")
        for q in answered[:3]:
            lines.append(f"• {q['label'][:60]}…\n  ↳ {str(q.get('selected'))[:90]}")
    if item.get("warnings"):
        lines.append("")
        for w in item["warnings"][:3]:
            lines.append(f"⚠️ {w.splitlines()[0][:110]}")
    if item.get("url"):
        lines += ["", f'<a href="{item["url"]}">open posting</a>']
    return "\n".join(lines)


# ------------------------------------------------------------------- pushing

def push_next_question(token, chat, item):
    q = next(iter(open_questions(item, required_only=False)), None)
    if q is None:
        return False
    sid = sid_of(item["id"])
    r = call(token, "sendMessage", chat_id=chat, text=question_text(item, q),
             parse_mode="HTML", reply_markup=question_kb(sid, q),
             link_preview_options={"is_disabled": True})
    if r.get("ok"):
        print(f"  [ask] {item['id']} q{q['qid']}: {q['label'][:60]}")
    else:
        print(f"  [ask] FAILED: {r}")
    return True


def push_approval(token, chat, item):
    sid = sid_of(item["id"])
    shot = item.get("screenshot")
    if shot and not os.path.isabs(shot):
        shot = os.path.join(TOOL, shot)
    text, kb = card_text(item), approval_kb(sid)
    if shot and os.path.isfile(shot):
        r = send_photo(token, chat, shot, text, kb)
    else:
        r = call(token, "sendMessage", chat_id=chat, text=text, parse_mode="HTML",
                 reply_markup=kb, link_preview_options={"is_disabled": True})
    if r.get("ok"):
        item["message_id"] = r["result"]["message_id"]
        item["pushed_at"] = dt.datetime.now().isoformat(timespec="seconds")
        item["status"] = PENDING
        save_item(item)
        print(f"  [push] {item['id']} -> message {item['message_id']}")
        return True
    print(f"  [push] FAILED {item['id']}: {r}")
    return False


def push(token, chat, item):
    """Questions first; the approval card only once required ones are answered."""
    if open_questions(item, required_only=False):
        return push_next_question(token, chat, item)
    return push_approval(token, chat, item)


# ------------------------------------------------------------------ decisions

def resolve_decision(token, chat, item, decision):
    if item.get("status") in (APPROVED, SKIPPED, SUBMITTED):
        print(f"  [dup] {item['id']} already {item['status']} — ignoring")
        return
    if decision == APPROVED and open_questions(item):
        call(token, "sendMessage", chat_id=chat,
             text="⚠️ Still has an unanswered required question — answer it first.")
        return
    item["status"] = decision
    item["decided_at"] = dt.datetime.now().isoformat(timespec="seconds")
    save_item(item)
    mark = {APPROVED: "✅ APPROVED — submitting", SKIPPED: "⏭ SKIPPED"}[decision]
    mid = item.get("message_id")
    if mid:
        method = "editMessageCaption" if item.get("screenshot") else "editMessageText"
        key = "caption" if method == "editMessageCaption" else "text"
        call(token, method, chat_id=chat, message_id=mid,
             **{key: card_text(item) + f"\n\n<b>{mark}</b>"}, parse_mode="HTML")
    print(f"  [{decision}] {item['id']}")


def answer_question(token, chat, item, qid, text, source):
    qs = item.get("questions") or []
    q = next((x for x in qs if x["qid"] == qid), None)
    if q is None:
        return
    if q.get("status") == "answered":
        print(f"  [dup] {item['id']} q{qid} already answered — ignoring")
        return
    q["selected"] = text
    q["status"] = "answered"
    q["answered_via"] = source
    q["answered_at"] = dt.datetime.now().isoformat(timespec="seconds")
    item.setdefault("fields", {})[q["label"][:80]] = text
    save_item(item)
    try:
        from jobpilot.fill import replay as R
        R.apply_answers(item, {q["label"]: text})       # backfill replay.json now
    except Exception as e:
        print(f"  [warn] replay.json not updated: {e}")
    print(f"  [answered:{source}] {item['id']} q{qid} -> {text[:70]}")
    if open_questions(item, required_only=False):
        push_next_question(token, chat, item)
    else:
        call(token, "sendMessage", chat_id=chat,
             text="All questions answered — here is the application to approve.")
        push_approval(token, chat, item)


def request_free_text(token, chat, item, qid):
    """Ask for a typed answer, and remember which question the reply belongs to."""
    q = next((x for x in (item.get("questions") or []) if x["qid"] == qid), None)
    r = call(token, "sendMessage", chat_id=chat,
             text=f"✍️ Reply with your answer for:\n\n<b>{q['label'][:300]}</b>",
             parse_mode="HTML",
             reply_markup={"force_reply": True, "input_field_placeholder": "your answer"})
    if r.get("ok"):
        reps = load_replies()
        reps[str(r["result"]["message_id"])] = [sid_of(item["id"]), qid]
        save_replies(reps)


# ---------------------------------------------------------------------- serve

def serve(token, chat, duration=None):
    print(f"  [serve] polling{' for %ds' % duration if duration else ''} ... Ctrl-C to stop")
    deadline = time.time() + duration if duration else None
    offset = load_offset()
    seen = load_seen()
    pushed = {i["id"] for i in all_items() if i.get("message_id")}

    while deadline is None or time.time() < deadline:
        for item in all_items():
            if item.get("status") in (PENDING, NEEDS_INPUT) and item["id"] not in pushed:
                if push(token, chat, item):
                    pushed.add(item["id"])

        params = {"timeout": 10}
        if offset is not None:
            params["offset"] = offset
        try:
            r = call(token, "getUpdates", **params)
        except requests.RequestException as e:
            print(f"  [warn] poll failed ({e}); retrying")
            time.sleep(5)
            continue

        for upd in r.get("result", []):
            uid = upd["update_id"]
            offset = uid + 1
            save_offset(offset, seen)
            if uid in seen:
                continue                       # redelivered after a timeout
            seen.add(uid)
            save_offset(offset, seen)

            # typed answer to a force_reply prompt
            msg = upd.get("message")
            if msg and msg.get("reply_to_message") and msg.get("text"):
                reps = load_replies()
                key = str(msg["reply_to_message"]["message_id"])
                if key in reps:
                    sid, qid = reps.pop(key)
                    save_replies(reps)
                    item = by_sid(sid)
                    if item:
                        q = next((x for x in item["questions"] if x["qid"] == qid), None)
                        if q is not None:
                            q["feedback"] = msg["text"]
                        answer_question(token, chat, item, qid, msg["text"], "typed")
                continue

            cq = upd.get("callback_query")
            if not cq:
                continue
            parts = cq.get("data", "").split(":")
            action, sid = parts[0], (parts[1] if len(parts) > 1 else "")
            item = by_sid(sid)
            if item is None:
                call(token, "answerCallbackQuery", callback_query_id=cq["id"],
                     text="That item is no longer in the queue.")
                continue

            if action == "a":
                resolve_decision(token, chat, item, APPROVED); note = "Approved"
            elif action == "s":
                resolve_decision(token, chat, item, SKIPPED); note = "Skipped"
            elif action == "f":
                body = "\n".join(f"<b>{k}</b>: {v}" for k, v in
                                 (item.get("fields") or {}).items()) or "no field data"
                call(token, "sendMessage", chat_id=chat,
                     text=f"<b>Will be submitted:</b>\n\n{body}"[:4000],
                     parse_mode="HTML", reply_markup=approval_kb(sid))
                note = "Sent field list"
            elif action == "q":
                qid, oi = int(parts[2]), int(parts[3])
                q = next((x for x in item["questions"] if x["qid"] == qid), None)
                answer_question(token, chat, item, qid, q["options"][oi], "picked")
                note = f"Picked option {oi + 1}"
            elif action == "w":
                request_free_text(token, chat, item, int(parts[2]))
                note = "Reply with your answer"
            else:
                note = "Unknown action"
            call(token, "answerCallbackQuery", callback_query_id=cq["id"], text=note)
    print("  [serve] stopped")


# ---------------------------------------------------------------------- entry

def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--duration", type=int)
    ap.add_argument("--push", metavar="ID")
    ap.add_argument("--revise", metavar="ID", help="re-send questions after new options")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        items = all_items()
        if not items:
            print("  queue empty")
        for i in items:
            qs = i.get("questions") or []
            nopen = len([q for q in qs if q.get("status") == "open"])
            print(f"  {sid_of(i['id'])} {i['id']:<34} {i.get('status','?'):<13} "
                  f"{nopen}/{len(qs)} open  {i.get('company','?')}")
        return

    token, chat = load_creds()

    if args.push or args.revise:
        target = args.push or args.revise
        item = next((i for i in all_items() if i["id"] == target), None)
        if item is None:
            sys.exit(f"No queue item {target}")
        if args.revise:
            for q in item.get("questions") or []:
                if q.get("status") == "answered" and q.get("answered_via") == "typed":
                    q["status"] = "open"
            save_item(item)
        push(token, chat, item)
        return

    if args.serve:
        serve(token, chat, args.duration)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
