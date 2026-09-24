#!/usr/bin/env python3
"""
platform_learn.py — how the tool comes to know a form it could not walk.
(fill/learn.py is the other learner: answers, not platforms.)

Two layers of knowledge, both plain code, both consulted by every pass:

    fill/platforms/<id>.py            the PLATFORM — shared by every application on it
    applications/<slug>/hooks.py this APPLICATION only — refinements on top

autofill --fill decides after every exploration:

  unknown platform, walked to the last page on its own
      The generic walker was enough. What it observed — host, the iframe the
      form lived in, the Apply / Next / Submit buttons page by page — becomes
      the platform's module. No LLM.

  unknown platform, stuck
      A Claude session writes the platform module (platform-wide structure) and,
      only if this form needs more, the application's hooks, testing against
      the real form in a LIVE session (fill/session.py): the browser stays open,
      the walk pauses on the current page, and each retry touches only what
      remains — a fix on page 4 never walks pages 1-3 again.

  known platform, stuck
      A Claude session may write the application's hooks and nothing else. The
      platform module is shared by every other application and is not touched.

Either way the session runs with --trust-agent: there is no browser-level
submit guard, and Claude's own judgment decides which button is Submit and
stops there (its prompt says so; the walker never presses Submit on its own).
Afterwards the tool explores once more by itself, guard on again: the new code is kept only when that run verifiably reaches the last
page; otherwise it is parked (platforms/_failed/, or hooks.failed-*.py in
the application) for a look. Replay (pass 2) then uses platform module + hooks
+ the application's replay.json — code and data, never an LLM.
"""
import datetime as dt
import glob
import os
import re
import shutil
import subprocess
import sys

from jobpilot.core.config import cfg
from jobpilot.core.paths import TOOL, DATA
from jobpilot.fill import platforms, hooks
from jobpilot.fill import replay as R

DIR = os.path.dirname(os.path.abspath(platforms.__file__))   # the platform modules live there
FAILED_DIR = os.path.join(DIR, "_failed")


def module_path(platform_id):
    return os.path.join(DIR, re.sub(r"[^a-z0-9_]+", "_", platform_id.lower()) + ".py")


def platform_of(rep, ctx):
    """The platform an exploration was really on: the host that served the form
    frame when there was one (a company page embedding SmartRecruiters is a
    SmartRecruiters form), else the platform the link was filed under."""
    if rep.frame:
        return platforms.slug_for_host(platforms.host_of(rep.frame)), platforms.host_of(rep.frame)
    return (rep.platform or ctx["portal"]), platforms.host_of(ctx["url"])


def write_from_observation(rep, ctx, by="observation"):
    """A platform module from one successful walk. Returns its path."""
    pid, host = platform_of(rep, ctx)
    frame = re.sub(r"^https?://", "", (rep.frame or "").split("?")[0])[:80]
    nexts = [p.get("next") for p in rep.pages if p.get("next")]
    src = f'''"""{pid} — written by the tool from exploring
{ctx["url"]}
on {dt.date.today().isoformat()} ({by}). Route seen: {len(rep.pages)} page(s){", form in an iframe" if frame else ""}.
Refine by hand, or delete this file to make the platform unknown again."""
ID = {pid!r}
HOSTS = ({host!r},)
ROUTE = "mobile"                       # autofilled end to end; approved on the phone
FRAME_PATTERNS = {(frame,) if frame else ()!r}
START = {rep.start!r}
NEXT = {nexts!r}
SUBMIT = {rep.submit!r}
LEARNED = {{"from": {ctx["url"]!r}, "at": {dt.datetime.now().isoformat(timespec="seconds")!r}, "by": {by!r}}}
'''
    p = module_path(pid)
    open(p, "w", encoding="utf-8").write(src)
    platforms.reload()
    return p


# ---------------------------------------------------------------- the session

PROMPT = """A job application form could not be walked to its last page automatically.
Write the code that makes it work, test it against the real form, and stop when
the exploration reaches the last page. Everything you need is in this message.

Platform id: {pid}      Application: {slug}      Form URL: {url}

======================================================================
SITUATION
======================================================================
{situation}

======================================================================
WHAT HAPPENED ON THE LAST WALK
======================================================================
{observed}

Screenshot of where it stopped: {shot}

======================================================================
WHAT YOU MAY WRITE
======================================================================
{targets}

Both files use the same interface (everything optional):
{interface}

Precedence when the engine looks something up: application hooks, then the
platform module, then the generic walker.

======================================================================
THE ENGINE YOU BUILD ON (do not edit these files)
======================================================================
{tool}/src/fill/walk.py      the generic walker: buttons(frame) lists visible buttons,
                           find_button(frame, descriptor) locates one, click(frame, loc),
                           signature(frame) fingerprints a page, errors(frame) reads
                           validation, wait_parsed(page, frame) waits for a resume parse.
                           explore() records the route; START / NEXT / SUBMIT descriptors
                           ({{"text": ..., "id"/"name"/"auto"/"testid": ...}}) are tried
                           before it looks.
{tool}/src/fill/browser.py   fill_fields(frame, resolve, answers, ctx) fills every
                           labelled control on the current page (placeholders for
                           unknown required ones) and records recipes; pick_form_frame(page)
                           finds the frame holding the form.
{tool}/src/fill/platforms/workday.py   a full custom driver, for reference only.

Most cases need ONLY data: FRAME_PATTERNS, START, NEXT, SUBMIT, maybe form_url(url).
Write explore()/submit() only when the walker truly cannot cope (a wizard that
repaints, a form inside shadow DOM, a page the buttons do not describe). If you
write explore(), return the walker-shaped dict
({{"filled", "warnings", "missing", "questions", "pages_done", "reached_end"}}),
or None to let the walker run.

======================================================================
HOW TO TEST — a live session; the browser stays open, nothing is walked twice
======================================================================
  {python} -m jobpilot.fill.autofill --fill {slug} --session start --trust-agent
      opens the form, fills page 1, stays open; prints what REMAINS on that page
  {python} -m jobpilot.fill.autofill --fill {slug} --session status
      the same report again (page, filled, remaining, next button, reached_end)
  {python} -m jobpilot.fill.autofill --fill {slug} --session retry
      after you edit the code: reloads it and fills the current page again —
      what was already right stays; only the remaining controls are news
  {python} -m jobpilot.fill.autofill --fill {slug} --session next
      when the page is complete: presses Next and fills the new page
  {python} -m jobpilot.fill.autofill --fill {slug} --session finish
      when status says reached_end: screenshot, artifact, queue item, close
  {python} -m jobpilot.fill.autofill --fill {slug} --session abort
      close without writing anything (then start again if you must)

Work page by page: fix what remains, retry, next. Success is `status` with
reached_end true and nothing required remaining; then `finish`. Do not start
more than 3 sessions. Read only the session output — it lists exactly what is
left; the full log is at {log} if you truly need it.

======================================================================
RULES
======================================================================
- Never submit. This session has NO automatic submit guard: you are the guard.
  The button that sends the application (usually on the last page, reading
  "Submit", "Submit application", "Send", "Finish", "Complete" or similar — not
  "Next" or "Continue") must never be pressed. The walker itself never presses
  it; any code you write must not either. When `status` shows reached_end true,
  call `finish` — that is where exploration stops. If you are unsure whether a
  button submits, treat it as Submit and do not press it.
- Never sign in, create an account, or type credentials. If the platform demands
  an account before showing the form, write ROUTE = "desktop" with no
  START/NEXT/SUBMIT into the platform module (or, if you may not touch it, into
  the hooks) and say so.
- Answers are never yours to invent: the engine fills from resolve() and enters
  placeholders where it has nothing. What you write describes STRUCTURE — the
  platform's in the platform module, this one form's in the hooks — nothing
  about this person or this role.
- Create or edit only the files named above. Keep each under 300 lines.
- Reply with the single word DONE when the run reached the last page, or
  FAILED: <one line why> if it cannot be made to work.
"""

TARGETS = {
    "new": ("  {module}\n"
            "      the platform module — what is true of EVERY form on this platform.\n"
            "  {hooks}\n"
            "      this application's hooks — only what THIS form needs beyond the\n"
            "      platform module. Create it only if the platform module alone is not enough."),
    "refine": ("  {hooks}\n"
               "      this application's hooks — the ONLY file you may create or edit.\n"
               "  {module}\n"
               "      the platform module exists and is shared by other applications:\n"
               "      READ it, never edit it. Put what this form needs into the hooks."),
}
SITUATION = {
    "new": "This platform has no module yet: the tool walked the form with the generic engine alone.",
    "refine": ("The platform is known and its module was used, but this particular form still\n"
               "could not be walked to the end. Something about this form differs."),
}


def _observed(item):
    doc = R.load(item)
    lines = [f"pages walked: {len(doc.get('pages') or [])}, reached the last page: {doc.get('reached_end')}",
             f"knowledge used: {item.get('knowledge', '?')}",
             f"form frame: {doc.get('frame') or 'main page'}   start button: {doc.get('start')}"]
    for p in doc.get("pages") or []:
        lines.append(f"  page {p.get('n')}: heading={p.get('heading')!r} controls={p.get('controls')} "
                     f"next={p.get('next')} submit={p.get('submit')}{' STUCK' if p.get('stuck') else ''}")
    lines.append(f"fields filled: {len(item.get('fields') or {})}, questions: {len(item.get('questions') or [])}")
    lines.append("warnings:")
    lines += [f"  ! {w[:200]}" for w in (item.get("warnings") or [])[:25]]
    return "\n".join(lines)


def session(pid, item, ctx, mode):
    """One Claude session writes and tests the code for `mode` ("new" or
    "refine"). Returns True when the expected file exists afterwards; whether
    it works is the caller's to verify by exploring again."""
    from jobpilot.tailor.autotailor import claude_bin, llm_flags, turn_limit, AUTO_CWD, log
    cli = claude_bin()
    if not cli:
        log("platform learning: claude CLI not found")
        return False
    module, hk = module_path(pid), hooks.path_for(ctx["company_slug"])
    prompt = PROMPT.format(pid=pid, slug=ctx["company_slug"], url=ctx["url"],
                           situation=SITUATION[mode], observed=_observed(item),
                           shot=os.path.join(TOOL, item.get("screenshot") or "(none)"),
                           targets=TARGETS[mode].format(module=module, hooks=hk),
                           interface=platforms.__doc__.strip(), tool=TOOL, python=sys.executable,
                           log=os.path.join(DATA, ".auto", f"explore-{ctx['company_slug']}.log"))
    cmd = [cli, "-p", prompt, *llm_flags("platform"), *turn_limit(),
           "--add-dir", TOOL,
           "--allowedTools", f"Read,Edit,Write,Glob,Grep,Bash({sys.executable} -m jobpilot.fill.autofill:*)",
           "--output-format", "text"]
    os.makedirs(AUTO_CWD, exist_ok=True)
    log(f"platform learning ({mode}): '{pid}' / {ctx['company_slug']} — starting a session "
        f"(up to {cfg('fill.learn_timeout_s', 1800)}s)")
    try:
        p = subprocess.run(cmd, cwd=AUTO_CWD, capture_output=True, text=True,
                           timeout=cfg("fill.learn_timeout_s", 1800))
        tail = ((p.stdout or "") + (p.stderr or ""))[-300:].strip()
    except subprocess.TimeoutExpired:
        tail = "timed out"
    # A live session the agent left open (turn cap, time-out, or it simply did
    # not call finish) would sit holding the Chrome profile until its idle
    # time-out — as one did for a closed Palo Alto posting. Close it.
    from jobpilot.fill import session as S
    if os.path.exists(S.sock_path(ctx["company_slug"])):
        S.send(ctx["company_slug"], "abort")
        log("platform learning: closed the live session the agent left open")
    log(f"platform learning: session ended — {tail[-160:]!r}")
    platforms.reload()
    return os.path.isfile(hk if mode == "refine" else module)


# ---------------------------------------------------------------- verification

def _park_module(pid, why):
    p = module_path(pid)
    if not os.path.isfile(p):
        return None
    os.makedirs(FAILED_DIR, exist_ok=True)
    dst = os.path.join(FAILED_DIR, f"{os.path.basename(p)[:-3]}-{dt.datetime.now():%m%d%H%M}.py")
    shutil.move(p, dst)
    open(dst, "a", encoding="utf-8").write(f"\n# parked: {why}\n")
    platforms.reload()
    return dst


def _park_hooks(slug, why):
    p = hooks.path_for(slug)
    if not os.path.isfile(p):
        return None
    dst = p[:-3] + f".failed-{dt.datetime.now():%m%d%H%M}.py"
    shutil.move(p, dst)
    open(dst, "a", encoding="utf-8").write(f"\n# parked: {why}\n")
    return dst


def _drop_queue_items(slug, keep):
    """Queue items the learning runs created for this slug — they are trials."""
    for f in glob.glob(os.path.join(DATA, "queue", f"{slug}-*.json")):
        if f in keep:
            continue
        for x in (f, f[:-5] + ".png"):
            try:
                os.remove(x)
            except OSError:
                pass


def _verify(pid, mode, item, ctx, answers, resolve, pay, before):
    """The tool's own check: explore again, from scratch, with the new code.
    Keep it if the walk reaches the last page; park it if not."""
    from jobpilot.fill.browser import fill_application
    from jobpilot.tailor.autotailor import log
    _drop_queue_items(ctx["company_slug"], before)
    ctx["portal"] = pid
    try:
        new = fill_application(ctx, answers, resolve, pay, submit=False)
    except SystemExit as e:
        new = None
        log(f"platform learning: verification run: {e}")
    if new and new.get("reached_end"):
        if mode == "new":
            with open(module_path(pid), "a", encoding="utf-8") as f:
                f.write(f'\nLEARNED = {{"from": {ctx["url"]!r}, "at": {dt.datetime.now().isoformat(timespec="seconds")!r}, '
                        f'"by": "agent", "verified": True}}\n')
        # The first exploration's item is superseded by the verified one.
        for x in (os.path.join(DATA, "queue", item["id"] + ".json"), os.path.join(DATA, "queue", item["id"] + ".png")):
            try:
                os.remove(x)
            except OSError:
                pass
        log(f"platform learning ({mode}): verified — exploration reached the last page; code kept "
            f"({new.get('knowledge')})")
        return new
    why = "verification exploration did not reach the last page"
    parked = [x for x in (_park_module(pid, why) if mode == "new" else None, _park_hooks(ctx["company_slug"], why)) if x]
    _drop_queue_items(ctx["company_slug"], before)
    log(f"platform learning ({mode}): did not verify — parked {[os.path.relpath(x, TOOL) for x in parked]}; "
        f"the first exploration's item stands")
    return item


def _learning_on(ctx, what):
    from jobpilot.tailor.autotailor import log
    if cfg("fill.learn_platforms", True):
        return True
    log(f"{ctx['company_slug']}: {what}; learning is off (fill.learn_platforms)")
    return False


def after_exploration(item, ctx, answers, resolve, pay):
    """Called by autofill --fill when the platform had no module."""
    from jobpilot.tailor.autotailor import log
    rep = R.Replay.from_doc(R.load(item))
    rep.platform = rep.platform or ctx["portal"]
    pid = platform_of(rep, ctx)[0]
    if rep.reached_end:
        p = write_from_observation(rep, ctx)
        log(f"platform '{pid}': walked to the end first time — module written from observation: "
            f"{os.path.relpath(p, TOOL)}")
        return item
    if not _learning_on(ctx, f"unknown platform '{pid}' not walked to the end"):
        return item
    before = set(glob.glob(os.path.join(DATA, "queue", f"{ctx['company_slug']}-*.json")))
    if not session(pid, item, ctx, "new"):
        log(f"platform '{pid}': no module written")
        return item
    return _verify(pid, "new", item, ctx, answers, resolve, pay, before)


def refine_application(item, ctx, answers, resolve, pay):
    """Called by autofill --fill when a KNOWN platform's form was not walked to
    the end: only this application's hooks may change."""
    from jobpilot.tailor.autotailor import log
    pid = item["portal"]
    if not _learning_on(ctx, f"platform '{pid}' form not walked to the end"):
        return item
    before = set(glob.glob(os.path.join(DATA, "queue", f"{ctx['company_slug']}-*.json")))
    if not session(pid, item, ctx, "refine"):
        log(f"{ctx['company_slug']}: no hooks written")
        return item
    return _verify(pid, "refine", item, ctx, answers, resolve, pay, before)



# ---------------------------------------------------------------- every exploration

EXPLORE_PROMPT = """Walk one job application form to its last page, WITHOUT submitting, using the
tool's existing code. You drive a live browser session by command. Keep it short:
most forms need only `next` until the last page, then `finish`.

Application: {slug}      Company: {company}      Platform: {platform}
Form URL: {url}
Code already loaded (in this order): {knowledge}

======================================================================
WHERE IT STANDS — the session is open, signed in, page 1 filled
======================================================================
{first}

======================================================================
COMMANDS (run each with the Bash tool, timeout 600000 ms — a page can take a minute)
======================================================================
  {cmd} status    the current page: what it filled, what REMAINS, the next button
  {cmd} next      save this page and fill the next one
  {cmd} retry     after you change code: reload it and fill THIS page again (nothing
                  before it is redone)
  {cmd} finish    when status says reached_end (LAST PAGE): write the result, close
  {cmd} abort     close without writing anything

======================================================================
HOW TO WORK
======================================================================
1. If the page has nothing REMAINING, or only placeholders: `next`. Placeholders are
   expected — they become questions for the applicant. Do not try to answer them.
2. Repeat until reached_end, then `finish`. That is the whole job for most forms.
3. If `next` reports STUCK, run `retry` once first: fields that appear late are
   filled then (a required menu with no stored answer gets a placeholder).
4. Still STUCK on something the code should have handled — a control it could
   not find or fill, a button it missed, a wizard step it misreads: fix it in THIS
   APPLICATION'S HOOKS, then `retry`, then `next`. Reuse first: the hooks file below
   {seeded}. Read it before writing; change only what this form needs.
     this application's hooks (the only code you may write): {hooks}
     read-only — never edit:  {reuse}, and everything under {tool}/src
   The platform driver is deliberately generic: it does what every tenant of the
   platform shares. Anything THIS employer does differently — its wording, an extra
   panel, a field it renders its own way — is exactly what hooks are for:
   after_fill(page, step, flow) to add to a step, fill_step(page, step, flow) to
   take a step over. Workday's widgets are importable from
   jobpilot.fill.platforms.workday (pick_listbox, type_prompt, set_date_any).
   Hooks interface:
{interface}
5. replay.json ({replay}) is written by `finish` from the walk you just did, so
   fix hooks and `retry` rather than editing it. After `finish` you may correct its
   STRUCTURE (a recipe's id/name/label/strategy, the route's buttons) if the walk
   recorded something wrong — never a value.
6. If a page cannot be completed even with hooks (the account, a closed posting),
   stop: `finish` anyway and say why in one line. A field only the applicant can
   answer is NOT a reason to stop — it gets a placeholder and becomes a question.

RULES
- Never submit. There is no automatic guard in this session: you are the guard.
  The button that sends the application (Submit / Submit application / Send /
  Finish on the last page) must never be pressed, by you or by code you write.
- Never invent or change an answer; values come from the tool's resolver.
- Never sign in yourself or handle credentials — the tool already did.
- Reply with the single word DONE after `finish` (or FAILED: <why>).
"""


def _status_text(reply):
    import io, contextlib
    from jobpilot.fill import session as S
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        S.show(reply)
    return buf.getvalue().rstrip() or str(reply)[:1500]


def _snapshot_engine():
    """Contents of every file under src/: the platform driver and the engine are
    not the session's to change (its permissions already say so)."""
    out = {}
    for p in glob.glob(os.path.join(TOOL, "src", "**", "*.py"), recursive=True):
        try:
            out[p] = open(p, encoding="utf-8").read()
        except OSError:
            pass
    return out


def _restore_engine(snap):
    changed = []
    for p, text in snap.items():
        try:
            if open(p, encoding="utf-8").read() != text:
                open(p, "w", encoding="utf-8").write(text)
                changed.append(os.path.relpath(p, TOOL))
        except OSError:
            pass
    for p in glob.glob(os.path.join(TOOL, "src", "**", "*.py"), recursive=True):
        if p not in snap:
            os.remove(p)
            changed.append(os.path.relpath(p, TOOL) + " (new, removed)")
    return changed


def explore_with_agent(ctx, answers, resolve, pay):
    """Every exploration: the tool opens a live session (signed in, page 1
    filled), and a claude -p run walks it to the last page, fixing the company's
    hooks only where a page gets stuck. Returns the queue item written."""
    from jobpilot.tailor.autotailor import claude_bin, llm_flags, turn_limit, AUTO_CWD, log
    from jobpilot.fill import session as S, browser as B
    slug = ctx["company_slug"]
    cli = claude_bin()
    if not cli:
        log(f"explore: claude CLI not found — {slug} explored by code alone")
        return B.fill_application(ctx, answers, resolve, pay, submit=False)
    before = set(glob.glob(os.path.join(DATA, "queue", f"{slug}-*.json")))
    first = S.start_detached(slug, ["--trust-agent"], wait_s=int(cfg("fill.session_start_s", 600)))
    if not first.get("ok"):
        log(f"explore: session did not start ({first.get('error')}) — {slug} explored by code alone")
        return B.fill_application(ctx, answers, resolve, pay, submit=False)
    know = hooks.knowledge(ctx.get("portal"), slug)
    # Every application has its own hooks.py: seeded from the company's (another
    # application for the same employer), else a stub — Claude reuses before it writes.
    had = os.path.isfile(hooks.path_for(slug))
    _, frm = hooks.ensure(slug)
    R.ensure(slug, ctx.get("url"), ctx.get("portal"))
    seeded = ("already exists and is loaded" if had else
              f"was seeded from {frm} (same employer) and is loaded" if frm else
              "is a stub — nothing written for this company yet")
    know = hooks.knowledge(ctx.get("portal"), slug)
    reuse = [p for p in (module_path(ctx["portal"]) if platforms.get(ctx.get("portal")) else None,) if p]
    engine = _snapshot_engine()
    cmd = f"{sys.executable} -m jobpilot.fill.autofill --fill {slug} --session"
    prompt = EXPLORE_PROMPT.format(
        slug=slug, company=ctx.get("company"), platform=ctx.get("portal"), url=ctx["url"],
        knowledge=know.describe(), first=_status_text(first), cmd=cmd, hooks=hooks.path_for(slug),
        reuse=", ".join(reuse) or "the generic walker", seeded=seeded, tool=TOOL,
        replay=R.path_for(slug),
        interface="\n".join("      " + l for l in platforms.__doc__.strip().splitlines()[5:32]))
    appdir = os.path.dirname(hooks.path_for(slug))
    argv = [cli, "-p", prompt, *llm_flags("explore"), *turn_limit(),
            "--add-dir", TOOL,
            # writes only inside this application's folder (hooks, replay.json)
            "--allowedTools", f"Read,Glob,Grep,Edit(/{appdir}/**),Bash({sys.executable} -m jobpilot.fill.autofill:*)",
            "--output-format", "text"]
    os.makedirs(AUTO_CWD, exist_ok=True)
    log(f"explore: {slug} — claude session on the live form ({know.describe()})")
    try:
        p = subprocess.run(argv, cwd=AUTO_CWD, capture_output=True, text=True,
                           timeout=int(cfg("fill.explore_timeout_s", 2400)))
        tail = ((p.stdout or "") + (p.stderr or ""))[-160:].strip()
    except subprocess.TimeoutExpired:
        tail = "timed out"
    if os.path.exists(S.sock_path(slug)):
        # Turn cap, time-out, or no `finish`: keep what was walked.
        r = S.send(slug, "finish")
        log(f"explore: the tool finished the session the agent left open ({r.get('item')})")
    reverted = _restore_engine(engine)
    if reverted:
        log(f"explore: the session changed engine code — reverted: {reverted}")
    log(f"explore: session ended — {tail[-120:]!r}")
    new = sorted(set(glob.glob(os.path.join(DATA, "queue", f"{slug}-*.json"))) - before)
    if not new:
        return None
    import json as _json
    item = _json.load(open(new[-1]))
    if not platforms.get(item.get("portal")) and item.get("reached_end"):
        rep = R.Replay.from_doc(R.load(item))
        p = write_from_observation(rep, ctx)
        log(f"explore: new platform — module written from the walk: {os.path.relpath(p, TOOL)}")
    return item
