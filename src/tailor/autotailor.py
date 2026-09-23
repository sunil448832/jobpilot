#!/usr/bin/env python3
"""
autotailor.py — tailor + build + fill the best new roles overnight, unattended.

Runs `claude -p` once per role to do the judgment work (which project leads, how
skills reorder), then builds the resume, adds only-true keywords, and fills the
form. It stops there: the result waits on the phone for approval. Nothing is ever
submitted by this script.

Three deliberate choices:

  * It runs from `jobs/.auto/` so its transcripts land in a SEPARATE Claude Code
    history bucket and never bury real sessions in `claude --resume`.
  * The spawned session is restricted to reading and editing files — no Bash. A
    tailoring step has no business running arbitrary commands at 3am.
  * Roles below the queue floor are skipped. There is no point spending a session
    tailoring something that would never be applied to.

Usage:
    python jobs/autotailor.py --limit 2
    python jobs/autotailor.py --limit 1 --dry-run
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)
from jobpilot.core.config import cfg  # noqa: E402
AUTO_CWD = os.path.join(DATA, ".auto")
DB = os.path.join(DATA, "state.db")

# Tools the spawned session may use. Deliberately no Bash: the job is to edit
# five text files, and anything else is out of scope for an unattended run.
ALLOWED_TOOLS = "Read,Edit,Write,Glob,Grep"

# Roles are prepared CONCURRENTLY (pipeline.tailor_workers). Everything per role
# is independent — its own applications/<slug>/, its own claude -p processes,
# its own DB row — except two things:
#   FILL_LOCK  the browser. One real Chrome profile cannot be driven by two
#              Playwright sessions at once, so filling stays one-at-a-time while
#              tailoring for the other roles carries on in the background.
#   LOG_LOCK   so interleaved lines from different roles stay whole.
FILL_LOCK = threading.Lock()
LOG_LOCK = threading.Lock()
MANUAL = []                    # apply-by-hand items created this run, for the Telegram note

PROMPT = """Tailor Sunil Kumar Sharma's resume for one specific job. It has already been
built untailored and scored against the JD; the score is below target, so this
session exists to raise it — honestly. Everything you need to know is in this
message; do not go looking for policy or the JD elsewhere.

======================================================================
OPERATING POLICY (the relevant sections; they outrank anything inferred from the JD)
======================================================================
{policy}

======================================================================
JOB DESCRIPTION
======================================================================
{jd}

======================================================================
SCORE REPORT — what the current resume misses against this JD
======================================================================
{report}

======================================================================
WHAT TO DO
======================================================================
The resume lives in {appdir}/sections/ — a per-application copy of the base
resume. Read these four files, then edit them in place, and nothing else:
  objective.tex   skills.tex   experience.tex   projects.tex
Read all four in one turn; make all your edits in one turn. Do not touch
_header, education, achievements, resume.tex, or anything outside sections/.
ats.md is generated from the .tex on build — never edit it.

Tailor ONLY by reordering, re-emphasising, and matching the JD's wording, using
things ALREADY TRUE in those files. In experience.tex you may reorder bullets and
reword a bullet into the JD's vocabulary for the same fact; never change an
employer, title, date or number, and never add a bullet describing work not
already there. Add a SAFE keyword only where the resume already states that
fact. Never add a NOT SAFE keyword. If the JD wants something he does not have,
leave it out and let the score be lower.

The objective may name only capabilities the resume's own bullets evidence. Do
not adopt the JD's description of the ROLE as a description of Sunil: no
"trusted advisor", "customer-facing consultant", "from discovery through
production", "partner with executives" and the like unless a resume bullet
already says he did that. Customer-facing work is claimable only in the concrete
form the resume shows (production systems delivered for Roche), never as
consulting or pre-sales scope. Matching keywords is never a reason to change
what he did.

The resume MUST stay at two pages. Rewording may not lengthen: when you put a
bullet into the JD's vocabulary, keep it the same length or shorter, and drop
filler rather than adding words. A round that overflows to a third page is
reverted automatically, so length is not a way to add keywords.

Keep every file valid LaTeX for the macros already used (\\skills{{}}, zitemize,
\\jobentry, \\subsection ... \\hfill \\href). When the edits are done, reply with
the single word DONE. Do not explain the edits.
"""

Q_PROMPT = """The application at {appdir}/ has been filled, but some form questions
could not be answered automatically. They are in this JSON file:

  {qfile}

Everything you need is in this message; do not go looking for policy or the JD
elsewhere, and do not read the resume files.

======================================================================
OPERATING POLICY (the relevant sections)
======================================================================
{policy}

======================================================================
JOB DESCRIPTION
======================================================================
{jd}

======================================================================
WHAT TO DO
======================================================================
Read the JSON file. For every question whose "options" list is EMPTY, write 2-3
complete, ready-to-send answers into its "options" array:
  - Each option must be a COMPLETE answer Sunil can tap and be done with — not a
    hint, not a template with blanks.
  - Each must be TRUE: draw only on facts in the policy above and these resume
    numbers: 20 ms median inference latency; 98% end-to-end success across 500
    concurrent sessions; 28 graded tasks, 86% exact-pass / 0.86 mean F1; 99%
    precision on a 1,000-person gallery; Qdrant index compressed 8x via TurboQuant
    4-bit at nDCG/Recall parity across 8 BEIR datasets; GRPO post-training of
    Qwen3-4B, pass rate 38.6% to 41.0% on 500 held-out problems; notice period
    2 months.
  - Vary them meaningfully — different projects or different emphasis, not
    reworded versions of the same answer.
  - For a multi-select question, each option is a complete combination.
  - A question about a personal fact only Sunil knows (have you interviewed here
    before, referrals, deadlines) gets ONE neutral option that states the most
    likely answer plainly, so he can tap or overwrite it.
  - If a question is a legal, compliance, sanctions or data-consent declaration,
    LEAVE its options empty. Those are Sunil's to answer.

Write the whole updated file back in ONE Write call — valid JSON, nothing changed
except the "options" arrays. Then reply with the single word DONE.
"""



def log(m):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  [autotailor] {m}"
    with LOG_LOCK:
        print(line, flush=True)
        try:
            with open(os.path.join(DATA, "daily.log"), "a") as f:
                f.write(line + "\n")
        except Exception:
            pass


def llm_flags(step):
    """--model/--effort for one pipeline step, from config.yaml `llm:`."""
    c = cfg(f"llm.{step}", {}) or {}
    out = []
    if c.get("model"):
        out += ["--model", str(c["model"])]
    if c.get("effort"):
        out += ["--effort", str(c["effort"])]
    return out


def claude_bin():
    # systemd user services get a minimal PATH, so an npm --prefix install in
    # the home directory is invisible to shutil.which(). Check the real locations.
    for c in (shutil.which("claude"),
              os.path.expanduser("~/.npm-global/bin/claude"),
              os.path.expanduser("~/.local/bin/claude"),
              os.path.expanduser("~/.local/share/npm/bin/claude"),
              os.path.expanduser("~/.claude/local/claude"),
              os.path.expanduser("~/node_modules/.bin/claude"),
              "/usr/local/bin/claude", "/usr/bin/claude"):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def slugify(s):
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", (s or "").lower())).strip("-")


def norm_url(u):
    """Same posting, different shapes: the queue stores the FORM url (Ashby appends
    /application) while the DB stores the posting url, so a raw compare misses."""
    u = (u or "").split("?")[0].split("#")[0].rstrip("/")
    u = re.sub(r"/(application|apply)$", "", u)
    return u.lower()


def already_handled():
    """Postings that already have a queue item, so a submitted role is never
    prepared a second time. Matching on folder slug alone is not enough — the
    folder name rarely matches the slug this script would generate."""
    import glob
    import json
    urls, titles = set(), set()
    for f in glob.glob(os.path.join(DATA, "queue", "*.json")):
        if os.path.basename(f).startswith("_"):
            continue
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if d.get("url"):
            urls.add(norm_url(d["url"]))
        if d.get("company") and d.get("role"):
            titles.add((d["company"].strip().lower(), d["role"].strip().lower()[:40]))
    return urls, titles


def _cokey(name):
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def quota_state():
    """{company_key: (used, max)} for portals that cap applications.

    Counts SUBMITTED queue items inside each quota's window. OpenAI's portal
    allows 6 applications per 180 days; the pipeline holds ~50 OpenAI roles, so
    without this it would spend every slot on whatever ranked highest that week.
    """
    quotas = cfg("apply.quotas", {}) or {}
    if not quotas:
        return {}
    now = dt.datetime.now()
    used = {}
    for f in glob.glob(os.path.join(DATA, "queue", "*.json")):
        if os.path.basename(f).startswith("_"):
            continue
        try:
            d = json.load(open(f))
        except (json.JSONDecodeError, OSError):
            continue
        if d.get("status") != "submitted":
            continue
        k = _cokey(d.get("company"))
        q = quotas.get(k)
        if not q:
            continue
        when = d.get("submitted_at") or d.get("created") or ""
        try:
            age = (now - dt.datetime.fromisoformat(when)).days
        except ValueError:
            age = 0          # unparseable date: count it, erring toward caution
        if age <= q.get("window_days", 180):
            used[k] = used.get(k, 0) + 1
    return {k: (used.get(k, 0), q.get("max", 0)) for k, q in quotas.items()}


def candidates(limit, floor, per_company=3, require_screen=None):
    """Best-scoring roles not yet acted on, sponsorship markets before the US.

    Requires a `keep` verdict from screen.py by default. That gate was missing:
    the filter was status + score only, so a role screen.py had never looked at
    was tailorable, and 45 of the 78 roles above the floor were unscreened —
    including the highest-scoring one. Screening exists precisely to catch what
    scoring cannot (sponsorship refusals, seniority walls, "must already be based
    in Dublin"), and each miss costs two Claude sessions on a dead role.

    Set pipeline.require_screen false to fall back to the old behaviour. The
    trade-off: if screening cannot run, the tailor queue goes empty rather than
    filling with unvetted roles — which is the safer of the two failures.
    """
    if require_screen is None:
        require_screen = cfg("pipeline.require_screen", True)
    handled, handled_titles = already_handled()
    w = float(cfg("pipeline.fit_weight", 0.5))
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT key, company, title, location, url, market, score, fit FROM jobs "
        "WHERE status='new' AND score >= ? AND url != '' "
        + ("AND screen LIKE 'keep%' " if require_screen else "")
        # The screener's fit score (0-100, resume vs the role's REAL requirements,
        # abstract ones and "one of" lists included) gates entry. Keyword scores
        # cannot read a JD like Adyen's (5 hard keywords in 87 terms); this can.
        + (f"AND fit >= {int(cfg('pipeline.fit_min', 45))} " if require_screen else "")
        # Blend: fit says how well he matches the role, rank says how worth
        # pursuing it is (market, sponsorship, pay). Non-US markets still come
        # first — that ordering is Sunil's stated priority, not a score.
        + "ORDER BY CASE market WHEN 'usa' THEN 1 ELSE 0 END, "
          f"(COALESCE(fit, 0) * {w} + score * {1 - w}) DESC LIMIT ?",
        (floor, max(limit * 8, 60))).fetchall()
    held = {_cokey(c) for c in (cfg("apply.hold_companies", []) or [])}
    quota = quota_state()
    out, seen, roles = [], {}, set()
    for k, co, title, loc, url, mk, sc, fit in rows:
        ck = _cokey(co)
        # One role, several cities = several rows. Two workers scaffolding the same
        # folder collided on Culture Amp; pick a role once.
        role = (ck, title.strip().lower()[:40])
        if role in roles:
            continue
        roles.add(role)
        if ck in held:
            continue                       # deliberately paused, see config.yaml
        u, mx = quota.get(ck, (0, 0))
        if mx and u + sum(1 for o in out if _cokey(o["company"]) == ck) >= mx:
            continue                       # portal application cap reached
        slug = slugify(f"{co}-{title}")[:44]
        if norm_url(url) in handled:
            continue
        if (co.strip().lower(), title.strip().lower()[:40]) in handled_titles:
            continue
        if os.path.isdir(os.path.join(APPLICATIONS, slug)):
            continue
        # Cap per company so one employer cannot own the queue — Anthropic and
        # OpenAI alone account for 38 of the 43 roles above the floor. A cap of 1
        # was throttling the whole pipeline down to ~5 candidates.
        if seen.get(co, 0) >= per_company:
            continue
        seen[co] = seen.get(co, 0) + 1
        out.append({"key": k, "company": co, "title": title, "location": loc,
                    "url": url, "market": mk, "score": sc, "fit": fit, "slug": slug,
                    "pick": round((fit or 0) * w + sc * (1 - w), 1)})
        if len(out) >= limit:
            break
    return out


def run(cmd, timeout=900, cwd=TOOL):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode == 0, (p.stdout or "") + (p.stderr or "")


def policy_sections(numbers):
    """The text of POLICY.md sections N (by their '## N.' headings), in order."""
    txt = open(POLICY, encoding="utf-8").read()
    parts = re.split(r"(?m)^(?=## \d+\.)", txt)
    want = {str(n) for n in numbers}
    out = [p.strip() for p in parts if re.match(r"## (\d+)\.", p) and re.match(r"## (\d+)\.", p).group(1) in want]
    return "\n\n".join(out)


def jd_text(company, limit=9000):
    p = os.path.join(APPLICATIONS, company, "JD.md")
    try:
        t = open(p, encoding="utf-8").read()
    except OSError:
        return "(JD.md missing)"
    return t if len(t) <= limit else t[:limit] + "\n[... JD truncated ...]"


def snapshot_sections(company):
    """Copy of sections/ before a tailoring round, so a bad round can be undone."""
    import shutil, tempfile
    src = os.path.join(APPLICATIONS, company, "sections")
    dst = tempfile.mkdtemp(prefix="sections-", dir=os.path.join(DATA, ".auto"))
    shutil.rmtree(dst); shutil.copytree(src, dst)
    return dst


def restore_sections(company, snap):
    import shutil
    dst = os.path.join(APPLICATIONS, company, "sections")
    shutil.rmtree(dst); shutil.copytree(snap, dst); shutil.rmtree(snap, ignore_errors=True)


def pdf_pages(company):
    p = subprocess.run(["pdfinfo", os.path.join(APPLICATIONS, company, "sunil_resume.pdf")],
                       capture_output=True, text=True)
    m = re.search(r"Pages:\s+(\d+)", p.stdout or "")
    return int(m.group(1)) if m else 0


def new_claims(company):
    """Words in the tailored objective/skills/experience/projects that are not in
    the base resume. Mechanical, no LLM: a cheap tripwire for JD language creeping
    in as a claim. Logged, and written to notes.md so it shows at review."""
    import collections, re as _re, glob as _glob
    tok = lambda t: collections.Counter(
        x.lower().strip(".,;:") for x in _re.findall(r"[A-Za-z][A-Za-z0-9+#/.-]*", _re.sub(r"\\[a-zA-Z]+|[{}]", " ", t)) if len(x.strip(".,;:")) > 3)
    base, tail = collections.Counter(), collections.Counter()
    for f in ("objective", "skills", "experience", "projects"):
        try:
            base += tok(open(os.path.join(RESUME, "sections", f + ".tex"), encoding="utf-8").read())
            tail += tok(open(os.path.join(APPLICATIONS, company, "sections", f + ".tex"), encoding="utf-8").read())
        except OSError:
            pass
    STOP = {"with", "and", "the", "for", "through", "from", "into", "that", "this", "using", "across", "while"}
    return sorted(w for w in (tail - base) if w not in STOP)


def ats_score(company):
    """The scorer's JSON for an application (built ats.md vs JD.md)."""
    p = subprocess.run([sys.executable, "-m", "jobpilot.tailor.ats_score",
                        "--company", company, "--json"],
                       cwd=APPLICATIONS, capture_output=True, text=True, timeout=120)
    try:
        return json.loads(p.stdout[p.stdout.index("{"):])
    except (ValueError, json.JSONDecodeError):
        return {"match_rate": 0.0, "stuffing_penalty": 0.0, "missing": [], "matched": []}


def technical_missing(r):
    """The scorer's missing keywords, restricted to skill categories. The 'other'
    bucket is JD boilerplate and role language ('range', 'salary', 'collaboration',
    'customers and engineering teams') — chasing it is what produced the
    'technical advisor to customers' sentence."""
    out = []
    for m in r.get("missing", []):
        term = m[0] if isinstance(m, (list, tuple)) else m
        cat = m[1] if isinstance(m, (list, tuple)) and len(m) > 1 else "other"
        if cat in ("hard", "soft"):
            out.append(term)
    return out


def safe_unsafe(company):
    """optimize.py's split of the missing SKILL keywords: SAFE (same fact already on
    the resume, worded the JD's way) vs NOT SAFE (a new claim)."""
    try:
        from jobpilot.tailor import optimize
        _, _, _, _, safe, unsafe = optimize.analyse(company)
    except SystemExit:
        return [], []
    return ([t for t, c, w in safe if c in ("hard", "soft")],
            [t for t, c, w in unsafe if c in ("hard", "soft")])


def score_report(company, r):
    """Missing SKILL keywords, split into SAFE / NOT SAFE, for the prompt."""
    lines = [f"ATS match {r['match_rate']}% | title present: {r.get('title_present')} | "
             f"matched {r['counts']['matched']} / missing {r['counts']['missing']} of "
             f"{r['counts']['jd_keywords']} JD keywords"]
    safe, unsafe = safe_unsafe(company)
    if safe:
        lines.append("SAFE to add (already true, word it the JD's way): " + ", ".join(safe[:20]))
    if unsafe:
        lines.append("NOT SAFE (would be a new claim — do NOT add): " + ", ".join(unsafe[:20]))
    tech = technical_missing(r)[:25]
    lines.append("Missing skill keywords, by weight: " + (", ".join(tech) if tech else "(none)"))
    lines.append("Role language and boilerplate the scorer also counts are deliberately NOT listed; do not chase them.")
    return "\n".join(lines)



def tailor(company, cli, report=""):
    """One `claude -p` session, scoped to editing this application's sections/."""
    cmd = [cli, "-p", PROMPT.format(company=company, appdir=os.path.join(APPLICATIONS, company),
                                    report=report, policy=policy_sections([1, 2, 3, 8]),
                                    jd=jd_text(company)),
           *llm_flags("tailor"),
           "--add-dir", TRACKING, "--add-dir", TOOL,
           "--allowedTools", ALLOWED_TOOLS,
           "--output-format", "text"]
    os.makedirs(AUTO_CWD, exist_ok=True)
    try:
        ok, out = run(cmd, timeout=900, cwd=AUTO_CWD)
        return ok, out[-400:]
    except subprocess.TimeoutExpired:
        return False, "claude -p timed out after 900s"


def newest_queue_file(slug):
    import glob
    fs = sorted(glob.glob(os.path.join(DATA, "queue", f"{slug}-*.json")))
    return fs[-1] if fs else None


def open_question_count(qfile):
    import json
    try:
        d = json.load(open(qfile))
    except Exception:
        return 0
    return len([q for q in d.get("questions", []) if not q.get("options")])


def draft_questions(company, qfile, cli):
    """Second `claude -p` pass: write tappable answers for anything unanswered."""
    rel = os.path.abspath(qfile)
    cmd = [cli, "-p", Q_PROMPT.format(company=company, qfile=rel, appdir=os.path.join(APPLICATIONS, company),
                                      policy=policy_sections([1, 5, 6, 8]), jd=jd_text(company)),
           *llm_flags("questions"),
           "--add-dir", TRACKING, "--add-dir", TOOL,
           "--allowedTools", ALLOWED_TOOLS,
           "--output-format", "text"]
    try:
        ok, out = run(cmd, timeout=600, cwd=AUTO_CWD)
        return ok, out[-300:]
    except subprocess.TimeoutExpired:
        return False, "question drafting timed out"


def process_role(p, cli, tag=""):
    """Everything for ONE role: scaffold, build+score gate, tailoring rounds,
    fill (serialised), question drafting, DB update. Runs on a worker thread;
    opens its own sqlite connection because connections are not thread-safe."""
    t0 = dt.datetime.now()
    log(f"{tag}{p['company']} — {p['title'][:44]} (pick {p['pick']}: rank {p['score']}, fit {p.get('fit', '?')}, {p['market']})")

    ok, out = run([sys.executable, "-m", "jobpilot.tailor.apply",
                   p["url"], "--company", p["slug"], "--market", p["market"]], timeout=300)
    if not ok:
        if "EXPIRED:" in out:
            con = sqlite3.connect(DB, timeout=30)
            con.execute("UPDATE jobs SET status='expired' WHERE company=? AND lower(title)=lower(?)",
                        (p["company"], p["title"]))
            con.commit(); con.close()
            shutil.rmtree(os.path.join(APPLICATIONS, p["slug"]), ignore_errors=True)
            log(f"  {tag}expired — {out[out.index('EXPIRED:'):][:120].strip()}")
        else:
            log(f"  {tag}scaffold FAILED: {out[-160:]}")
        return

    # Build and score FIRST. A role the untailored resume already matches
    # costs zero LLM sessions. (POLICY: tailoring is a means to a score, and
    # an honest resume that already scores does not need touching.)
    ok, _ = run([sys.executable, "-m", "jobpilot.tailor.apply",
                 "--build", p["slug"]], timeout=600)
    if not ok:
        log(f"  {tag}build FAILED"); return
    target = cfg("pipeline.ats_target", 60)
    rounds = cfg("pipeline.ats_rounds", 2)
    best = ats_score(p["slug"])
    log(f"  {tag}score: {best['match_rate']}% untailored (target {target})")
    rnd = 0
    while best["match_rate"] < target and rnd < rounds:
        safe, unsafe = safe_unsafe(p["slug"])
        if not safe:
            log(f"  {tag}nothing SAFE left to add"
                + (f" (only new claims: {', '.join(unsafe[:6])})" if unsafe else " — remaining gap is role language")
                + "; not running a round")
            break
        rnd += 1
        snap = snapshot_sections(p["slug"])
        report = score_report(p["slug"], best)
        ok, out = tailor(p["slug"], cli, report)
        log(f"  {tag}tailor round {rnd}: {'ok' if ok else 'FAILED — ' + out[-120:]}")
        if not ok:
            restore_sections(p["slug"], snap); break
        run([sys.executable, "-m", "jobpilot.tailor.optimize", p["slug"],
             "--apply"], timeout=300)
        ok, _ = run([sys.executable, "-m", "jobpilot.tailor.apply",
                     "--build", p["slug"]], timeout=600)
        if not ok:
            log(f"  {tag}rebuild FAILED — reverting round"); restore_sections(p["slug"], snap)
            run([sys.executable, "-m", "jobpilot.tailor.apply", "--build", p["slug"]], timeout=600)
            break
        cur = ats_score(p["slug"]); pages = pdf_pages(p["slug"])
        log(f"  {tag}score: {cur['match_rate']}% after round {rnd}"
            f" (stuffing {cur['stuffing_penalty']}, {pages} pages)")
        why = ("stuffing penalty non-zero" if cur["stuffing_penalty"] > 0 else
               f"{pages} pages — the resume must stay at two" if pages > 2 else
               "no improvement" if cur["match_rate"] <= best["match_rate"] else None)
        if why:
            log(f"  {tag}{why} — reverting round {rnd}")
            restore_sections(p["slug"], snap)
            run([sys.executable, "-m", "jobpilot.tailor.apply", "--build", p["slug"]], timeout=600)
            break
        best = cur
        added = new_claims(p["slug"])
        if added:
            log(f"  {tag}new words vs base ({len(added)}): {', '.join(added[:24])}")
            with open(os.path.join(APPLICATIONS, p["slug"], "notes.md"), "a", encoding="utf-8") as nf:
                nf.write(f"\n## Tailoring round {rnd} — words not in the base resume (check before approving)\n"
                         + ", ".join(added) + "\n")
    log(f"  {tag}final: {best['match_rate']}%"
        + (" (below target — real gap, reported not chased)" if best["match_rate"] < target else ""))

    with FILL_LOCK:                       # one browser session at a time
        ok, out = run([sys.executable, "-m", "jobpilot.fill.autofill",
                       "--fill", p["slug"]], timeout=900)
    if not ok and "not supported for autofill" in out:
        # Phenom / Workday / unknown: hand him the content instead of dropping it.
        from jobpilot.fill import manual
        m = re.search(r"Portal '([^']+)'", out)
        qfile = manual.create(p, m.group(1) if m else "unknown")
        ok2, out2 = draft_questions(p["slug"], qfile, cli)
        log(f"  {tag}fill: not autofillable ({m.group(1) if m else 'unknown'}) — manual pack ready"
            f"{', answers drafted' if ok2 else ', drafting FAILED — ' + out2[-80:]}")
        MANUAL.append(p)
    else:
        log(f"  {tag}fill: {'queued for approval' if ok else 'not fillable — ' + out[-100:]}")

    # A question with no drafted answers makes him type on a phone, which is
    # exactly what this system exists to avoid (POLICY.md section 5).
    qfile = newest_queue_file(p["slug"])
    if qfile and open_question_count(qfile):
        ok2, out2 = draft_questions(p["slug"], qfile, cli)
        log(f"  {tag}questions: {'drafted' if ok2 else 'FAILED — ' + out2[-100:]}")
    con = sqlite3.connect(DB, timeout=30)
    con.execute("UPDATE jobs SET status='queued' WHERE key=?", (p["key"],))
    con.commit(); con.close()
    log(f"  {tag}done in {(dt.datetime.now() - t0).seconds}s")



def notify_manual():
    """One Telegram note for the apply-by-hand items created this run."""
    if MANUAL:
        try:
            from jobpilot.core import daily
            link = daily.form_link()
            text = ("🖐 <b>Apply by hand</b> — the portal cannot be autofilled, but everything is ready to copy:\n\n"
                    + "\n".join(f"• {m['company']} — {str(m['title'])[:44]} ({m['market']})" for m in MANUAL)
                    + f"\n\nOpen the list, section \"Apply by hand\": {link}\n"
                      "Each page has every field with a Copy button, the resume files, and drafted answers. "
                      "Tap \"Mark as submitted\" when done.")
            log("telegram (manual): " + ("sent" if daily.telegram(text) else "FAILED"))
        except Exception as e:
            log(f"telegram (manual) FAILED: {type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=cfg("pipeline.tailor_limit", 6),
                    help="roles per run (2 Claude sessions each)")
    ap.add_argument("--floor", type=float, default=cfg("pipeline.tailor_floor", 55.0))
    ap.add_argument("--unscreened", action="store_true",
                    help="allow roles screen.py has not vetted (not recommended)")
    ap.add_argument("--per-company", type=int, default=cfg("pipeline.per_company", 3),
                    help="max roles from one employer per run")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    cli = claude_bin()
    if not cli and not a.dry_run:
        log("claude CLI not found — install: npm install -g @anthropic-ai/claude-code")
        return 1

    picks = candidates(a.limit, a.floor, a.per_company,
                       require_screen=not a.unscreened)
    if not picks:
        log(f"nothing new above {a.floor}")
        return 0
    log(f"{len(picks)} role(s) to prepare (floor {a.floor})")

    if a.dry_run:
        for p in picks:
            log(f"{p['company']} — {p['title'][:44]} (pick {p['pick']}: rank {p['score']}, fit {p.get('fit', '?')}, {p['market']})")
            log(f"  [dry-run] would scaffold {p['slug']}, build, score, tailor-if-needed, fill")
        return 0

    # WAL lets worker threads write their own rows without blocking each other.
    sqlite3.connect(DB).execute("PRAGMA journal_mode=WAL").close()
    workers = max(1, min(int(cfg("pipeline.tailor_workers", 3)), len(picks)))
    log(f"preparing {len(picks)} role(s) with {workers} worker(s); browser fills are serialised")
    t0 = dt.datetime.now()
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tailor") as ex:
        futs = {ex.submit(process_role, p, cli, f"[{i}] "): p for i, p in enumerate(picks, 1)}
        for f in as_completed(futs):
            p = futs[f]
            try:
                f.result()
            except Exception as e:
                log(f"  {p['slug']}: FAILED {type(e).__name__}: {e}")
    log(f"all {len(picks)} done in {(dt.datetime.now() - t0).seconds}s")
    notify_manual()
    log("done — nothing submitted; approve on the phone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
