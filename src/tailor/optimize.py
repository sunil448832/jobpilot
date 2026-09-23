#!/usr/bin/env python3
"""
optimize.py — raise the ATS score for one application WITHOUT adding anything false.

The only honest way to lift a match rate is to say true things in the JD's own
words. So every missing keyword is classified against a TRUTH VOCABULARY built
from Sunil's own base resume:

  SAFE    the same fact in different words — an acronym he already spells out, a
          plural, a synonym, or the target job title. Applied automatically.
  UNSAFE  a genuinely new claim (Spark, Azure, TypeScript). NEVER applied, only
          reported, so he can decide whether it is actually true.

That distinction is the whole point: keyword-stuffing with skills he lacks fails
the interview, and the scorer's own anti-stuffing penalty catches it anyway.

Usage:
    python jobs/optimize.py <company>              # show what it would do
    python jobs/optimize.py <company> --apply      # apply SAFE edits + rebuild
"""
import argparse
import os
import re
import subprocess
import sys

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)
APPS = APPLICATIONS
from jobpilot.tailor import ats_score as A                                       # noqa: E402
from jobpilot.rank import keywords as KW                                       # noqa: E402

# Both directions. If he writes one, the other is the same fact.
EQUIV = [
    ("rag", "retrieval augmented generation"), ("rag", "retrieval-augmented generation"),
    ("llm", "large language model"), ("llms", "large language models"),
    ("nlp", "natural language processing"), ("cv", "computer vision"),
    ("ml", "machine learning"), ("genai", "generative ai"),
    ("vlm", "vision language model"), ("lora", "low rank adaptation"),
    ("peft", "parameter efficient fine tuning"), ("a2a", "agent to agent"),
    ("mlops", "machine learning operations"), ("etl", "extract transform load"),
    ("sota", "state of the art"), ("qa", "question answering"),
    ("ci/cd", "continuous integration"), ("aws", "amazon web services"),
    ("gpt", "generative pretrained transformer"),
    ("agentic ai", "ai agents"), ("multi-agent", "multi agent systems"),
    ("fine-tuning", "finetuning"), ("fine-tuning", "fine tuning"),
    ("vector database", "vector store"), ("evaluation", "evals"),
    ("guardrails", "safety guardrails"), ("quantization", "quantisation"),
    ("optimization", "optimisation"), ("modeling", "modelling"),
]


def truth_vocabulary():
    """Everything Sunil can truthfully claim, from his own base resume."""
    text = []
    for d in (os.path.join(RESUME, "sections"), APPS):
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".tex") or fn == "ats.md":
                try:
                    text.append(open(os.path.join(d, fn), encoding="utf-8").read())
                except Exception:
                    pass
    for extra in ("answers.yaml", "learned.yaml"):
        p = os.path.join(CONFIG, extra)
        if os.path.isfile(p):
            text.append(open(p, encoding="utf-8").read())
    # targets.yaml: only the buckets that are vetted TRUE. The `interest` bucket
    # (gaps Sunil ticked to rank for) is deliberately excluded — reading the raw
    # file would make a ticked keyword look "already on the resume" and SAFE to
    # inject. That is exactly the leak the bucket was designed to avoid.
    try:
        import yaml
        t = yaml.safe_load(open(os.path.join(CONFIG, "targets.yaml"))) or {}
        k = t.get("keywords") or {}
        # `confirmed` = work Sunil ticked as DONE on the keyword page: his own
        # confirmation, so it is truth. `interest` stays excluded.
        text.append("\n".join(str(x) for b in ("strong", "strong_recent", "learned", "confirmed") for x in (k.get(b) or [])))
    except Exception:
        pass
    return A.normalize(A.strip_markup("\n".join(text)))


def stem(t):
    t = re.sub(r"[^a-z0-9 ]+", " ", (t or "").lower()).strip()
    t = re.sub(r"\s+", " ", t)
    return re.sub(r"(ing|ed|es|s)$", "", t)


STOP = {"https", "http", "link", "job", "jobs", "have", "and", "the", "for", "with",
        "you", "your", "our", "we", "us", "are", "is", "be", "will", "can", "that",
        "this", "from", "into", "across", "such", "role", "team", "teams", "work",
        "working", "years", "year", "new", "also", "more", "than", "who", "what",
        "how", "may", "should", "would", "each", "both", "about", "other", "please"}


# JD boilerplate that reads as a phrase of content words but is not a skill.
# "Salary Range" and "Core Values" landed on a real resume before this existed.
BOILERPLATE = re.compile(
    r"salary|compensation|benefit|equity|bonus|perks?|culture|core value|mission|"
    r"vision|diversity|inclusion|equal opportunity|about (us|the)|our team|"
    r"why join|what you.ll|responsibilit|qualificat|requirement|nice to have|"
    r"platform\b|company|employer|applicant|candidate|posting|position|"
    r"opportunit|policy|privacy|reference|disclosure|range\b|package", re.I)


def worth_adding(term, cat):
    """Filter JD-extraction noise. Only real skills and meaningful phrases."""
    t = (term or "").strip().lower()
    if not t or len(t) < 3:
        return False
    if re.match(r"^[^a-z]", t):                 # "- have", "5+", punctuation lead
        return False
    words = [w for w in re.findall(r"[a-z0-9+#.]+", t) if w]
    if not words or all(w in STOP for w in words):
        return False
    if BOILERPLATE.search(t):
        return False
    # Only HARD skills earn a place. "soft" and "other" are where the JD's prose
    # lives, and prose on a skills line reads as careless to a human reviewer —
    # which costs more than the keyword gains.
    return cat == "hard"


def is_safe(term, truth_norm, resume_norm, jd_title):
    """SAFE = the same fact worded differently. Returns (bool, why)."""
    t = (term or "").lower().strip()
    if not t:
        return False, ""

    # Share the ONE alias table with rank.py rather than keeping a second, smaller
    # list here — two tables drift, and this one was already missing pairs the
    # other knew ("gpu inference" -> vLLM). If a canonical term Sunil genuinely has
    # matches this phrase, it is the same fact in the JD's words.
    tn = KW.normalize(t)
    for canon in KW.ALIASES:
        if KW.hit(canon, tn) and canon.lower() in truth_norm:
            return True, f"the JD's wording for '{canon}'"
    if t and t in jd_title.lower():
        return True, "the target job title"
    if t in truth_norm:
        return True, "already true on the base resume"
    for a, b in EQUIV:
        if t == a and b in truth_norm:
            return True, f"acronym/synonym of '{b}'"
        if t == b and a in truth_norm:
            return True, f"expansion of '{a}'"
    s = stem(t)
    if s and len(s) > 3 and s in truth_norm:
        return True, f"word-form variant of '{s}'"
    words = [w for w in s.split() if len(w) > 3]
    if words and all(w in truth_norm for w in words) and len(words) > 1:
        return True, "phrase built from terms already on the resume"
    return False, "NOT on the resume — would be a new claim"


def analyse(company):
    d = os.path.join(APPS, company)
    ats_p, jd_p = os.path.join(d, "ats.md"), os.path.join(d, "JD.md")
    for p in (ats_p, jd_p):
        if not os.path.isfile(p):
            sys.exit(f"missing {p}")
    resume, jd = A.read_text(ats_p), A.read_text(jd_p)
    r = A.score(resume, jd)
    truth = truth_vocabulary()
    resume_norm = A.normalize(A.strip_markup(resume))
    title = r.get("title") or ""

    safe, unsafe = [], []
    for item in r.get("missing", []):
        term = item[0] if isinstance(item, (list, tuple)) else item
        cat = item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else ""
        if not worth_adding(term, cat):
            continue
        ok, why = is_safe(term, truth, resume_norm, title)
        (safe if ok else unsafe).append((term, cat, why))
    order = {"hard": 0, "soft": 1}
    safe.sort(key=lambda x: order.get(x[1], 2))
    unsafe.sort(key=lambda x: order.get(x[1], 2))
    return d, ats_p, resume, r, safe, unsafe


def apply_safe(company_dir, resume, safe):
    """Add the JD's exact wording next to the form already on the resume — into
    sections/skills.tex, the source. ats.md is generated from it on build."""
    added = []
    sk_p = os.path.join(company_dir, "sections", "skills.tex")
    if not os.path.isfile(sk_p):
        return added
    tex = open(sk_p, encoding="utf-8").read()
    lines = tex.splitlines()
    sk = [i for i, l in enumerate(lines) if "\\skills{" in l]
    if not sk:
        return added
    for term, cat, why in safe:
        if re.search(rf"\b{re.escape(term)}\b", resume, re.I) or \
           re.search(rf"\b{re.escape(term)}\b", tex, re.I):
            continue
        target = sk[0]
        for a, b in EQUIV:
            other = b if term.lower() == a else (a if term.lower() == b else None)
            if other:
                for i in sk:
                    if re.search(rf"\b{re.escape(other)}\b", lines[i], re.I):
                        target = i
                        break
                break
        l = lines[target]
        # append before the trailing \par\vspace{...} if present
        m = re.search(r"(\\par\\vspace\{[^}]*\})\s*$", l)
        ins = ", " + term.title().replace("&", "\\&")
        lines[target] = (l[:m.start()].rstrip() + ins + l[m.start():]) if m else (l.rstrip() + ins)
        added.append((term, why))
    if added:
        open(sk_p, "w", encoding="utf-8").write("\n".join(lines) + ("\n" if tex.endswith("\n") else ""))
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("company")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    d, ats_p, resume, r, safe, unsafe = analyse(a.company)
    print(f"  {a.company}: match {r['match_rate']}%  title_present={r['title_present']}")
    print(f"\n  SAFE to add ({len(safe)}) — true, just worded the JD's way:")
    for t, c, why in safe[:16]:
        print(f"    + {t[:34]:<36} {why}")
    print(f"\n  NOT SAFE ({len(unsafe)}) — would be a new claim, left out:")
    for t, c, why in unsafe[:12]:
        print(f"    - {t[:34]:<36} {c}")

    if not a.apply:
        print("\n  re-run with --apply to write the safe ones and rebuild")
        return
    if not safe:
        print("\n  nothing safe to add")
        return

    added = apply_safe(d, resume, safe)
    if not added:
        print("\n  all safe terms already present")
        return
    subprocess.run([sys.executable, "-m", "jobpilot.tailor.build", a.company], cwd=APPS,
                   capture_output=True)          # regenerates ats.md + docx
    after = A.score(A.read_text(ats_p), A.read_text(os.path.join(d, "JD.md")))
    print(f"\n  applied {len(added)} true keyword(s)")
    print(f"  match {r['match_rate']}% -> {after['match_rate']}%   "
          f"stuffing penalty {after['stuffing_penalty']}")
    if after["stuffing_penalty"] > 0:
        print("  !! stuffing penalty non-zero — back some out")


if __name__ == "__main__":
    main()
