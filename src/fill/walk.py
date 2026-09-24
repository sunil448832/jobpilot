#!/usr/bin/env python3
"""
walk.py — walk any application form, single page or multi-page, without knowing
the portal. The engine here knows only what a human sees: a form lives in some
frame, an "Apply" button may have to be pressed to reveal it, a "Next" button
leads to the following page, a "Submit" button ends it. Everything it observes
about THIS form — which frame, which buttons, how many pages, what each page is
called — is recorded in the application's replay artifact (replay.Replay), so
pass 2 follows the same route with the approved answers before it falls back to
looking again.

explore()   pass 1 — fill each page, note the way to the next, walk to the last
            page, record the submit button WITHOUT pressing it.
replay()    pass 2 — take the recorded route page by page, fill with true values,
            press the recorded submit button, verify.

What is already known — the application's own hooks (applications/<slug>/
hooks.py) first, then the platform module (fill/platforms) — is tried
before looking: the iframe the form lives in, the Apply / Next / Submit
buttons. What the walk observes is written back to the artifact, and for a
platform nobody has described yet, into a new platform module (platform_learn.py).
"""
import re

from jobpilot.core.config import cfg
from jobpilot.fill import hooks

# What a human reads on the button. Order matters in NEVER: anything that could
# send, sign in, or destroy is never treated as "next".
NEXT_RX = re.compile(r"^(next|continue|save and continue|save & continue|save and proceed|proceed|"
                     r"next step|next page|go to next step|continue to [\w ]{1,30}|review( application)?)"
                     r"\s*[→>»]?$", re.I)
# "Apply" is never in here: it is START_RX's word (a landing-page CTA that
# reveals the form). A real Greenhouse/Ashby/Lever submit button always says
# "Submit ..."; treating bare "Apply" as a submit candidate too made the code-
# entry step on Konux press the page's top "Apply" link instead of the form's
# own Submit button, once, with a real code already spent.
SUBMIT_RX = re.compile(r"^(submit(\s+(my\s+|your\s+)?application)?|submit now|send(\s+application)?|"
                       r"finish|complete(\s+application)?)\s*[→>»]?$", re.I)
START_RX = re.compile(r"^(apply(\s+now|\s+for\s+this\s+(job|position|role|opening)|\s+to\s+this\s+(job|position))?|"
                      r"start( your)? application|i'?m interested)\s*[→>»]?$", re.I)
NEVER_RX = re.compile(r"submit|apply|send|finish|complete|confirm|sign in|log in|create account|"
                      r"cancel|delete|withdraw|remove", re.I)
BUTTONS = 'button, [role="button"], input[type="submit"], input[type="button"], a[role="button"], a.button, a[class*="apply" i]'
CONFIRMED = ("thank you for applying", "application received", "application submitted",
             "we have received", "thanks for applying", "successfully submitted",
             "your application has been", "congratulations")


# Pass 1 must not be able to submit even by accident — including when a Claude
# session is learning a new platform. Installed with page.add_init_script, this
# swallows clicks on anything that reads like a submit button (capture phase,
# so it runs before the page's own handlers) and logs what it stopped.
NO_SUBMIT_GUARD_JS = r"""
(() => {
  const RX = /%s/i;
  const textOf = el => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
  document.addEventListener('click', e => {
    const b = e.target && e.target.closest && e.target.closest('button, [role="button"], input[type="submit"], input[type="button"], a[role="button"], a.button');
    if (!b) return;
    // A visible password box means this is a sign-in / create-account form, not
    // the application: its "Submit" signs him in. Blocking it kept Workday from
    // ever signing in (Berkadia), which then looked like an unverified account.
    if ([...document.querySelectorAll('input[type="password"]')].some(e => e.getClientRects().length > 0)) return;
    const t = textOf(b);
    // "Apply" opens the form on a landing page and sends it on a form page:
    // it is a submit button only when a form is rendered.
    const formy = [...document.querySelectorAll('input:not([type=hidden]),select,textarea')].filter(e => e.getClientRects().length > 0).length >= 3;
    if (t && t.length <= 48 && RX.test(t) && (formy || !/^apply/i.test(t))) {
      e.preventDefault(); e.stopImmediatePropagation();
      window.__jobbot_blocked = (window.__jobbot_blocked || []).concat([t]);
      console.warn('[jobbot] exploration: click on submit-looking button blocked: ' + t);
    }
  }, true);
})();
""" % SUBMIT_RX.pattern


def guard_exploration(page):
    """Arm the no-submit guard on every document this page loads."""
    page.add_init_script(NO_SUBMIT_GUARD_JS)


def blocked_clicks(frame):
    try:
        return frame.evaluate("() => window.__jobbot_blocked || []") or []
    except Exception:
        return []


# ------------------------------------------------------------------ observing

def buttons(frame):
    """Visible clickable things in the frame: text plus whatever identifies them.
    Tags each with data-jobbot-btn so a pick can be clicked by index."""
    try:
        return frame.evaluate("""(sel) => {
          const out = []; let i = 0;
          for (const b of document.querySelectorAll(sel)) {
            const r = b.getBoundingClientRect(), cs = getComputedStyle(b);
            if (!(r.width > 0 && r.height > 0) || cs.visibility === 'hidden' || cs.display === 'none') continue;
            if (b.closest('header, nav, [id*="cookie" i], [class*="cookie" i], [id*="consent" i], [class*="consent" i], [id*="onetrust" i]')) continue;
            const text = (b.innerText || b.value || b.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
            if (!text || text.length > 48) continue;
            b.setAttribute('data-jobbot-btn', String(i));
            out.push({i, text, id: b.id || '', name: b.getAttribute('name') || '',
                      auto: b.getAttribute('data-automation-id') || '', testid: b.getAttribute('data-testid') || '',
                      disabled: !!b.disabled || b.getAttribute('aria-disabled') === 'true'});
            i++;
          }
          return out;
        }""", BUTTONS) or []
    except Exception:
        return []


def is_next(b):
    return bool(NEXT_RX.match(b["text"])) and not NEVER_RX.search(b["text"]) and not b["disabled"]


def is_submit(b):
    return bool(SUBMIT_RX.match(b["text"]))


def is_start(b):
    return bool(START_RX.match(b["text"])) and not b["disabled"]


def descriptor(b):
    """What the artifact keeps about a button: enough to find it again."""
    return {k: b[k] for k in ("text", "id", "name", "auto", "testid") if b.get(k)}


def find_button(frame, d):
    """Locate a recorded button: identifying attributes first, exact text last.
    Returns a locator or None."""
    if not d:
        return None
    want = (d.get("text") or "").strip().lower()
    for attr, key in (("data-automation-id", "auto"), ("data-testid", "testid"), ("id", "id"), ("name", "name")):
        if d.get(key):
            loc = frame.locator(f'[{attr}="{d[key]}"]').first
            try:
                if loc.count() and loc.is_visible(timeout=800):
                    return loc
            except Exception:
                pass
    if want:
        try:
            loc = frame.locator(BUTTONS).filter(has_text=re.compile(rf"^\s*{re.escape(want)}\s*$", re.I)).first
            if loc.count() and loc.is_visible(timeout=800):
                return loc
        except Exception:
            pass
    return None


def click(frame, loc):
    try:
        loc.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    try:
        loc.click(timeout=5000)
        return True
    except Exception:
        try:
            loc.click(timeout=5000, force=True)
            return True
        except Exception:
            return False


def signature(frame):
    """What identifies a page to a reader: heading, active step, URL, controls."""
    try:
        return frame.evaluate("""() => {
          const h = document.querySelector('h1, h2, [role="heading"]');
          const a = document.querySelector('[aria-current="step"], [aria-current="page"], [aria-current="true"]');
          return {heading: ((h && h.innerText) || '').trim().split('\\n')[0].slice(0, 120),
                  step: ((a && a.innerText) || '').trim().split('\\n')[0].slice(0, 80),
                  url: location.href.split('#')[0].slice(0, 200),
                  controls: [...document.querySelectorAll('input:not([type=hidden]),select,textarea,button[aria-haspopup="listbox"]')]
                              .filter(e => e.type === 'file' || e.getClientRects().length > 0).length};
        }""") or {}
    except Exception:
        return {}


def advanced(before, after):
    keys = ("heading", "step", "url", "controls")
    return any((before or {}).get(k) != (after or {}).get(k) for k in keys)


def errors(frame):
    """Field-level validation the page is showing, as {label, kind, text}."""
    try:
        return frame.evaluate("""() => {
          const res = [], seen = new Set();
          const labelOf = el => {
            let t = '';
            if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) t = l.innerText; }
            if (!t) { const l = el.closest('label'); if (l) t = l.innerText; }
            if (!t) { const ref = el.getAttribute('aria-labelledby');
                      if (ref) t = ref.split(/\\s+/).map(id => ((document.getElementById(id) || {}).innerText || '')).join(' '); }
            if (!t) t = el.getAttribute('aria-label') || '';
            if (!t) { const ff = el.closest('fieldset, [class*="field" i], [data-automation-id^="formField-"]');
                      const l = ff && ff.querySelector('label, legend'); if (l) t = l.innerText; }
            return (t || '').split('\\n')[0].trim().slice(0, 160);
          };
          for (const el of document.querySelectorAll('[aria-invalid="true"]')) {
            const lab = labelOf(el); if (!lab || seen.has(lab)) continue; seen.add(lab);
            const isList = el.tagName === 'BUTTON' || el.tagName === 'SELECT' || el.getAttribute('aria-haspopup') === 'listbox';
            res.push({label: lab, kind: isList ? 'dropdown' : (el.type === 'radio' || el.type === 'checkbox' ? 'choice' : 'text')});
          }
          for (const e of document.querySelectorAll('[role="alert"], [class*="error" i]:not(input):not(select):not(textarea)')) {
            const r = e.getBoundingClientRect(); if (!(r.width > 0 && r.height > 0)) continue;
            const t = (e.innerText || '').trim(); if (!t || t.length > 200) continue;
            const ff = e.closest('fieldset, [class*="field" i], [data-automation-id^="formField-"]');
            const ctl = ff && ff.querySelector('input,button,textarea,select');
            const lab = ctl ? labelOf(ctl) : '';
            const key = lab || t; if (seen.has(key)) continue; seen.add(key);
            res.push({label: lab || t.replace(/^error:?\\s*/i, ''), kind: 'text', text: t});
          }
          return res;
        }""") or []
    except Exception:
        return []


def wait_parsed(page, frame, max_s=20):
    """An uploaded resume triggers the portal's own autofill, which rewrites
    fields mid-run. Wait for that to finish before touching anything."""
    for _ in range(max_s):
        try:
            body = (frame.inner_text("body") or "").lower()
        except Exception:
            return
        if "parsing your resume" not in body and "autofilling" not in body:
            return
        page.wait_for_timeout(1000)


def _has_form(frame):
    return (signature(frame).get("controls") or 0) >= 3


# ------------------------------------------------------------------ passes

def _open(page, rep, B, explore, know):
    """The frame with the form. When the landing page hides the form behind an
    Apply button, press it (recorded as `start`) and look again. `know` is what
    the application's hooks and the platform module already say (hooks first)."""
    pats = know.get("FRAME_PATTERNS", ()) or ()
    target, how = B.pick_form_frame(page, hint=None if explore else rep.frame, patterns=pats)
    print(f"  [form] {how}")
    if not _has_form(target):
        start = None
        for d in (rep.start if not explore else None, know.get("START")):
            start = find_button(target, d) if d else None
            if start is not None:
                rep.start = d
                break
        if start is None:
            b = next((b for b in buttons(target) if is_start(b)), None)
            if b:
                start = find_button(target, b)
                rep.start = descriptor(b)
        if start is not None and click(target, start):
            page.wait_for_timeout(2500)
            B.wait_dom_stable(page, max_s=10, quiet=2)
            target, how = B.pick_form_frame(page, hint=None if explore else rep.frame, patterns=pats)
            print(f"  [form] after '{rep.start['text']}': {how}")
    rep.frame = target.url if target is not page.main_frame else None
    return target


def _fill_page(page, target, ctx, answers, resolve, out, B, harvest):
    wait_parsed(page, target)
    f, w, m = B.fill_fields(target, resolve, answers, ctx)
    out["filled"].update(f)
    out["warnings"].extend(w)
    out["missing"].extend(m)
    if harvest:
        for q in B.harvest_questions(target, resolve, out["warnings"]):
            k = B.norm(q["label"])[:90]
            if k not in out["_seen"]:
                out["_seen"].add(k)
                out["questions"].append(q)


def _go_next(page, target, loc, text, n, out, B):
    """Press a next button; report whether the page changed and why not."""
    before = signature(target)
    if loc is None or not click(target, loc):
        out["warnings"].append(f"page {n}: could not press '{text}'")
        return target, False
    page.wait_for_timeout(1500)
    B.wait_dom_stable(target, max_s=12, quiet=2)
    if target.is_detached():
        target, _ = B.pick_form_frame(page)
    if advanced(before, signature(target)):
        return target, True
    errs = errors(target)
    out["warnings"].append(f"page {n}: '{text}' did not advance"
                           + (f" — {[e['label'] for e in errs][:5]}" if errs else ""))
    for e in errs:
        out["missing"].append({"label": e["label"], "required": True, "kind": e.get("kind", "text"),
                               "options": [], "reason": e.get("text") or "rejected on the way to the next page"})
    return target, False


def _new_out():
    return {"filled": {}, "warnings": [], "missing": [], "questions": [], "_seen": set(),
            "pages_done": 0, "reached_end": False}


class Walk:
    """One exploration, page by page, RESUMABLE: between calls the browser stays
    where it is. explore() drives it start-to-end in one go; session.py drives
    it by command (status / retry / next / finish), so a learning session that
    fixes one control on page 4 never walks pages 1-3 again."""

    def __init__(self, page, ctx, answers, resolve, rep, max_pages=None):
        from jobpilot.fill import browser as B
        self.B = B
        self.page, self.ctx, self.answers, self.resolve, self.rep = page, ctx, answers, resolve, rep
        self.max_pages = max_pages or cfg("browser.max_pages", 12)
        self.out = _new_out()
        self.know = hooks.knowledge(rep.platform, ctx.get("company_slug"))
        self.target = None
        self.n = 0                      # current page number, 1-based
        self.done = False               # the last page is showing
        self.stuck = False              # Next would not advance
        self.nxt = None                 # the Next button seen on the current page
        self.page_missing = {}          # n -> what the latest fill of page n could not do
        self.page_filled = {}           # n -> labels filled on page n

    # ---- knowledge can change under a session: hooks file, platform module
    def reload_knowledge(self):
        from jobpilot.fill import platforms
        platforms.reload()
        self.know = hooks.knowledge(self.rep.platform, self.ctx.get("company_slug"))
        return self.know.describe()

    def start(self):
        self.target = _open(self.page, self.rep, self.B, True, self.know)
        self.n = 1
        return self

    def fill(self):
        """Fill the current page (again, if asked again) and look at its buttons.
        A repeat replaces the page's record; what was already right stays."""
        n, target, rep, out = self.n, self.target, self.rep, self.out
        sig = signature(target)
        rep.step = f"page {n}" + (f": {sig['heading']}" if sig.get("heading") else "")
        wait_parsed(self.page, target)
        f, w, m = self.B.fill_fields(target, self.resolve, self.answers, self.ctx)
        out["filled"].update(f)
        self.page_filled[n] = sorted(f)
        self.page_missing[n] = m
        out["warnings"].extend(w)
        for q in self.B.harvest_questions(target, self.resolve, out["warnings"]):
            k = self.B.norm(q["label"])[:90]
            if k not in out["_seen"]:
                out["_seen"].add(k)
                out["questions"].append(q)
        out["pages_done"] = max(out["pages_done"], n)

        btns = buttons(target)
        nexts = self.know.get("NEXT") or []
        hinted_next = nexts[n - 1] if len(nexts) >= n else None
        nxt = (next((b for b in btns if hinted_next and is_next(b)
                     and b["text"].lower() == hinted_next.get("text", "").lower()), None)
               or next((b for b in btns if is_next(b)), None))
        hinted_sub = self.know.get("SUBMIT")
        # "Apply now" is a start button on a landing page and a submit button on
        # a form; the one that opened this form is never also its submit.
        sub = (next((b for b in btns if hinted_sub and b["text"].lower() == hinted_sub.get("text", "").lower()), None)
               or next((b for b in btns if is_submit(b) and descriptor(b) != rep.start), None))
        rec = {"n": n, **sig, "next": descriptor(nxt) if nxt else None,
               "submit": descriptor(sub) if sub else None}
        rep.pages = [p for p in rep.pages if p.get("n") != n] + [rec]
        rep.pages.sort(key=lambda p: p.get("n", 0))
        self.nxt = nxt
        if nxt is None:
            rep.submit = rec["submit"]
            self.done = True
            out["reached_end"] = True
            rep.reached_end = True
            if sub is None:
                out["warnings"].append(f"page {n}: neither a Next nor a Submit button found — "
                                       f"the last page may need something the walk did not see")
        return self.status()

    def next(self):
        """Press Next. Returns (advanced, message)."""
        if self.done:
            return False, "already on the last page"
        if self.nxt is None:
            return False, "no Next button on this page"
        if self.n >= self.max_pages:
            self.out["warnings"].append(f"stopped after {self.max_pages} pages without reaching the end")
            return False, f"page cap ({self.max_pages}) reached"
        before = len(self.out["missing"])
        self.target, ok = _go_next(self.page, self.target, find_button(self.target, self.nxt),
                                   self.nxt["text"], self.n, self.out, self.B)
        for m in self.out["missing"][before:]:
            m["page"] = self.n                      # a rejection belongs to the page it happened on
        if ok:
            self.stuck = False
            self.n += 1
            return True, f"on page {self.n}"
        self.stuck = True
        for p in self.rep.pages:
            if p.get("n") == self.n:
                p["stuck"] = True
        return False, self.out["warnings"][-1] if self.out["warnings"] else "did not advance"

    def remaining(self):
        """What the current page still needs, from the latest fill and the
        latest Next attempt — the only things a fix needs to look at."""
        n = self.n
        seen, out = set(), []
        for m in list(self.page_missing.get(n, [])) + [m for m in self.out["missing"] if m.get("page") == n]:
            k = self.B.norm(m.get("label", ""))[:90]
            if k and k not in seen:
                seen.add(k)
                out.append({k2: m[k2] for k2 in ("label", "kind", "options", "reason") if k2 in m})
        return out

    def status(self):
        rec = next((p for p in self.rep.pages if p.get("n") == self.n), {})
        return {"page": self.n, "heading": rec.get("heading"), "controls": rec.get("controls"),
                "filled_on_page": self.page_filled.get(self.n, []),
                "filled_total": len(self.out["filled"]),
                "placeholders": {k: v for k, v in self.rep.placeholders.items()},
                "remaining": self.remaining(),
                "next": rec.get("next"), "submit": rec.get("submit"),
                "stuck": self.stuck, "reached_end": self.done,
                "knowledge": self.know.describe(),
                "pages": [{"n": p.get("n"), "heading": p.get("heading"), "stuck": p.get("stuck", False)} for p in self.rep.pages]}

    def finalize(self):
        out = self.out
        out["missing"] = [m for ms in self.page_missing.values() for m in ms] + \
                         [m for m in out["missing"] if m.get("page") is not None]
        for i, q in enumerate(out["questions"]):
            q["qid"] = i
        out.pop("_seen", None)
        return out

    def run(self):
        """Start to end in one go: explore()."""
        self.start()
        while True:
            self.fill()
            if self.done:
                break
            ok, _ = self.next()
            if not ok:
                break
        return self.finalize()


def explore(page, ctx, answers, resolve, rep, max_pages=None):
    """PASS 1 for any portal. Never presses a submit button."""
    return Walk(page, ctx, answers, resolve, rep, max_pages).run()


def replay_to_submit(page, ctx, answers, resolve, rep):
    """PASS 2, part 1: the recorded route with the true values, page by page,
    stopping on the last page with the Submit button NOT pressed. Returns
    (target_frame, out); out["reached_end"] says whether the last page is showing."""
    from jobpilot.fill import browser as B
    out = _new_out()
    out.update({"clicked": False, "submit_ok": False, "errs": 0})
    know = hooks.knowledge(rep.platform, ctx.get("company_slug"))
    target = _open(page, rep, B, False, know)
    pages = rep.pages or [{"n": 1}]
    for i, rec in enumerate(pages):
        n = rec.get("n", i + 1)
        rep.step = f"page {n}" + (f": {rec['heading']}" if rec.get("heading") else "")
        _fill_page(page, target, ctx, answers, resolve, out, B, harvest=False)
        out["pages_done"] = n
        if i == len(pages) - 1:
            out["reached_end"] = True
            break
        loc = find_button(target, rec.get("next"))
        text = (rec.get("next") or {}).get("text", "next")
        if loc is None:                                   # the route changed: look again
            b = next((b for b in buttons(target) if is_next(b)), None)
            loc, text = (find_button(target, b), b["text"]) if b else (None, text)
        target, ok = _go_next(page, target, loc, text, n, out, B)
        if not ok:
            break
    out.pop("_seen", None)
    return target, out


def replay(page, ctx, answers, resolve, rep, it):
    """PASS 2 by code alone: replay_to_submit, then the recorded submit."""
    from jobpilot.fill import browser as B
    target, out = replay_to_submit(page, ctx, answers, resolve, rep)
    if not out["reached_end"]:
        return out
    page.wait_for_timeout(800)
    clicked, submit_ok, errs = press_submit(page, target, it, rep.submit, B)
    out.update({"clicked": clicked, "submit_ok": submit_ok, "errs": errs})
    return out


def press_submit(page, target, it, first, B):
    """Press the recorded submit button (else the first submit-looking one),
    then VERIFY: confirmation text, no submit button left, no invalid field.
    A clicked button is not a submission."""
    desc = first
    loc = find_button(target, desc)
    if loc is None:
        b = next((b for b in buttons(target) if is_submit(b)), None)
        desc = descriptor(b) if b else None
        loc = find_button(target, desc) if desc else None
    clicked = loc is not None and click(target, loc)
    it["_pressed"] = desc                         # what was pressed, for the submit procedure record
    # A portal may hold the submission behind an emailed code. Ask, type it in,
    # then press the SAME button `desc` names — never search fresh, which is
    # how a same-text decoy elsewhere on the page got clicked once.
    page.wait_for_timeout(2500)
    if B.enter_verification_code(page, target, it, submit_desc=desc):
        clicked = True
    # Submission can take a while — poll for the outcome instead of
    # screenshotting a page that is still mid-request.
    for _ in range(int(cfg("browser.submit_poll_s", 30))):
        page.wait_for_timeout(1000)
        if _confirmed(target):
            break
    still_there = any(is_submit(b) for b in buttons(target))
    shown = errors(target)
    errs = len([e for e in shown if e.get("kind") != "text" or e.get("label")])
    confirmed = _confirmed(target)
    if not confirmed and shown:
        # Say what the portal said, so a failed attempt explains itself.
        it.setdefault("warnings", []).append(
            "portal shows: " + "; ".join((e.get("text") or e.get("label") or "")[:80] for e in shown[:4]))
    return clicked, confirmed or (clicked and not still_there and errs == 0), errs


def _confirmed(target):
    try:
        body = (target.inner_text("body") or "").lower()
    except Exception:
        return False
    return any(w in body for w in CONFIRMED)
