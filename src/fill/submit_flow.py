#!/usr/bin/env python3
"""
submit_flow.py — a LIVE submit session: the form replayed with the approved
values and held open on its last page, so a Claude session can find out why a
submit did not go through, fix it, and file it.

Submit is two parts (browser.submit_approved):
  1. replay   code only: the recorded route, the true values, the recorded
              Submit button. Most applications are filed here, no LLM.
  2. resolve  only when part 1 did not go through: this session, driven by a
              `claude -p` run (submit_resolve.py), by command:

    autofill --submit <id> --resolve-session status      page, errors the portal shows,
                                                          buttons, presses left, screenshot
    autofill --submit <id> --resolve-session refill      reload hooks + platform module,
                                                          fill the current page again
    autofill --submit <id> --resolve-session set --label L --value V
                                                          put an APPROVED value into the
                                                          control labelled L
    autofill --submit <id> --resolve-session click --button TEXT
                                                          press a non-submit, non-answer
                                                          button (close a dialog, Next)
    autofill --submit <id> --resolve-session press [--button TEXT]
                                                          press Submit (capped), verify
    autofill --submit <id> --resolve-session finish --note "what was wrong / what fixed it"
    autofill --submit <id> --resolve-session abort

The limits live here, in code, not in the prompt:
  - `set` accepts only a value that is already approved for this application
    (a filled field, an answered question, or the stored answers.yaml value the
    resolver gives for that label). Nothing new can be typed in.
  - `click` refuses anything that reads as Submit (that is `press`) and any
    Yes/No-style answer button (answers go through `set`/`refill`).
  - `press` is capped (fill.resolve_presses) and refuses once the portal shows a
    confirmation or says the application already exists — no duplicate filings.
  - "Submitted" is decided by the page (confirmation text, no submit button, no
    invalid field), never by the agent's reading of a screenshot.
"""
import datetime as dt
import json
import os
import re

from jobpilot.core.config import cfg
from jobpilot.core.paths import TOOL, DATA

ALREADY_RX = re.compile(r"already applied|already submitted|you('ve| have) (already )?applied|"
                        r"application (has been|was) (already )?received", re.I)
CHOICE_RX = re.compile(r"^(yes|no|n/a|true|false|prefer not to say|decline.*)$", re.I)


def approved_values(it, answers):
    """Every value this application may carry: filled fields, answered
    questions, and the scalar answers from answers.yaml."""
    vals = set()
    for v in (it.get("fields") or {}).values():
        if isinstance(v, str) and v.strip():
            vals.add(v.strip())
    for q in it.get("questions") or []:
        if q.get("status") == "answered" and (q.get("selected") or "").strip():
            vals.add(q["selected"].strip())

    def walk_yaml(o):
        if isinstance(o, dict):
            for x in o.values():
                walk_yaml(x)
        elif isinstance(o, list):
            for x in o:
                walk_yaml(x)
        elif isinstance(o, (str, int, float)) and str(o).strip():
            vals.add(str(o).strip())
    walk_yaml(answers)
    return vals


class SubmitFlow:
    def __init__(self, page, ctx, answers, resolve, rep, it, max_presses=None):
        from jobpilot.fill import browser as B
        self.B = B
        self.page, self.ctx, self.answers, self.resolve, self.rep, self.it = page, ctx, answers, resolve, rep, it
        self.max_presses = int(max_presses or cfg("fill.resolve_presses", 2))
        self.presses = 0
        self.target = None
        self.out = {}
        self.last = None            # last press result
        self.log = []               # what the agent did, in order — becomes the procedure's steps
        self.allowed = approved_values(it, answers)
        self.shot = os.path.join(DATA, "queue", f"{it['id']}-resolve.png")

    # ---- observing -------------------------------------------------------
    def _body(self):
        try:
            return (self.target.inner_text("body") or "")
        except Exception:
            return ""

    def confirmed(self):
        from jobpilot.fill import walk
        return walk._confirmed(self.target)

    def status(self, note=None):
        from jobpilot.fill import walk
        try:
            self.page.screenshot(path=self.shot, full_page=True)
            shot = self.shot
        except Exception:
            shot = None
        body = self._body()
        btns = walk.buttons(self.target)
        return {"ok": True,
                "reached_end": bool(self.out.get("reached_end")),
                "pages_done": self.out.get("pages_done"),
                "filled": len(self.out.get("filled") or {}),
                "confirmed": self.confirmed(),
                "already_applied": bool(ALREADY_RX.search(body[:4000])),
                "errors": walk.errors(self.target)[:12],
                "buttons": [b["text"] for b in btns][:25],
                "submit_button": self.rep.submit,
                "presses_used": self.presses, "presses_left": self.max_presses - self.presses,
                "last_press": self.last,
                "screenshot": shot,
                "message": note}

    # ---- acting ----------------------------------------------------------
    def open(self):
        from jobpilot.fill import walk
        self.target, self.out = walk.replay_to_submit(self.page, self.ctx, self.answers, self.resolve, self.rep)
        self.log.append(f"replayed {self.out.get('pages_done')} page(s), {len(self.out.get('filled') or {})} fields")
        return self.status()

    def refill(self):
        from jobpilot.fill import platforms
        platforms.reload()
        f, w, m = self.B.fill_fields(self.target, self.resolve, self.answers, self.ctx)
        self.out.setdefault("filled", {}).update(f)
        self.log.append(f"refill: {len(f)} field(s)")
        st = self.status(note=f"refilled {len(f)} field(s)")
        st["fill_warnings"] = w[:10]
        st["still_missing"] = [x.get("label") for x in m][:10]
        return st

    def set(self, label, value):
        """Put an approved value into one control, by label."""
        value = (value or "").strip()
        if not label or not value:
            return {"ok": False, "error": "set needs --label and --value"}
        if value not in self.allowed:
            return {"ok": False, "error": f"{value[:60]!r} is not an approved value for this application — "
                                          f"use one of the item's filled fields or answered questions"}
        want = self.B.norm(label)
        base = self.resolve

        def one(lbl):
            L = self.B.norm(lbl)
            if L == want or (len(want) > 12 and (want in L or L in want)):
                return {"yes": self.B.YES, "no": self.B.NO}.get(value.lower(), value)
            return base(lbl)
        f, w, m = self.B.fill_fields(self.target, one, self.answers, self.ctx)
        hit = [k for k in f if self.B.norm(k) == want or (len(want) > 12 and want in self.B.norm(k))]
        self.log.append(f"set {label[:50]!r} = {value[:40]!r} ({'took' if hit else 'not found'})")
        st = self.status(note=f"set {label[:50]!r}: {'took' if hit else 'no control with that label took it'}")
        st["fill_warnings"] = [x for x in w if want[:20] in self.B.norm(x)][:5]
        return st

    def click(self, text):
        from jobpilot.fill import walk
        if not text:
            return {"ok": False, "error": "click needs --button TEXT"}
        b = next((b for b in walk.buttons(self.target) if b["text"].strip().lower() == text.strip().lower()), None)
        if b is None:
            return {"ok": False, "error": f"no visible button reads {text!r}"}
        if walk.is_submit(b):
            return {"ok": False, "error": "that is a Submit button — use `press`, which is capped and verified"}
        if CHOICE_RX.match(b["text"].strip()):
            return {"ok": False, "error": "that is an answer button — answers go through `set` or `refill`"}
        ok = walk.click(self.target, walk.find_button(self.target, walk.descriptor(b)))
        self.page.wait_for_timeout(1200)
        self.log.append(f"clicked {text[:40]!r}")
        return self.status(note=f"clicked {text!r}: {'ok' if ok else 'did not click'}")

    def press(self, text=None):
        from jobpilot.fill import walk
        if self.confirmed() or ALREADY_RX.search(self._body()[:4000]):
            return {"ok": False, "error": "the portal already shows a confirmation / an existing application — "
                                          "do not press again; call finish"}
        if self.presses >= self.max_presses:
            return {"ok": False, "error": f"submit pressed {self.presses} time(s), the cap — call finish"}
        desc = self.rep.submit
        if text:
            b = next((b for b in walk.buttons(self.target) if b["text"].strip().lower() == text.strip().lower()), None)
            if b is None:
                return {"ok": False, "error": f"no visible button reads {text!r}"}
            desc = walk.descriptor(b)
        self.presses += 1
        clicked, ok, errs = walk.press_submit(self.page, self.target, self.it, desc, self.B)
        self.last = {"pressed": self.it.get("_pressed") or desc, "clicked": clicked, "submitted": ok, "invalid": errs}
        self.log.append(f"press #{self.presses} on {(desc or {}).get('text')!r}: "
                        f"{'SUBMITTED' if ok else 'not through'}")
        return self.status(note="submitted" if ok else "not through — read errors")

    def outcome(self):
        return bool((self.last or {}).get("submitted")) or self.confirmed()


def serve_submit(it, ctx, answers, resolve, browser_factory=None, idle_s=1800, max_presses=None):
    """The live submit session behind --resolve-session serve."""
    from playwright.sync_api import sync_playwright
    from jobpilot.fill import browser as B, replay as R, session as S, hooks
    rep = R.Replay.from_doc(R.load(it))
    ctx["replay"] = rep
    hooks_path = hooks.path_for(it["company_slug"])
    hooks_before = os.path.getmtime(hooks_path) if os.path.isfile(hooks_path) else None
    state = {"done": False}

    with sync_playwright() as pw:
        br = (browser_factory or B._browser)(pw)
        page = br.new_page()
        print(f"  [resolve] opening {it['url']}", flush=True)
        page.goto(it["url"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        flow = SubmitFlow(page, ctx, answers, resolve, rep, it, max_presses)
        flow.open()

        def handle(msg):
            cmd = (msg.get("cmd") or "").strip()
            if cmd == "status":
                return flow.status()
            if cmd == "refill":
                return flow.refill()
            if cmd == "set":
                return flow.set(msg.get("label"), msg.get("value"))
            if cmd == "click":
                return flow.click(msg.get("button"))
            if cmd == "press":
                return flow.press(msg.get("button"))
            if cmd in ("finish", "abort"):
                submitted = cmd == "finish" and flow.outcome()
                note = (msg.get("note") or "").strip()
                shot = os.path.join(DATA, "queue", f"{it['id']}-submitted.png")
                try:
                    page.screenshot(path=shot, full_page=True)
                    it["submit_screenshot"] = os.path.relpath(shot, TOOL)
                except Exception:
                    pass
                now = dt.datetime.now().isoformat(timespec="seconds")
                res = {"by": "claude", "at": now, "presses": flow.presses, "submitted": submitted,
                       "steps": flow.log, "note": note[:600]}
                it.setdefault("resolve", []).append(res)
                if submitted:
                    it["status"] = "submitted"
                    it["submitted_at"] = now
                    it["submitted_via"] = "claude-resolve"
                    changed = os.path.isfile(hooks_path) and os.path.getmtime(hooks_path) != hooks_before
                    R.record_submit_procedure(it, {
                        "resolved_by": "claude",
                        "pressed": (flow.last or {}).get("pressed") or rep.submit,
                        "verification_code": any("verification code entered" in w for w in it.get("warnings", [])),
                        "steps": flow.log + ([note] if note else []),
                        "code_written": [os.path.relpath(hooks_path, TOOL)] if changed else []})
                else:
                    it.setdefault("warnings", []).append(
                        f"claude resolve: not submitted after {flow.presses} press(es)" + (f" — {note[:200]}" if note else ""))
                it.pop("_pressed", None)
                with open(os.path.join(DATA, "queue", f"{it['id']}.json"), "w") as f:
                    json.dump(it, f, indent=2)
                state["done"] = True
                return {"ok": True, "closing": True, "submitted": submitted, "status": it.get("status")}
            return {"ok": False, "error": f"unknown command {cmd!r}"}

        try:
            S.serve_loop(S.sock_path(it["id"], "submit"), handle, idle_s, lambda: state["done"])
        finally:
            br.close()
    return it


def show(reply):
    """Short, the errors first — what a resolving agent needs to read."""
    if not reply.get("ok"):
        print(f"  [resolve] {reply.get('error') or 'failed'}")
        return
    if reply.get("closing"):
        print(f"  [resolve] closed — submitted={reply.get('submitted')} status={reply.get('status')}")
        return
    print(f"  [resolve] {'CONFIRMED' if reply.get('confirmed') else 'not confirmed'}"
          f"{' — ALREADY APPLIED' if reply.get('already_applied') else ''}; "
          f"last page={reply.get('reached_end')}, {reply.get('filled')} filled, "
          f"presses left {reply.get('presses_left')}"
          + (f" — {reply['message']}" if reply.get("message") else ""))
    for e in reply.get("errors") or []:
        print(f"    ! {(e.get('text') or e.get('label') or '')[:140]}")
    for k in ("fill_warnings", "still_missing"):
        for x in reply.get(k) or []:
            print(f"    - {k}: {str(x)[:140]}")
    print(f"    submit button: {reply.get('submit_button')}")
    print(f"    buttons: {reply.get('buttons')}")
    if reply.get("last_press"):
        print(f"    last press: {reply['last_press']}")
    print(f"    screenshot: {reply.get('screenshot')}")
