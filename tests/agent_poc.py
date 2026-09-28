"""tests/agent_poc.py — POC: ONE Claude agent fills one application, driving the page
through tools. Explore only: it never presses Submit.

    python tests/agent_poc.py <slug> [--model=opus --effort=low] [--max-turns=150]

The agent is started once for the whole application. Its system prompt, sent once, holds
how to work, the row grammar (the map prompt's rules) and every fact of the applicant;
everything else comes back as observations from its tools:

    see(full)            the page as it is now: its name, whether it is the last page, the
                         page's error messages, the placeholders it holds, its CONTROLS (id,
                         HTML hints, what each shows, lists read whole) and — the first time
                         a page is seen, or with full — the SNAPSHOT with the ids in place
    act(rows)            [id, write|select, answer] rows, done on the page (act.act_rows);
                         what each did comes back
    press(id)            one page button (Apply, Apply Manually, Next ...): the page it lands
                         on, or what the page says when it did not move on. Refused on the
                         form's last page, and for a button that sends the application
    finish(outcome, note)  the end: "last-page" or "stuck"

The browser lives on one worker thread (Playwright's sync API cannot run inside the agent's
event loop); every tool call is handed to it. Written to tests/maps/<slug>/:
    agent-poc-<time>.system.txt   the system prompt, as sent
    agent-poc-<time>.log          the whole session: the agent's words, each tool call, each result
"""
import asyncio
import concurrent.futures as cf
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from claude_agent_sdk import (tool, create_sdk_mcp_server, ClaudeAgentOptions, ClaudeSDKClient,   # noqa: E402
                              AssistantMessage, UserMessage, ResultMessage, TextBlock, ToolUseBlock,
                              ToolResultBlock, ToolAnnotations)

from jobpilot.core.answers import load, load_learned, read_jd, detect_market          # noqa: E402
from jobpilot.core.paths import TOOL                                                  # noqa: E402
from jobpilot.tailor.autotailor import AUTO_CWD                                       # noqa: E402
from jobpilot.apply import platforms                                                  # noqa: E402
from jobpilot.apply.explore import browser as B, see as S, act as A, facts as F, record as R   # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maps")
PROMPT = os.path.join(TOOL, "src", "agents", "map", "prompt.md")

INTRO = """You fill ONE job application in a web browser, page by page, for the applicant whose
facts are below. You act on the page only through your tools:

  see(full)      the page as it is now: its name, whether it is the form's LAST PAGE, the
                 page's own error messages, the placeholders it holds, and its CONTROLS —
                 each with an id (c1, c2, ...), what its HTML is, what it shows now, and for
                 a list its choices, read whole. The first time you see a page (or with
                 full=true) also its SNAPSHOT, the page as a screen reader reads it, with
                 each id written into its control's line.
  act(rows)      do rows on the page — [id, write|select, answer] each, the grammar below.
                 Returns what each row did (what the control shows now, what a search
                 matched, a choice the list did not hold, an error) and then THE PAGE NOW:
                 its controls as they are after acting, with new ids — check it: every
                 control shows what it should, and a pick or an Add may have added new
                 fields to fill. Use the ids of the latest list you were given.
  press(id)      press one of the page's own buttons — Apply, Apply Manually, a blank-form
                 start, Next / Continue / Save and Continue. Returns the page you are on
                 afterwards, or what the page says when it did not move on.
  finish(outcome, note)   the end: outcome "last-page" once you are on the form's last page
                 (its review page) with every page before it saved; "stuck" when you
                 cannot go on (say why in note).

How to work, every page: see -> act on every control that needs something -> read THE PAGE
NOW that act returns: each control shows what it should? new fields appeared (a follow-up
question, an added block)? -> act again where needed -> only then press the page's Next ->
see the new page. Never press Next or finish while a control that needs an answer shows
nothing, unless you know why it cannot be filled.

Work authorization, right to work, sponsorship, relocation: always about the country of THIS
job (job.location, job.market in FACTS) — use that country's work_authorization facts, never
the applicant's home country's or another market's.
When Next is refused, see what the page says and fix those controls. On the job posting,
press its Apply; in a "how do you want to apply" choice, press the one that opens a blank
form to fill in by hand.

This session never sends the application: on the form's last page do not press Submit —
call finish("last-page", ...) once every control on it is filled. The last page is the one
see marks LAST PAGE, or — on a one-page form, or any page — a page whose way forward is a
button that sends the application (Submit, Submit application) rather than a Next. press
refuses a button that would send it.

A control no fact answers gets a placeholder (the question itself, or guess:) — the
applicant answers it later on the phone; carry on with the form. Never invent a value.

His history stays whole: an employment or education block is never removed, and one
block never takes another's values. When a list does not hold his entry (his school, his
degree, his field): the list's own "Other" / "Not listed" entry when it has one, with the
real name typed where the form asks for it; else guess:<the nearest entries> | <the
question> — never the name of a different school, employer or degree. A degree the list
words differently (M.Tech -> Masters) is option: only when the list's entry is the same
level; "Master of Science" for an M.Tech is not the same: guess: and ask. Only an optional
extra block you added (a certification) may be removed when its list cannot hold it.

Never infer who he is: pronouns, gender identity, ethnicity, disability, veteran status
come only from their own facts, never from another fact (gender does not give pronouns);
without one, the question itself, or the list's "prefer not to say" when the question
allows it. Optional consents (keep my CV on file, send me job alerts, marketing emails)
stay unticked: no row. A consent the form requires to apply (a privacy notice, "I confirm
the above") is ticked.

ROWS FOR act
"""


def rules():
    """The map prompt's rules for a row (its answer grammar and what to leave out), as the
    rules of act."""
    text = open(PROMPT, encoding="utf-8").read()
    start = text.index("Reply with one row per control")
    end = text.index("LAST ACT (below the CONTROLS)")
    body = text[start:end].rstrip()
    return body.replace("Reply with one row per control that needs something done: [id, kind, answer]",
                        "In act, one row per control that needs something done: [id, kind, answer]")


def system_prompt(facts, learned, approved=None):
    own = ""
    if approved:
        own = ("\n\nTHE APPLICANT'S OWN ANSWERS FOR THIS APPLICATION — he gave these for this very form: the\n"
               "control each answers takes it as its answer (its learned: key), before any other fact, and\n"
               "it is his answer, not a guess:\n" + "\n".join(f"- {k}: {q!r} -> {a!r}" for k, q, a in approved))
    return INTRO + rules() + own + "\n\nFACTS (key: value)\n" + F.for_prompt(facts, learned) + "\n"


# ---------------------------------------------------------------- the form, on its own thread

class Form:
    """The live application: one browser, one page, what the agent last saw. Every method
    runs on the browser's own thread."""

    def __init__(self, slug, answers, learned, log):
        self.slug, self.log = slug, log
        meta, jd = read_jd(slug)
        self.url = meta.get("Apply URL") or meta.get("Link")
        self.pid = R.load(slug).get("platform") or platforms.detect(self.url)[0]
        self.mod = platforms.get(self.pid)
        self.ctx = {"market": detect_market(meta.get("Location", ""), jd), "company": meta.get("Company", slug),
                    "location": meta.get("Location", ""), "company_slug": slug, "url": self.url}
        approved = {q: p["answer"] for q, p in (R.load(slug).get("placeholders") or {}).items()
                    if (p.get("answer") or "").strip()}
        base = len(learned or [])
        self.learned = list(learned or []) + [{"match": q, "answer": a} for q, a in approved.items()]
        self.approved = [(f"learned:{base + i}", q, a) for i, (q, a) in enumerate(approved.items())]
        self.resume = B.resume_path(answers, slug)
        self.facts = F.job_facts(answers, self.ctx, self.learned, self.resume)
        self.controls, self.step, self.seen = {}, None, set()
        self.placeholders, self.done = {}, None

    # the browser
    def open(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.br = B.open_browser(self.pw)
        self.page = self.br.new_page()
        url = self.mod.form_url(self.url) if self.mod and hasattr(self.mod, "form_url") else self.url
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        B.wait_quiet(self.page.main_frame, max_s=8)
        gone = B.dead_posting(self.page)
        return f"the posting is closed ({gone})" if gone else None

    def close(self):
        try:
            self.br.close()
            self.pw.stop()
        except Exception:
            pass

    def frame(self):
        return S.form_frame(self.page, getattr(self.mod, "FRAME_PATTERNS", ()))

    def drawn(self, wait_s=20, settle_s=4):
        t0, last, still = time.time(), None, 0
        frame = self.frame()
        while time.time() - t0 < wait_s:
            snap = S.snapshot(frame)
            if sum(1 for c in S.parse(snap) if c.role in S.FORM_ROLES) >= 2:
                break
            still = still + 1 if snap == last else 0
            if still >= 6 and time.time() - t0 >= settle_s:
                break
            last = snap
            self.page.wait_for_timeout(250)
            frame = self.frame()
        B.wait_quiet(frame, max_s=4)
        return frame

    def step_name(self, frame):
        if self.mod and hasattr(self.mod, "step"):
            s = self.mod.step(self.page)
            if s:
                return s
        try:
            return (frame.get_by_role("heading").first.inner_text(timeout=1500) or "").strip()[:60] or "page"
        except Exception:
            return "page"

    def is_last(self, step):
        return bool(self.mod and hasattr(self.mod, "is_last") and self.mod.is_last(step))

    def pass_gate(self):
        """A platform's account gate (Workday's sign-in / create account) is passed by code,
        with credentials the agent never sees. True when one was passed."""
        if not (self.mod and hasattr(self.mod, "at_gate") and self.mod.at_gate(self.page)):
            return False
        self.log("  [platform] account gate: signing in")
        ok = self.mod.start(self.page, self.ctx, self.log, lambda p: False)
        self.log(f"  [platform] account gate {'passed' if ok else 'NOT passed'}")
        return ok

    # the tools
    def see(self, full=False):
        self.pass_gate()
        frame = self.drawn()
        step = self.step_name(frame)
        if step != self.step:
            S.forget_lists()
        self.step = step
        fields = sum(1 for c in S.parse(S.snapshot(frame)) if c.role in S.FORM_ROLES)
        controls, _, text, snap = S.describe(frame, entries=[], open_lists=True, chrome=fields < 2)
        self.controls = {c.id: c for c in controls if c.id}
        first = step not in self.seen
        self.seen.add(step)
        errs = S.errors(frame)
        held = [f"- {p['field']} holds {p['used']!r} — asked as {q!r}" for q, p in self.placeholders.items()
                if p.get("page") == step]
        out = [f"PAGE: {step}" + ("   — the form's LAST PAGE: do not submit, call finish" if self.is_last(step) else ""),
               "PAGE ERRORS: " + ("; ".join(errs) if errs else "(none)"),
               "PLACEHOLDERS ON THIS PAGE (not the applicant's answers):\n" + ("\n".join(held) or "(none)"),
               "CONTROLS\n" + text]
        if first or full:
            out.append("SNAPSHOT\n" + snap)
        self.log(f"  see '{step}': {len(self.controls)} controls" + (" + snapshot" if first or full else ""))
        return "\n\n".join(out)

    def act(self, rows):
        if not self.controls:
            return "call see first: there are no ids yet"
        good = [r for r in rows if isinstance(r, list) and len(r) >= 3]
        unknown = [r[0] for r in good if r[0] not in self.controls]
        outcomes = A.act_rows(self.frame(), list(self.controls.values()), good, self.facts, self.resume,
                              log=lambda s: self.log("  " + s.strip()))
        lines = []
        for o in outcomes:
            p = o.get("placeholder")
            if p:
                self.placeholders[p["question"] or o.get("control", "")] = {"used": p["used"], "field": o.get("control", ""),
                                                                            "candidates": p.get("candidates") or [],
                                                                            "page": self.step}
            offered = o.get("offered")
            wanted, shown = o.get("wanted"), str(o.get("shown") or "")
            check = not o.get("ok") and not offered and not o.get("error") and wanted and shown.strip()
            detail = (("searched: " + "; ".join(f"{t!r} -> {S.compact(h)}" for t, h in offered.items()))
                      if isinstance(offered, dict) else
                      (f"{o.get('error')}; the list holds {S.compact(offered)}" if offered else
                       (f"set to {wanted!r}; the field now shows {shown[:200]!r} — the same entry (a short or "
                        "cut form of it)? if not, act on it again" if check else
                        (o.get("error") or f"shows {shown[:200]!r}"))))
            label = "ok" if o.get("ok") else "CHECK" if check else "NOT DONE"
            lines.append(f"{o.get('id')}  {label}  {o.get('control', '')}: {detail}"
                         + (f"   (a placeholder: {p['used']!r}, asked later)" if p else ""))
        if unknown:
            lines.append(f"ids not on the page (see again for the current ids): {unknown}")
        B.wait_quiet(self.frame(), max_s=3)
        frame = self.frame()
        controls, _, text, _ = S.describe(frame, entries=[], open_lists=True,
                                          chrome=sum(1 for c in S.parse(S.snapshot(frame)) if c.role in S.FORM_ROLES) < 2)
        self.controls = {c.id: c for c in controls if c.id}
        errs = S.errors(frame)
        return ("\n".join(lines) or "(no rows)") + "\n\nTHE PAGE NOW (new ids)\nPAGE ERRORS: " + \
            ("; ".join(errs) if errs else "(none)") + "\nCONTROLS\n" + text

    def press(self, cid):
        c = self.controls.get(cid)
        if c is None:
            return f"{cid!r} is not an id of your latest see"
        name = (c.name or "").lower()
        if self.is_last(self.step) or "submit" in name or "send application" in name:
            return "refused: this session never sends the application — call finish"
        before_step, before_snap = self.step, S.snapshot(self.frame())
        S.tap(A.locate(self.frame(), c))
        B.wait_quiet(self.page.main_frame, max_s=4)
        self.pass_gate()                                  # an account gate: passed by code, never by the agent
        t0 = time.time()
        while time.time() - t0 < 12:                     # the page moves on, or says why not
            B.wait_quiet(self.page.main_frame, max_s=3)
            frame = self.frame()
            step = self.step_name(frame)
            if step != before_step or S.snapshot(frame) != before_snap:
                break
            if S.errors(frame):
                break
        frame = self.drawn()
        step = self.step_name(frame)
        self.controls = {}
        if step != before_step:
            self.log(f"  press {c.ref!r}: moved to '{step}'")
            return f"moved on: now on page {step!r}" + (" — the form's LAST PAGE" if self.is_last(step) else "") + ". see it."
        errs = S.errors(frame)
        self.log(f"  press {c.ref!r}: stayed on '{step}' {errs[:3]}")
        return (f"still on page {step!r}. " + ("The page says: " + "; ".join(errs) if errs else
                "The page changed but shows no error message") + ". see it.")


# ---------------------------------------------------------------- the session

def text_of(result):
    return {"content": [{"type": "text", "text": result}]}


async def main():
    slug = next(a for a in sys.argv[1:] if not a.startswith("--"))
    flag = lambda k, d=None: next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{k}=")), d)
    model, effort, max_turns = flag("model", "opus"), flag("effort", "low"), int(flag("max-turns", "150"))
    stamp = dt.datetime.now().strftime("%m%d-%H%M")
    base = os.path.join(OUT, slug, f"agent-poc-{stamp}")
    os.makedirs(os.path.dirname(base), exist_ok=True)
    logf = open(base + ".log", "w", encoding="utf-8")
    t0 = time.time()

    def log(s):
        line = f"[{time.time() - t0:5.0f}s] {s}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    form = Form(slug, load("answers.yaml"), load_learned(), log)
    pool = cf.ThreadPoolExecutor(1)                           # the browser's own thread
    on_browser = lambda fn, *a: asyncio.get_running_loop().run_in_executor(pool, fn, *a)
    big = ToolAnnotations(maxResultSizeChars=300000)

    @tool("see", "The page as it is now (controls with ids; the snapshot the first time or with full).",
          {"full": bool}, annotations=big)
    async def see(args):
        return text_of(await on_browser(form.see, bool(args.get("full"))))

    @tool("act", "Do rows on the page: [id, write|select, answer] each, ids of your latest see.",
          {"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}},
           "required": ["rows"]}, annotations=big)
    async def act(args):
        return text_of(await on_browser(form.act, args.get("rows") or []))

    @tool("press", "Press one of the page's own buttons (Apply, Apply Manually, Next ...) by its id.", {"id": str})
    async def press(args):
        return text_of(await on_browser(form.press, args.get("id", "")))

    @tool("finish", "End the session: outcome 'last-page' or 'stuck', with a note.", {"outcome": str, "note": str})
    async def finish(args):
        form.done = (args.get("outcome"), args.get("note"))
        log(f"  finish: {form.done}")
        return text_of("finished.")

    server = create_sdk_mcp_server("form", tools=[see, act, press, finish])
    system = system_prompt(form.facts, form.learned, form.approved)
    open(base + ".system.txt", "w", encoding="utf-8").write(system)
    options = ClaudeAgentOptions(
        system_prompt=system, mcp_servers={"form": server}, tools=[],
        allowed_tools=["mcp__form__see", "mcp__form__act", "mcp__form__press", "mcp__form__finish"],
        permission_mode="bypassPermissions", setting_sources=[], model=model, effort=effort,
        max_turns=max_turns, cwd=AUTO_CWD)
    os.makedirs(AUTO_CWD, exist_ok=True)

    closed = await on_browser(form.open)
    if closed:
        log(closed)
        return
    log(f"agent session: {slug} ({form.pid}) model={model} effort={effort}")
    calls, result = 0, None
    try:
        async with ClaudeSDKClient(options) as client:
            await client.query("The browser shows the job posting. Fill the application: begin with see.")
            for attempt in range(2):
                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for b in msg.content:
                            if isinstance(b, TextBlock) and b.text.strip():
                                log("AGENT: " + b.text.strip().replace("\n", " ")[:600])
                            elif isinstance(b, ToolUseBlock):
                                calls += 1
                                logf.write(f"\n>>> {b.name} {json.dumps(b.input, ensure_ascii=False)}\n")
                    elif isinstance(msg, UserMessage):
                        for b in msg.content if isinstance(msg.content, list) else []:
                            if isinstance(b, ToolResultBlock):
                                body = b.content if isinstance(b.content, str) else \
                                    "\n".join(x.get("text", "") for x in (b.content or []) if isinstance(x, dict))
                                logf.write(f"<<< {body}\n")
                    elif isinstance(msg, ResultMessage):
                        result = msg
                if form.done:
                    break
                await client.query("You stopped without calling finish. Carry on from the page as it is: "
                                   "see it, and finish the form (or call finish with why you cannot).")
    finally:
        await on_browser(form.close)
        pool.shutdown()
    log(f"\nDONE: {form.done} | tool calls {calls} | {time.time() - t0:.0f}s"
        + (f" | turns {result.num_turns} | cost ${result.total_cost_usd or 0:.2f}" if result else ""))
    for q, p in form.placeholders.items():
        log(f"  placeholder: {q[:70]!r} = {p['used']!r} ({p['page']})")
    log(f"transcript: {base}.log")
    logf.close()


if __name__ == "__main__":
    asyncio.run(main())
