#!/usr/bin/env python3
"""
session.py — a LIVE exploration: the browser stays open, the walk is driven by
command, and nothing is ever walked twice.

A learning session (platform_learn) used to re-run the whole exploration after
every edit: page 1 again, the resume uploaded again, twenty controls filled
again, the whole log read again — to fix one control on page 4. Here the
walk pauses on the current page; the session asks what remains, edits its
code, asks for a retry of that page, and moves on when the page is complete.

    autofill --fill <slug> --session start    open the form, fill page 1, stay open
    autofill --fill <slug> --session status   page, what is filled, what REMAINS
    autofill --fill <slug> --session retry    reload hooks + platform module, fill the
                                              current page again (what was right stays)
    autofill --fill <slug> --session next     press Next
    autofill --fill <slug> --session finish   screenshot, replay.json, queue item, close
    autofill --fill <slug> --session abort    close without writing anything

Transport: one Unix socket per slug under data/.auto/, JSON lines, shared with
the live SUBMIT session (fill/submit_flow.py) — `kind` keeps the two apart.

The guard: a session a person starts keeps the browser-level no-submit guard
(walk.guard_exploration). A session a Claude learning run starts is launched
with --trust-agent and has NO guard: the agent recognises the Submit button
itself and stops there (its prompt says so, and the walker never presses
Submit on its own). A platform whose module brings its own driver (Workday)
has no pages to pause on; `start` runs that driver to its end and `finish` is
all that is left to do.
"""
import datetime as dt
import json
import os
import socket
import subprocess
import sys
import time

from jobpilot.core.paths import TOOL, DATA

SOCK_DIR = os.path.join(DATA, ".auto")
COMMANDS = ("start", "status", "retry", "next", "finish", "abort")


def sock_path(key, kind="explore"):
    return os.path.join(SOCK_DIR, f"{kind}-{key}.sock")


def log_path(key, kind="explore"):
    return os.path.join(SOCK_DIR, f"{kind}-{key}.log")


def serve_loop(sp, handle, idle_s, is_done):
    """Accept one JSON command per connection on `sp` until is_done() or idle.
    `handle(msg)` returns the reply dict. The socket file is removed on exit."""
    os.makedirs(os.path.dirname(sp), exist_ok=True)
    if os.path.exists(sp):
        os.remove(sp)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sp)
    srv.listen(1)
    srv.settimeout(idle_s)
    print(f"  [session] listening on {sp}", flush=True)
    try:
        while not is_done():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                print(f"  [session] idle for {idle_s}s — closing without writing", flush=True)
                break
            with conn:
                line = conn.makefile("r").readline()
                try:
                    msg = json.loads(line or "{}")
                except json.JSONDecodeError:
                    msg = {}
                try:
                    reply = handle(msg)
                except SystemExit as e:
                    reply = {"ok": False, "error": str(e), "closing": True}
                except Exception as e:
                    reply = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
                conn.sendall((json.dumps(reply, default=str) + "\n").encode())
                if reply.get("closing"):
                    break
    finally:
        srv.close()
        try:
            os.remove(sp)
        except OSError:
            pass


# ---------------------------------------------------------------- server

def serve(ctx, answers, resolve, pay, browser_factory=None, idle_s=1800, guard=True):
    """Run the exploration under command until finish/abort or `idle_s` of silence.
    guard=False only for a session a Claude learning run drives (--trust-agent)."""
    from playwright.sync_api import sync_playwright
    from jobpilot.fill import browser as B, walk, replay as R
    slug = ctx["company_slug"]
    item_id = f"{slug}-{dt.datetime.now():%m%d%H%M}"
    shot_rel = os.path.join("data", "queue", f"{item_id}.png")
    shot_abs = os.path.join(TOOL, shot_rel)
    rep = ctx["replay"] = R.Replay(explore=True)
    B.carry_recipes(ctx, rep)
    outcome = {"finished": False, "item": None}

    with sync_playwright() as pw:
        br = (browser_factory or B._browser)(pw)
        page = br.new_page()
        if guard:
            walk.guard_exploration(page)
        else:
            print("  [session] no browser guard: the agent decides where Submit is and stops there")
        print(f"  [session] opening {ctx['url']}")
        page.goto(ctx["url"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        know, custom = B.begin_exploration(page, ctx, rep)
        w, res = None, None
        # A platform that walks page by page (Workday's stepper) is driven like
        # the generic walker: stop on a page, fix, retry that page, go on.
        stepper = know.custom("walker")
        if stepper:
            w = stepper(page, ctx, answers, resolve, rep).start()
            w.fill()
        elif custom:
            res = custom(page, ctx, answers, resolve, rep)
            print(f"  [session] platform driver ran to {'the end' if res and res.get('reached_end') else 'a stop'} — only finish/abort apply")
        if res is None and w is None:
            w = walk.Walk(page, ctx, answers, resolve, rep).start()
            w.fill()

        def handle(msg):
            cmd = (msg.get("cmd") or "").strip()
            if cmd == "status":
                return {"ok": True, **(w.status() if w else {"reached_end": bool(res and res.get("reached_end")),
                                                            "driver": True, "warnings": (res or {}).get("warnings", [])[-8:]})}
            if cmd == "retry":
                if not w:
                    return {"ok": False, "error": "a platform driver ran this form; nothing to retry page by page"}
                know_now = w.reload_knowledge()
                st = w.retry() if hasattr(w, "retry") else w.fill()
                return {"ok": True, "knowledge": know_now, **st}
            if cmd == "next":
                if not w:
                    return {"ok": False, "error": "a platform driver ran this form; there is no page to press Next on"}
                ok, m = w.next()
                if ok:
                    w.fill()
                return {"ok": ok, "message": m, **w.status()}
            if cmd == "finish":
                r = w.finalize() if w else res
                custom_used = custom if not w else None
                filled, warnings, questions, placeholders, replay_rel, shot_ok = \
                    B.finish_exploration(ctx, rep, r, page, shot_abs, custom_used or (w if w and not isinstance(w, walk.Walk) else None), know, resolve=resolve)
                item = B.write_item(ctx, answers, pay, rep, know, item_id, shot_rel if shot_ok else None,
                                    filled, warnings, questions, placeholders, replay_rel)
                outcome.update(finished=True, item=item)
                return {"ok": True, "item": item["id"], "reached_end": rep.reached_end,
                        "replay": replay_rel, "questions": len(questions)}
            if cmd == "abort":
                outcome.update(finished=True)
                return {"ok": True, "aborted": True}
            return {"ok": False, "error": f"unknown command {cmd!r}"}

        try:
            serve_loop(sock_path(slug), handle, idle_s, lambda: outcome["finished"])
        finally:
            br.close()
    return outcome["item"]


# ---------------------------------------------------------------- client

def send(key, cmd, timeout=900, kind="explore", **args):
    sp = sock_path(key, kind)
    if not os.path.exists(sp):
        return {"ok": False, "error": f"no {kind} session for {key} — start one first"}
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(timeout)
    c.connect(sp)
    c.sendall((json.dumps({"cmd": cmd, **args}) + "\n").encode())
    data = c.makefile("r").readline()
    c.close()
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return {"ok": False, "error": "no reply"}


def start_detached(key, extra_args=(), wait_s=120, kind="explore", argv=None):
    """Spawn the server as its own process and wait for the socket. Returns the
    first status, so the caller sees where it stands straight away."""
    os.makedirs(SOCK_DIR, exist_ok=True)
    if os.path.exists(sock_path(key, kind)):
        return {"ok": False, "error": f"a {kind} session for {key} is already running — use status/finish/abort"}
    logf = open(log_path(key, kind), "a")
    argv = argv or ["--fill", key, "--no-learn", "--session", "serve", *extra_args]
    subprocess.Popen([sys.executable, "-m", "jobpilot.fill.autofill", *argv],
                     cwd=TOOL, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(wait_s * 2):
        if os.path.exists(sock_path(key, kind)):
            time.sleep(0.5)
            return send(key, "status", kind=kind)
        time.sleep(0.5)
    return {"ok": False, "error": f"{kind} session did not come up in {wait_s}s — see {log_path(key, kind)}"}


def show(reply):
    """Print a reply the way a person (or a session reading logs) wants it: the
    remaining work first, short."""
    if not reply.get("ok"):
        print(f"  [session] {reply.get('error') or reply.get('message') or 'failed'}")
        return
    if "item" in reply:
        print(f"  [session] finished -> {reply['item']} (reached_end={reply.get('reached_end')}, "
              f"{reply.get('questions')} question(s)); {reply.get('replay')}")
        return
    if reply.get("aborted"):
        print("  [session] aborted, nothing written")
        return
    if reply.get("driver"):
        print(f"  [session] platform driver: reached_end={reply.get('reached_end')}")
        for w in reply.get("warnings", []):
            print(f"    ! {w[:160]}")
        return
    print(f"  [session] page {reply.get('page')}{': ' + reply['heading'] if reply.get('heading') else ''} — "
          f"{len(reply.get('filled_on_page', []))} filled on this page, {reply.get('filled_total')} total; "
          f"{'LAST PAGE' if reply.get('reached_end') else 'next: ' + str((reply.get('next') or {}).get('text'))}"
          f"{' — STUCK' if reply.get('stuck') else ''}   [{reply.get('knowledge')}]")
    if reply.get("message"):
        print(f"    {reply['message']}")
    rem = reply.get("remaining") or []
    print(f"    remaining on this page: {len(rem)}")
    for m in rem:
        print(f"      - {m.get('label', '')[:70]!r} [{m.get('kind')}] {m.get('reason', '')[:80]}"
              + (f" options={m['options'][:5]}" if m.get("options") else ""))
    if reply.get("placeholders"):
        print(f"    placeholders so far: {len(reply['placeholders'])}")
