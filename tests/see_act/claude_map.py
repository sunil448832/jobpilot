"""Claude does the SEE step: one page's accessibility snapshot + the stored facts in,
the page's controls as JSON out (map_prompt.md). One `claude -p` call per page,
one turn, no tools. Claude names controls and fact KEYS only — never a value.

The code then checks every entry against the live page: the role + name must
resolve to a control (Claude must have copied it exactly), and the fact key must
exist. Anything that fails the check is reported, not acted on.
"""
import json
import os
import re
import shutil
import subprocess
import time

PROMPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "map_prompt.md")
KINDS = {"text", "search-and-pick", "dropdown", "native-select", "radio-group", "yes-no-buttons", "checkbox", "file"}


def claude_bin():
    for c in (shutil.which("claude"), os.path.expanduser("~/.npm-global/bin/claude"),
              os.path.expanduser("~/.local/bin/claude"), "/usr/local/bin/claude"):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


ROLE = {"text": "textbox", "file": "button", "dropdown": "button", "search-and-pick": "combobox",
        "native-select": "combobox", "checkbox": "checkbox", "radio-group": "group", "yes-no-buttons": "group"}
GROUPED = ("radio-group", "yes-no-buttons")


def expand(name, kind, fact):
    """Claude's compact [name, kind, fact] -> the entry the code works with. Everything
    else follows from those three, so Claude never writes it (output tokens)."""
    name = name or ""
    return {"name": name, "kind": kind, "fact": fact or None, "role": ROLE.get(kind, ""),
            "group": name if kind in GROUPED else "",
            "question": re.sub(r"\s*\*?\s*(Select One)?\s*$", "", name).rstrip("*").strip()}


def compact(e):
    return [e["name"], e["kind"], e.get("fact")]


def snapshot(page, root="main, form, body"):
    return page.locator(root).first.aria_snapshot()


CORRECTION = """

Some entries of your map were wrong. Each with what the live page said:
{failures}
Reply with only the corrected entries (same format; none for a control that is
not really there): [["<name>", "<kind>", "<fact or null>"], ...]"""


def ask(step, snap, facts, model="sonnet", effort="low", log=print, failures=None):
    """Claude's map of one page as a list of entries.
    failures: [(entry or None, error)] — a correction round: only those entries
    (or, with None, the page's own complaint) go back, and only fixes come back."""
    cli = claude_bin()
    if not cli:
        raise RuntimeError("claude CLI not found — run with --offline")
    shown = {k: v for k, v in facts.items() if not k.startswith("file:")}
    text = open(PROMPT, encoding="utf-8").read()
    for k, v in {"step": step, "snapshot": snap,
                 "facts": "\n".join(f"{k}: {v}" for k, v in shown.items()) + "\nfile:resume: (the resume file)"}.items():
        text = text.replace("{" + k + "}", str(v))
    if failures:
        text += CORRECTION.replace("{failures}", "\n".join(
            (f"  {json.dumps(compact(e))}  -> {err}" if e else f"  (page) -> {err}") for e, err in failures))
    t0 = time.time()
    p = subprocess.run([cli, "-p", text, "--model", model, "--effort", effort, "--max-turns", "1",
                        "--output-format", "text"], capture_output=True, text=True, timeout=300,
                       cwd=os.path.dirname(PROMPT))
    out = (p.stdout or "").strip()
    m = re.search(r"\[.*\]", out, re.S)
    if not m:
        raise RuntimeError(f"no JSON from claude: {out[:300] or p.stderr[:300]}")
    entries = [expand(*(list(x) + [None] * 3)[:3]) for x in json.loads(m.group(0)) if isinstance(x, list)]
    log(f"    claude: {'corrected' if failures else 'mapped'} '{step}' in {time.time() - t0:.0f}s "
        f"(~{len(text) // 4} tokens in, ~{len(m.group(0)) // 4} out)")
    return entries


def check_map(page, entries, facts, locate):
    """Check every entry against the live page. Nothing is silently fixed: a wrong
    entry comes back as (entry, error), worded so Claude can correct it.
    Returns (good, failures)."""
    good, bad = [], []
    for e in entries:
        kind, name = e["kind"], e["name"]
        if kind not in KINDS:
            bad.append((e, f"unknown kind {kind!r}"))
            continue
        if e.get("fact") and e["fact"] not in facts:
            bad.append((e, f"fact {e['fact']!r} is not a listed key — use a listed key or null"))
            continue
        if kind in GROUPED:
            grp = page.get_by_role("group", name=name)
            if not grp.count():
                bad.append((e, f"no group named {name!r} in the snapshot"))
                continue
            radios, buttons = grp.first.get_by_role("radio").count(), grp.first.get_by_role("button").count()
            if kind == "radio-group" and not radios:
                bad.append((e, "that group holds buttons, not radios — kind yes-no-buttons"))
                continue
            if kind == "yes-no-buttons" and not buttons:
                bad.append((e, "that group holds radios, not buttons — kind radio-group"))
                continue
        else:
            try:
                locate(page, e["role"], name)
            except Exception:
                bad.append((e, f"no {e['role']} named exactly {name!r} — wrong kind, or name not copied exactly"))
                continue
        good.append(e)
    return good, bad


def merge(good, fixes):
    """The page's map after a correction: what was right, plus the fixes."""
    names = {f["name"] for f in fixes}
    return [e for e in good if e["name"] not in names] + fixes
