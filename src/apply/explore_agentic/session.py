"""explore_agentic/session.py — one Claude agent explores one application: started once, it
drives the live form through its tools (form.py) in any order until it finishes. It never
sends the application.

    python -m jobpilot.apply.explore_agentic.session <slug> [--model=opus] [--effort=low] [--max-turns=200] [--fresh]

    resuming        when the application has a record (calls.json) the agent gets the replay
                    tool and resume.md, and starts from it: it redoes the record page by page
                    and carries on where it stops; --fresh ignores the record. The record is
                    saved after every call, so a session stopped anywhere resumes from there.

    system prompt   sent once: prompt.md (how to work, the tools, the row grammar, the rules),
                    the applicant's own answers for this form, and every fact
    tools           see, act, options, search, clear, inspect, press, finish (+ replay when a
                    record exists; + submit in a filing, mode "submit": replay.py, with filing.md)
    the browser     on its own worker thread; every tool call is handed to it
    written         applications/<slug>/agentic.json      what the session left: outcome, note,
                                                           pages, filled values, placeholders, the
                                                           submit button
                    applications/<slug>/calls.json        every tool call of the agent, in order:
                                                           tool, arguments, page, the lasting
                                                           identity of each control it names, what
                                                           it did — what replay.py runs again
                    logs/sessions/<slug>/agentic-<time>.log   the whole session (review phase):
                                                           the agent's words, each call, each result
                    logs/sessions/<slug>/agentic-<time>.system.txt   the system prompt, as sent
"""
import asyncio
import concurrent.futures as cf
import datetime as dt
import json
import os
import sys
import time

from claude_agent_sdk import (tool, create_sdk_mcp_server, ClaudeAgentOptions, ClaudeSDKClient, AssistantMessage,
                              UserMessage, ResultMessage, TextBlock, ToolUseBlock, ToolResultBlock, ToolAnnotations)

from jobpilot.core.answers import load
from jobpilot.core.config import cfg
from jobpilot.core import cards as CD
from jobpilot.core.paths import LOGS, TOOL
from jobpilot.tailor.autotailor import AUTO_CWD
from jobpilot.apply.explore_agentic import facts as F, record as R
from jobpilot.apply.explore_agentic.form import Form
from jobpilot.apply.explore_agentic import calls as C

HERE = os.path.dirname(os.path.abspath(__file__))


def system_prompt(form):
    text = open(os.path.join(HERE, "prompt.md"), encoding="utf-8").read().rstrip()
    if form.redo is not None:                    # a former session's record: the replay tool
        text += "\n\n" + open(os.path.join(HERE, "resume.md"), encoding="utf-8").read().rstrip()
    if form.mode == "submit":
        text += "\n\n" + open(os.path.join(HERE, "filing.md"), encoding="utf-8").read().rstrip()
    if form.approved:
        text += ("\n\nTHE APPLICANT'S OWN ANSWERS FOR THIS APPLICATION — he gave these for this very form: the\n"
                 "control each answers takes it (its learned: key) before any other fact; it is his answer,\n"
                 "not a guess:\n" + "\n".join(f"- {k}: {q!r} -> {a!r}" for k, q, a in form.approved))
    return text + "\n\nFACTS (key: value)\n" + F.for_prompt(form.facts, form.asked, form.ctx.get("tenant", "")) + "\n"


def keep_placeholders(slug, form):
    """The session's placeholders into the application's record (explore.json), where the
    phone's answers are written (record.apply_answers) and the filing reads them: an answer he
    gave already stays; a placeholder no longer asked goes, unless he answered it."""
    doc = R.load(slug)
    old = doc.get("placeholders") or {}
    by_norm = {R.norm(q): p for q, p in old.items()}
    now = {}
    for q, p in form.placeholders.items():
        prev = by_norm.get(R.norm(q)) or {}
        now[q] = {**p, **{k: prev[k] for k in ("answer", "answered_at") if prev.get(k)}}
    for q, p in old.items():
        if (p.get("answer") or "").strip() and R.norm(q) not in {R.norm(x) for x in now}:
            now[q] = p
    for old in ("pages", "submit", "explored_at", "company"):     # the old engine's record: calls.json now
        doc.pop(old, None)
    doc.update(placeholders=now, platform=form.pid, reached_end=(form.done or ("",))[0] == "last-page")
    R.save(slug, doc)


def reply(text):
    return {"content": [{"type": "text", "text": text}]}


def build_tools(form, on_browser):
    """The agent's tools: each hands its work to the browser's thread."""
    big = ToolAnnotations(maxResultSizeChars=300000)

    async def run(name, args, fn, *a):
        try:
            return reply(await on_browser(form.call, name, args, fn, *a))
        except KeyError as e:
            return reply(str(e).strip("'\""))
        except Exception as e:
            return reply(f"error: {type(e).__name__}: {str(e).splitlines()[0][:300]}")

    @tool("see", "What the page shows now: scope page | section (name) | outline | ids; snapshot adds the page text with ids.",
          {"type": "object", "properties": {
              "scope": {"type": "string", "enum": ["page", "section", "outline", "ids"]},
              "name": {"type": "string"}, "ids": {"type": "array", "items": {"type": "string"}},
              "snapshot": {"type": "boolean"}}}, annotations=big)
    async def see(a):
        return await run("see", a, form.see, a.get("scope") or "page", a.get("name"), a.get("ids"), bool(a.get("snapshot")))

    @tool("act", "Do only these rows, [id, write|select, answer] each; reports what they did and what else changed.",
          {"type": "object", "properties": {
              "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
              "report": {"type": "string", "enum": ["changes", "page"]}}, "required": ["rows"]}, annotations=big)
    async def act(a):
        return await run("act", a, form.act, a.get("rows") or [], a.get("report") or "changes")

    @tool("options", "A list's entries, read whole (categories with their entries); nothing picked.", {"id": str},
          annotations=big)
    async def options(a):
        return await run("options", a, form.options, a.get("id", ""))

    @tool("search", "What a list or search box holds for each regex pattern; nothing picked.",
          {"type": "object", "properties": {"id": {"type": "string"},
                                            "patterns": {"type": "array", "items": {"type": "string"}}},
           "required": ["id", "patterns"]}, annotations=big)
    async def search(a):
        return await run("search", a, form.search, a.get("id", ""), a.get("patterns") or [])

    @tool("clear", "Empty one field: untick it, empty it, take its picks out.", {"id": str})
    async def clear(a):
        return await run("clear", a, form.clear, a.get("id", ""))

    @tool("inspect", "Read-only: the HTML of one control's field.", {"id": str}, annotations=big)
    async def inspect(a):
        return await run("inspect", a, form.inspect, a.get("id", ""))

    @tool("press", "Press one of the page's own buttons or links (Apply, Apply Manually, Next ...); never one that sends.",
          {"id": str})
    async def press(a):
        return await run("press", a, form.press, a.get("id", ""))

    @tool("finish", "End: outcome 'last-page' (with submit_id: the id of the button that would send the "
                    "application — never pressed here) or 'stuck', with a note.",
          {"type": "object", "properties": {"outcome": {"type": "string"}, "note": {"type": "string"},
                                            "submit_id": {"type": "string"}}, "required": ["outcome", "note"]})
    async def finish(a):
        return await run("finish", a, form.finish, a.get("outcome", ""), a.get("note", ""), a.get("submit_id"))

    tools = [see, act, options, search, clear, inspect, press, finish]
    if form.redo is not None:
        @tool("replay", "Redo a former session's recorded steps from the page shown now, page after page; "
                        "reports where it stopped and why.", {"type": "object", "properties": {}})
        async def replay(a):
            return await run("replay", a, lambda: form.redo.text(form.redo.forward()))
        tools.append(replay)
    if form.mode == "submit":
        @tool("submit", "FILING only: press the button that sends the application; reports what the portal did.",
              {"id": str})
        async def submit(a):
            return await run("submit", a, form.submit, a.get("id", ""))
        tools.append(submit)
    return tools


async def agent(form, on_browser, task, model, effort, max_turns, log, logf):
    """One agent session on the form, given `task`, until it calls finish (once reminded).
    Returns (tool calls, the ResultMessage)."""
    tools = build_tools(form, on_browser)
    server = create_sdk_mcp_server("form", tools=tools)
    options = ClaudeAgentOptions(
        system_prompt=system_prompt(form), mcp_servers={"form": server}, tools=[],
        allowed_tools=[f"mcp__form__{t.name}" for t in tools], permission_mode="bypassPermissions",
        setting_sources=[], model=model, effort=effort, max_turns=max_turns, cwd=AUTO_CWD)
    os.makedirs(AUTO_CWD, exist_ok=True)
    calls, result = 0, None
    form.done = None
    async with ClaudeSDKClient(options) as client:
        await client.query(task)
        for _ in range(2):
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
            await client.query("You stopped without calling finish. Carry on from the page as it is, "
                               "and finish (or call finish with why you cannot).")
    return calls, result


async def run(slug, model="opus", effort="low", max_turns=200, fresh=False):
    stamp = dt.datetime.now().strftime("%m%d-%H%M")
    base = os.path.join(LOGS, "sessions", slug, f"agentic-{stamp}")
    os.makedirs(os.path.dirname(base), exist_ok=True)
    logf = open(base + ".log", "w", encoding="utf-8")
    t0 = time.time()

    def log(s):
        line = f"[{time.time() - t0:5.0f}s] {s}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    form = Form(slug, load("answers.yaml"), log)
    rec, former = C.load(slug)
    if former and not fresh:                     # resumable: the replay tool redoes the former record
        form.redo = C.Redo(form, rec, former, C.approved_answers(slug, rec), True, log)
    else:
        former = []
    form.save_calls = lambda cs: C.save(slug, cs, former)
    pool = cf.ThreadPoolExecutor(1)                              # the browser's own thread
    on_browser = lambda fn, *a: asyncio.get_running_loop().run_in_executor(pool, fn, *a)
    open(base + ".system.txt", "w", encoding="utf-8").write(system_prompt(form))
    closed = await on_browser(form.open)
    calls, result, shot = 0, None, None
    if closed:
        log(closed)
        form.done = ("stuck", closed)
    else:
        log(f"agentic explore: {slug} ({form.pid}) model={model} effort={effort}")
        try:
            task = "The browser shows the job posting. Fill the application." + (
                " A former session's record exists: start with replay (see RESUMING)." if form.redo else "")
            calls, result = await agent(form, on_browser, task, model, effort, max_turns, log, logf)
            os.makedirs(CD.folder(slug), exist_ok=True)
            shot = os.path.relpath(os.path.join(CD.folder(slug), f"{slug}-{stamp}.png"), TOOL)
            try:                                          # the page it ended on, for the phone card
                await on_browser(lambda: form.page.screenshot(path=os.path.join(TOOL, shot), full_page=True))
            except Exception:
                shot = None
        finally:
            await on_browser(form.close)
    pool.shutdown()
    secs = time.time() - t0
    out = {"slug": slug, "platform": form.pid, "at": dt.datetime.now().isoformat(timespec="seconds"),
           "outcome": (form.done or ("stuck", "the session ended without finish"))[0],
           "note": (form.done or ("", "the session ended without finish"))[1],
           "pages": form.pages, "filled": form.filled, "placeholders": form.placeholders,
           "submit_control": form.submit_control or rec.get("submit_control"),
           "calls": os.path.relpath(C.calls_path(slug), TOOL),
           "tool_calls": calls, "seconds": round(secs), "model": model, "effort": effort,
           "turns": getattr(result, "num_turns", None), "cost_usd": getattr(result, "total_cost_usd", None),
           "transcript": os.path.relpath(base + ".log", TOOL), "screenshot": shot, "resume": form.resume}
    with open(os.path.join(os.path.dirname(R.path_for(slug)), "agentic.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    C.save(slug, form.calls, former)
    keep_placeholders(slug, form)
    log(f"\nDONE: {out['outcome']} | {out['note'][:300]} | tool calls {calls} | {secs:.0f}s"
        + (f" | turns {out['turns']} | cost ${out['cost_usd'] or 0:.2f}" if result else ""))
    for q, p in form.placeholders.items():
        log(f"  placeholder: {q[:70]!r} = {p['used']!r} ({p['page']})")
    logf.close()
    return out


def main():
    slug = next((a for a in sys.argv[1:] if not a.startswith("--")), None)
    if not slug:
        sys.exit(__doc__)
    flag = lambda k, d: next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{k}=")), d)
    asyncio.run(run(slug, flag("model", cfg("llm.form_agent.model", "opus")),
                    flag("effort", cfg("llm.form_agent.effort", "low")), int(flag("max-turns", "200")),
                    "--fresh" in sys.argv))


if __name__ == "__main__":
    main()
