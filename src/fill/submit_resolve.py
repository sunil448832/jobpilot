#!/usr/bin/env python3
"""
submit_resolve.py — part 2 of submit: when the code-only replay did not get an
approved application through, one `claude -p` session finds out why, fixes
it, and files it, through the live submit session (submit_flow.py).

It runs only after a real failure, so a normal submit costs no tokens. What it
learns is kept: a structural fix goes into applications/<slug>/hooks.py
(the only file it may write), and how the filing finally went is recorded as
the application's submit_procedure in replay.json.
"""
import json
import os
import subprocess
import sys

from jobpilot.core.config import cfg
from jobpilot.core.paths import TOOL, DATA
from jobpilot.fill import hooks, session as S

PROMPT = """An approved job application did not go through when the tool submitted it.
Find out why, fix it, and submit it. Everything you need is in this message.

Company: {company}      Role: {role}      Application id: {id}
Form URL: {url}

======================================================================
WHAT HAPPENED ON THE AUTOMATIC ATTEMPT
======================================================================
{failure}

======================================================================
THE ONLY VALUES THIS APPLICATION MAY CARRY (approved by the applicant)
======================================================================
{values}

======================================================================
THE LIVE SESSION — the form is open, replayed with those values, on its last page
======================================================================
  {cmd} status
      what the page shows: errors, buttons, presses left, and a screenshot path.
      Read the screenshot file to see the page.
  {cmd} refill
      reload the code and fill the current page again
  {cmd} set --label "<field label>" --value "<approved value>"
      put one APPROVED value into the control with that label. Anything that is
      not in the list above is refused.
  {cmd} click --button "<button text>"
      press a button that is neither Submit nor an answer (close a dialog, dismiss
      a banner, Next). Submit buttons and Yes/No answer buttons are refused.
  {cmd} press [--button "<button text>"]
      press Submit and check the result. Capped at {presses} press(es). The
      portal may email a verification code; the tool asks the applicant for it.
  {cmd} finish --note "<one or two sentences: what was wrong, what fixed it>"
      ends the session. The page decides whether it counts as submitted.
  {cmd} abort
      ends the session without claiming anything.

======================================================================
CODE YOU MAY WRITE (only if the problem is in how the form is filled)
======================================================================
  {hooks}
      this application's own fill code — same interface as a platform module
      (START, NEXT, SUBMIT descriptors; form_url(); explore(); submit()).
      After editing it, run `refill`. Do not edit any other file.
      The interface:
{interface}

======================================================================
RULES
======================================================================
- Never invent, reword or guess a value. Only the approved values above may go
  into the form. If the portal needs something that is not there, finish with
  a note naming exactly what is missing — the applicant will answer it.
- Press Submit only when status shows no errors you can still fix. Never press
  it once status says CONFIRMED or ALREADY APPLIED.
- Never sign in, create an account, or type credentials.
- Work from `status` output and the screenshot; do not read other files except
  {hooks} and the platform module named in it.
- Always end with `finish` (or `abort`). Reply with the single word DONE.
"""


def _failure_text(it):
    sf = it.get("submit_fill") or {}
    lines = [f"reason: {it.get('fail_reason') or 'submit did not go through'}"]
    lines += [f"  ! {w[:200]}" for w in (it.get("warnings") or [])[-8:]]
    lines += [f"  fill: {w[:200]}" for w in (sf.get("warnings") or [])[:8]]
    for m in sf.get("missing") or []:
        lines.append(f"  missing: {m.get('label')!r} ({m.get('reason', '')[:80]})")
    if it.get("submit_screenshot"):
        lines.append(f"screenshot of the failed attempt: {os.path.join(TOOL, it['submit_screenshot'])}")
    return "\n".join(lines)


def _values_text(it):
    out = []
    for k, v in (it.get("fields") or {}).items():
        out.append(f"  {k[:70]}: {str(v)[:160]}")
    for q in it.get("questions") or []:
        if q.get("status") == "answered":
            out.append(f"  {q['label'][:70]}: {str(q.get('selected'))[:300]}")
    return "\n".join(out) or "  (none)"


def resolve(it):
    """Run part 2 for one approved item whose automatic submit failed. Returns
    the item as it stands afterwards (re-read from the queue)."""
    from jobpilot.tailor.autotailor import claude_bin, llm_flags, turn_limit, AUTO_CWD, log
    qfile = os.path.join(DATA, "queue", f"{it['id']}.json")
    cli = claude_bin()
    if not cli:
        log("submit resolve: claude CLI not found")
        return it
    # the live session: its own process, the persistent Chrome profile is free again
    first = S.start_detached(it["id"], kind="submit",
                             argv=["--submit", it["id"], "--resolve-session", "serve"])
    if not first.get("ok"):
        log(f"submit resolve: session did not start — {first.get('error')}")
        return it
    cmd = f"{sys.executable} -m jobpilot.fill.autofill --submit {it['id']} --resolve-session"
    from jobpilot.fill import platforms
    prompt = PROMPT.format(company=it["company"], role=it["role"], id=it["id"], url=it["url"],
                           failure=_failure_text(it), values=_values_text(it), cmd=cmd,
                           presses=cfg("fill.resolve_presses", 2),
                           hooks=hooks.path_for(it["company_slug"]),
                           interface="\n".join("      " + l for l in platforms.__doc__.strip().splitlines()[5:25]))
    argv = [cli, "-p", prompt, *llm_flags("submit"), *turn_limit(),
            "--add-dir", TOOL,
            "--allowedTools", f"Read,Glob,Grep,Edit(/{os.path.dirname(hooks.path_for(it['company_slug']))}/**),"
                              f"Bash({sys.executable} -m jobpilot.fill.autofill:*)",
            "--output-format", "text"]
    os.makedirs(AUTO_CWD, exist_ok=True)
    log(f"submit resolve: {it['id']} — starting a session (up to {cfg('fill.resolve_timeout_s', 1500)}s)")
    try:
        p = subprocess.run(argv, cwd=AUTO_CWD, capture_output=True, text=True,
                           timeout=cfg("fill.resolve_timeout_s", 1500))
        tail = ((p.stdout or "") + (p.stderr or ""))[-200:].strip()
    except subprocess.TimeoutExpired:
        tail = "timed out"
    # A session the agent left open closes without claiming anything.
    if os.path.exists(S.sock_path(it["id"], "submit")):
        S.send(it["id"], "abort", kind="submit", note="closed by the tool: the agent did not finish")
    log(f"submit resolve: session ended — {tail[-140:]!r}")
    try:
        return json.load(open(qfile))
    except (OSError, ValueError):
        return it
