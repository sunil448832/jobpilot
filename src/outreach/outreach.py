#!/usr/bin/env python3
"""
outreach.py — draft the messages; you send them (Phase 5).

Writing is the slow part of outreach, so this does that. Sending stays manual —
automated LinkedIn invites and DMs are the most heavily detected behaviour on the
platform, and LinkedIn is the only channel that has actually converted for Sunil.

Every claim here is true to the resume: ~5 years, ex-Amazon Applied Scientist,
M.Tech AI from IIT Jodhpur, production LLM agents, evaluation harnesses, vLLM.

Usage:
    python jobs/outreach.py --company OpenAI --role "Applied AI Engineer"
    python jobs/outreach.py --company Adyen --role "Senior FDE" --kind referral \\
        --name "Rajesh" --their-role "Staff Engineer"
    python jobs/outreach.py --queue
    python -m jobpilot.outreach.outreach --cold <slug>   # after a new connection accepts: the referral ask
"""
import argparse
import os
import sys
import textwrap

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, MEMORY)

HOOK = ("ex-Amazon Applied Scientist, M.Tech in AI from IIT Jodhpur, ~5 years "
        "building production AI")

PROOF = [
    "an orchestrator agent on the A2A protocol running at 98% success across 500 concurrent sessions",
    "an evaluation harness with a cell-level F1 grader that benchmarks our agent fleet at 86% exact-pass",
    "Phi-3 Vision on vLLM at 20 ms median latency in production",
    "a Qdrant hybrid-retrieval index compressed 8x at nDCG parity across 8 BEIR datasets",
]


# What a referral message may say he did, each a line of his base resume compressed
# (resume/sections/experience.tex, projects.tex) — never more than the resume says — with
# the JD words that make it the relevant one. fit() picks the two a JD asks most about.
FIT = [
    (("agent", "agentic", "multi-agent", "orchestrat", "a2a", "crewai", "tool use", "llm application", "autonomous"),
     "built an orchestrator agent on the A2A protocol that routes work to autonomous sub-agents, at 98% "
     "end-to-end success across 500 concurrent sessions"),
    (("evaluat", "eval", "benchmark", "grading", "judge", "quality", "test", "metric", "experiment"),
     "built an agent evaluation service (28 graded tasks, a cell-level F1 grader and an LLM root-cause "
     "analyzer) that benchmarks our agent fleet at 86% exact-pass"),
    (("inference", "serving", "vllm", "latency", "quantiz", "deploy", "gpu", "throughput", "production"),
     "run Phi-3 Vision on vLLM at 20 ms median latency and a self-hosted FP8 LLaMA 3 in production"),
    (("rag", "retrieval", "search", "vector", "embedding", "rank", "semantic", "index"),
     "compressed a production Qdrant hybrid-retrieval index 8x (4-bit) at nDCG/Recall parity across 8 BEIR datasets"),
    (("reinforcement", " rl ", "rlhf", "post-train", "grpo", "reward", "alignment", "fine-tun", "finetun"),
     "implemented GRPO with verifiable rewards from scratch and RL-post-trained Qwen3-4B (QLoRA) for competition math"),
    (("multimodal", "vision", "image", "vlm", "computer vision", "video", "moderation", "classif"),
     "built a FLAVA multimodal classifier for ad moderation at Amazon (72% precision, 90% recall)"),
    (("data pipeline", "etl", "data engineering", "sql", "analytics", "warehouse", "duckdb", "spark", "data platform"),
     "built agents that plan and run end-to-end ETL over databases and S3 from one natural-language task, "
     "cutting manual data-engineering effort by 90%"),
    (("document", "pdf", "extraction", "nlp", "ocr", "parsing", "text"),
     "built an NLP + CV pipeline that parses millions of scientific PDFs and tags them with a self-hosted LLM"),
]


def fit(jd, n=2):
    """The n lines of FIT whose topics the JD mentions most (the first ones on a tie)."""
    t = " " + " ".join((jd or "").lower().split()) + " "
    scored = [(sum(t.count(k) for k in keys), -i, line) for i, (keys, line) in enumerate(FIT)]
    return [line for _, _, line in sorted(scored, reverse=True)[:n]]


def cold_referral(company, role, url=None, jd=""):
    """After a new connection accepts (someone he did not know, asked to connect without a
    note): the referral ask for this role. [First name] is his to fill."""
    a, b = fit(jd)
    link = f" ({url})" if url else ""
    # one line per paragraph: a LinkedIn message keeps every line break it is pasted with
    return "\n\n".join([
        "Hi [First name],",
        f"Thanks for connecting! I've applied for the {role} role at {company}{link} and wanted to "
        "ask whether you'd be open to referring me.",
        f"A little about me: {HOOK}. Most relevant to this role, I {a}, and I {b}.",
        "If it helps, I can send my resume and a two-line summary you could paste into the referral "
        "form. And no worries at all if it's not something you can do.",
        "Thanks,\nSunil"])


def wrap(s, w=76):
    """Re-flow by PARAGRAPH. Filling each source line separately leaves the
    ragged half-lines you get from wrapping already-wrapped text."""
    paras = [" ".join(line.strip() for line in p.splitlines() if line.strip())
             for p in s.split("\n\n")]
    return "\n\n".join(textwrap.fill(p, w) if p else "" for p in paras)


def connection_note(company, role=None):
    r = f" for the {role} role" if role and "role" not in role.lower() else (
        f" for {role}" if role else "")
    return (f"Hi — ML engineer here ({HOOK}). I'm exploring {company}{r} and would "
            f"value connecting with someone doing this work there.")[:300]


def post_accept(company, role, name=None):
    hi = f"Hi {name}," if name else "Hi,"
    return f"""{hi}

Thanks for connecting. I'm an ML engineer — {HOOK} — currently building
LLM agent systems: {PROOF[0]}, plus {PROOF[1]}.

I've applied for the {role} role at {company} and wanted to reach out directly
rather than let the application sit in a queue. If you have five minutes, I'd
value your read on what the team actually optimises for.

Happy to send my resume, or answer anything useful from my side.

Sunil"""


def referral_ask(company, role, name=None, their_role=None):
    hi = f"Hi {name}," if name else "Hi,"
    ctx = f" as a {their_role}" if their_role else ""
    return f"""{hi}

Hope you're well. I saw you're at {company}{ctx} — I've just applied for the
{role} role there and wondered if you'd be open to referring me internally.

Quick context: {HOOK}. Most relevant to this role, I built {PROOF[1]},
and {PROOF[2]}.

No pressure at all if it's not something you're comfortable doing, or if you
don't know the team. If it helps, I can send my resume and a two-line summary
you could paste straight into the referral form.

Thanks either way,

Sunil"""


def recruiter_note(company, role):
    return f"""Hi — I've applied for the {role} role at {company} and wanted to
flag my application directly.

Background: {HOOK}. The closest match to this role is the evaluation work —
{PROOF[1]} — plus production LLM serving: {PROOF[2]}.

I'd need visa sponsorship, and I'm available at two months' notice. Happy to
share anything that would help you assess fit.

Sunil Kumar Sharma
linkedin.com/in/sunil4832sharma | github.com/sunil448832"""


def paper_author(company, role, name=None, paper=None):
    hi = f"Hi {name}," if name else "Hi,"
    p = f' your paper "{paper}"' if paper else " your recent work"
    return f"""{hi}

I read{p} — the evaluation methodology stuck with me, since I've been
building something adjacent: {PROOF[1]}.

I'm an ML engineer ({HOOK}) and I've applied for the {role} role at {company}.
Mostly I wanted to say the work landed; if you're open to a short exchange
about how you evaluate these systems in practice, I'd value it.

Sunil"""


KINDS = {"note": None, "accept": post_accept, "referral": referral_ask, "cold": cold_referral,
         "recruiter": recruiter_note, "paper": paper_author}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--company")
    ap.add_argument("--role", default="the role")
    ap.add_argument("--kind", choices=list(KINDS), help="default: print all")
    ap.add_argument("--name")
    ap.add_argument("--their-role")
    ap.add_argument("--paper")
    ap.add_argument("--queue", action="store_true")
    ap.add_argument("--cold", metavar="SLUG", help="the referral ask for a new connection, after they accept "
                                                   "(an application he submitted; [First name] is his to fill)")
    a = ap.parse_args()

    if a.cold:
        from jobpilot.core import cards as CD
        from jobpilot.core.answers import read_jd
        meta, jd = read_jd(a.cold)
        fs = CD.of(a.cold)
        url = (CD.load(os.path.basename(fs[-1])[:-5]).get("url") if fs else None) or meta.get("Apply URL") or meta.get("Link")
        print(cold_referral(meta.get("Company", a.cold), meta.get("Role / Title", "the role"), url, jd))
        return

    if a.queue:
        import sqlite3
        con = sqlite3.connect(os.path.join(DATA, "state.db"))
        rows = con.execute("SELECT company, title FROM jobs WHERE market != 'usa' "
                           "AND score >= 55 ORDER BY score DESC LIMIT 5").fetchall()
        for co, title in rows:
            print(f"\n{'=' * 74}\n  {co} — {title}\n{'=' * 74}")
            print(f"\n--- connection note (300 cap) ---\n{connection_note(co, title)}")
        return

    if not a.company:
        ap.error("give --company or --queue")

    print(f"\n{'=' * 74}\n  {a.company} — {a.role}\n{'=' * 74}")
    show = [a.kind] if a.kind else ["note", "referral", "accept", "recruiter"]
    for k in show:
        print(f"\n--- {k} ---\n")
        if k == "note":
            n = connection_note(a.company, a.role)
            print(wrap(n))
            print(f"\n[{len(n)}/300 chars]")
        elif k == "referral":
            print(wrap(referral_ask(a.company, a.role, a.name, a.their_role)))
        elif k == "accept":
            print(wrap(post_accept(a.company, a.role, a.name)))
        elif k == "recruiter":
            print(wrap(recruiter_note(a.company, a.role)))
        elif k == "paper":
            print(wrap(paper_author(a.company, a.role, a.name, a.paper)))
    print("\n" + "-" * 74)
    print("Copy and send by hand. Never automate LinkedIn invites or DMs.")


if __name__ == "__main__":
    main()
