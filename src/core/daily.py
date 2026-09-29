#!/usr/bin/env python3
"""
daily.py — the overnight run (Phase 6). Costs Sunil zero morning time.

Sequence:
  1. intake.py   pull every board, filter, dedupe into state.db
  2. rank.py     score and route what is new
  3. tracker.py  follow-ups due, from the submitted queue cards
  4. Telegram    one digest: new roles worth a look + follow-ups due

It deliberately does NOT tailor or fill anything. Tailoring is judgment work
(plan decision A) and filling a form unattended, at 2am, with nobody to read the
screenshot, is how a wrong answer gets submitted. The digest tells Sunil what is
worth his evening; he drives the rest from his phone.

Usage:
    python jobs/daily.py                 # the real run
    python jobs/daily.py --no-telegram
    python jobs/daily.py --dry-run
"""
import argparse
import datetime as dt
import os
import sqlite3
import subprocess
import sys
import json
import urllib.parse
import urllib.request

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, LOGS, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, MEMORY)
from jobpilot.core.config import cfg  # noqa: E402
DB = os.path.join(DATA, "state.db")
ENV = os.path.expanduser("~/.config/jobbot/env")
LOG = os.path.join(LOGS, "daily.log")
LOCK = os.path.join(DATA, ".daily.lock")


def log(msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


MODULES = {"scaffold.py": "jobpilot.tailor.scaffold", "ats_score.py": "jobpilot.tailor.ats_score", "explore": "jobpilot.apply.explore_agentic", "submit": "jobpilot.apply.explore_agentic.replay", "autotailor.py": "jobpilot.tailor.autotailor", "bot.py": "jobpilot.review.bot", "build.py": "jobpilot.tailor.build", "build_reference.py": "jobpilot.tailor.build_reference", "careers.py": "jobpilot.discover.careers", "config.py": "jobpilot.core.config", "daily.py": "jobpilot.core.daily", "dedupe.py": "jobpilot.core.dedupe", "find_careers.py": "jobpilot.discover.find_careers", "form.py": "jobpilot.review.form", "intake.py": "jobpilot.discover.intake", "keyword_form.py": "jobpilot.review.keyword_form", "keyword_learn.py": "jobpilot.rank.keyword_learn", "keywords.py": "jobpilot.rank.keywords", "learn.py": "jobpilot.apply.draft.learn", "llm_eval.py": "jobpilot.screen.llm_eval", "mine_keywords.py": "jobpilot.rank.mine_keywords", "optimize.py": "jobpilot.tailor.optimize", "outreach.py": "jobpilot.outreach.outreach", "paths.py": "jobpilot.core.paths", "prospects.py": "jobpilot.outreach.prospects", "rank.py": "jobpilot.rank.rank", "referral_form.py": "jobpilot.review.referral_form", "referral_tracker.py": "jobpilot.outreach.referral_tracker", "referrals.py": "jobpilot.outreach.referrals", "salary.py": "jobpilot.rank.salary", "screen.py": "jobpilot.screen.screen", "semantic.py": "jobpilot.rank.semantic", "serve.py": "jobpilot.review.serve", "startups.py": "jobpilot.discover.startups", "telegram_setup.py": "jobpilot.review.telegram_setup", "tenants.py": "jobpilot.discover.tenants", "tex2md.py": "jobpilot.tailor.tex2md", "tracker.py": "jobpilot.core.tracker", "quota.py": "jobpilot.core.quota"}


def run(script, *args, timeout=900):
    # Stages are run as modules of the package, not as loose scripts.
    mod = MODULES.get(script, script)
    cmd = [sys.executable, "-m", mod, *args]
    log(f"-> {script} {' '.join(args)}")
    try:
        p = subprocess.run(cmd, cwd=TOOL, capture_output=True, text=True, timeout=timeout)
        tail = (p.stdout or "").strip().splitlines()[-3:]
        for t in tail:
            log(f"   {t}")
        if p.returncode != 0:
            log(f"   !! {script} exited {p.returncode}: {(p.stderr or '')[:200]}")
        return p.returncode == 0
    except subprocess.TimeoutExpired:
        log(f"   !! {script} timed out after {timeout}s")
        return False


def env():
    d = {}
    if os.path.isfile(ENV):
        for line in open(ENV):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
    return d


def telegram(text):
    e = env()
    tok, chat = e.get("TELEGRAM_BOT_TOKEN"), e.get("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        log("   telegram: no credentials, skipping digest")
        return False
    data = urllib.parse.urlencode({
        "chat_id": chat, "text": text[:4000], "parse_mode": "HTML",
        "disable_web_page_preview": "true"}).encode()
    try:
        with urllib.request.urlopen(
                f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=20) as r:
            import json
            return json.load(r).get("ok", False)
    except Exception as ex:
        log(f"   telegram failed: {ex}")
        return False


def form_link():
    """Review URL: tailnet host if configured or detectable, else the LAN IP."""
    host = cfg("server.tailscale_host", "") or ""
    if not host:
        try:
            out = subprocess.run(["tailscale", "status", "--json"],
                                 capture_output=True, timeout=6).stdout
            host = json.loads(out).get("Self", {}).get("DNSName", "").rstrip(".")
        except Exception:
            host = ""
    if not host:
        import socket
        s_ = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s_.connect(("8.8.8.8", 80)); host = s_.getsockname()[0]
        except Exception:
            host = "localhost"
        finally:
            s_.close()
    tok = env().get("FORM_TOKEN", "")
    return f"http://{host}:{cfg('server.port', 8765)}/" + (f"?t={tok}" if tok else "")


def digest(since_iso):
    """New roles worth the evening, plus follow-ups due."""
    con = sqlite3.connect(DB)
    # status='new' is what excludes both rejects and roles already queued. Without it the digest showed roles
    # the pipeline had already decided against — screen verdicts like "requires
    # native/fluent German", and roles in markets Sunil had removed — presented
    # on his phone as worth the evening. The floor comes from config rather than
    # a literal 50 so it moves with pipeline.tailor_floor. Ordering stays on
    # score: sorting vetted roles ahead of unvetted ones buried a 94.4 Dublin
    # role under 49.7 ones, which is worse than showing one unscreened row.
    floor = cfg("pipeline.tailor_floor", 45.0)
    fresh = con.execute(
        "SELECT score,company,title,location,market,fit FROM jobs "
        "WHERE seen >= ? AND status='new' AND score >= ? ORDER BY "
        "CASE market WHEN 'usa' THEN 1 ELSE 0 END, score DESC LIMIT 8",
        (since_iso, floor)).fetchall()
    total_new = con.execute("SELECT COUNT(*) FROM jobs WHERE seen >= ?",
                            (since_iso,)).fetchone()[0]
    from jobpilot.core import tracker
    due = tracker.followups()

    e = env()
    token = e.get("FORM_TOKEN", "")
    host = "sunil-latitude-3420.taild263f7.ts.net"
    link = f"http://{host}:{cfg('server.port', 8765)}/?t={token}" if token else ""

    lines = [f"☀️ <b>Overnight scan</b> — {dt.date.today():%a %d %b}", ""]
    if fresh:
        lines.append(f"<b>{len(fresh)} worth a look</b> (of {total_new} new)")
        for s, co, t, loc, mk, fit in fresh:
            lines.append(f"• <b>{s:.0f}</b>{' · fit ' + str(fit) if fit is not None else ''} {co} — {t[:44]}\n   <i>{mk} · {loc[:34]}</i>")
    else:
        lines.append(f"No new roles above threshold ({total_new} scanned).")
    if due:
        lines += ["", f"<b>⏰ {len(due)} follow-up(s) due</b>"]
        for d in due[:5]:
            tag = f"{d['overdue']}d late" if d["overdue"] > 0 else "today"
            lines.append(f"• {d['company']} — {str(d['role'])[:36]} ({tag})")
    if link:
        lines += ["", f"Review queue: {link}"]
    return "\n".join(lines)


# Every stage of the unattended run, in order. `--only` picks a subset so a
# stage can be re-run on demand without paying for the ones before it.
STAGES = ("dedupe", "intake", "hold", "rank", "screen", "tailor", "referrals", "digest")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-tailor", action="store_true",
                    help="skip the claude -p tailoring step")
    ap.add_argument("--per-company", type=int, default=cfg("pipeline.per_company", 3))
    ap.add_argument("--no-screen", action="store_true",
                    help="skip the Claude false-positive screen")
    ap.add_argument("--source-limit", type=int, default=cfg("discovery.source_limit", 0),
                    help="intake stops once this many postings match titles + markets (0 = every board)")
    ap.add_argument("--tailor-limit", type=int, default=cfg("pipeline.tailor_limit", 6),
                    help="roles to tailor per run (2 Claude sessions each)")
    ap.add_argument("--digest-only", action="store_true",
                    help="just send the approval prompt; scan nothing")
    ap.add_argument("--no-submit", action="store_true",
                    help="with --digest-only: do not file approved applications")
    ap.add_argument("--submit-limit", type=int, default=cfg("pipeline.submit_limit", 10),
                    help="approved applications to file per review run")
    ap.add_argument("--only", metavar="STAGES",
                    help="run only these stages, comma-separated, in pipeline order: "
                         + ",".join(STAGES))
    a = ap.parse_args()
    if a.only:
        bad = [x for x in a.only.split(",") if x.strip() not in STAGES]
        if bad:
            sys.exit(f"unknown stage(s) {bad}; choose from {', '.join(STAGES)}")
        only = {x.strip() for x in a.only.split(",")}
    else:
        only = set(STAGES)
    want = only.__contains__
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    # Two intakes at once fight over the same SQLite file and the same boards.
    # The first unattended run died this way, so take an exclusive lock and exit
    # cleanly if another run holds it.
    import fcntl
    lockf = open(LOCK, "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another daily run is already in progress — exiting")
        return
    lockf.write(str(os.getpid()))
    lockf.flush()

    since = (dt.datetime.now() - dt.timedelta(hours=26)).isoformat(timespec="seconds")
    log("=" * 60)
    log("daily run starting")

    failures = []
    if a.digest_only:
        # The 07:00 / 19:00 approval prompts: nothing to scan, just tell him what
        # is waiting. Anything deferred is still here, by design.
        pend = 0
        try:
            from jobpilot.core import cards as CD
            for d in CD.cards():
                st = d.get("status")
                if st in ("pending", "needs_input", "approved", "manual"):
                    pend += 1
        except Exception:
            pass
        e = env()
        link = form_link()
        text = (f"🔔 <b>Review time</b> — {dt.datetime.now():%a %d %b %H:%M}\n\n"
                f"<b>{pend}</b> application(s) waiting for you.\n\n"
                f"{link}\n\n"
                f"<i>Later keeps it in the queue. Only Reject drops it.</i>")
        # Sundays, morning run: let targets.yaml learn from the week's applied
        # JDs. Pure Python over files on disk; the digest carries the result.
        now = dt.datetime.now()
        if now.weekday() == 6 and now.hour < 12:
            # Weekly: let boards.yaml find new employers' boards on its own.
            run("tenants.py", "--quiet", timeout=1800)
            try:
                from jobpilot.rank import keyword_learn
                safe, gaps = keyword_learn.promote()
                pending = keyword_learn.pending()
                if safe or pending:
                    text += "\n\n📚 <b>Keywords this week</b>\n"
                    if safe:
                        text += "learned (already on your resume): " + ", ".join(c["term"] for c in safe) + "\n"
                    if pending:
                        text += ("market asks, not on resume — tick the ones to track:\n"
                                 + form_link().replace("/?", "/keywords?"))
                log(f"keywords: +{len(safe)} learned, {len(gaps)} gaps")
            except Exception as ex:
                log(f"keywords: FAILED {type(ex).__name__}: {ex}")
        log(f"digest-only: {pend} pending")
        if not a.no_telegram:
            log("telegram: " + ("sent" if telegram(text) else "FAILED"))
        run("referral_tracker.py", "--digest",
            *(["--no-telegram"] if a.no_telegram else []), timeout=300)
        # File what he has APPROVED since the last run. Approval on the phone is
        # the only human gate; pending / needs_input / manual are never touched.
        # Each outcome (filed / needs answers / failed) is its own Telegram line.
        if not a.no_submit and not a.dry_run:
            if not run("submit", "--limit", str(a.submit_limit),
                       timeout=3600):
                log("   !! submit-approved failed — see each card's fail_reason (applications/<slug>/cards/)")
        return

    # Dedupe BEFORE intake so new rows are compared against a clean store, and
    # again at the end because queue and referral-tracker writes happen throughout.
    if want("dedupe"):
        run("dedupe.py", timeout=600)

    if want("intake"):
        if not run("intake.py", *(["--limit", str(a.source_limit)] if a.source_limit else []), timeout=1800):
            failures.append("intake.py")
    # A company at its application limit takes no ranking, screening or tailoring: its
    # postings wait as 'held' until it has room (core/quota.py).
    if want("hold") and not run("quota.py", "--hold", *(["--dry-run"] if a.dry_run else []), timeout=120):
        failures.append("quota.py")
    if want("rank") and "intake.py" not in failures:
        if not run("rank.py", "--all", "--top", "1", timeout=900):
            failures.append("rank.py")
    # Screen BEFORE tailoring: one Claude call over 30 candidates is far cheaper
    # than tailoring even one role that was never viable (no sponsorship, wrong
    # discipline, 10+ years). Rejects are marked and never reach autotailor.
    if want("screen") and not a.no_tailor and not a.no_screen:
        if not run("screen.py", "--loop", "--per-company", str(a.per_company),
                   timeout=2400):
            failures.append("screen.py")

    # Tailor + fill what survived. Never submits — approval stays a human decision.
    if want("tailor") and not a.no_tailor:
        if not run("autotailor.py", "--limit", str(a.tailor_limit),
                   "--per-company", str(a.per_company),
                   *(["--dry-run"] if a.dry_run else []), timeout=2400):
            failures.append("autotailor.py")
    # Referral follow-ups are the highest-converting thing he does, so they get
    # their own line in the evening digest rather than living only on a page.
    if want("referrals") and not run("referral_tracker.py", "--digest",
               *(["--no-telegram"] if a.dry_run or a.no_telegram else []), timeout=300):
        failures.append("referral_tracker.py")

    if want("dedupe"):
        run("dedupe.py", timeout=600)      # queue + referral tracker were written above

    if not want("digest"):
        log("daily run complete (stages: " + ",".join(x for x in STAGES if want(x)) + ")")
        return
    text = digest(since)
    if failures:
        # Silence is indistinguishable from success, so say when a stage broke.
        text += ("\n\n\u26a0\ufe0f <b>Stage(s) failed:</b> " + ", ".join(failures)
                 + "\nCheck: journalctl --user -u jobpilot-daily -n 40")
    log("digest:\n" + text.replace("<b>", "").replace("</b>", "")
        .replace("<i>", "").replace("</i>", ""))
    if not a.no_telegram and not a.dry_run:
        log("telegram: " + ("sent" if telegram(text) else "FAILED"))
    log("daily run complete")


if __name__ == "__main__":
    main()
