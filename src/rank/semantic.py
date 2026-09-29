#!/usr/bin/env python3
"""
semantic.py - EVALUATED AND REJECTED. Kept so nobody re-runs this experiment.

Two embedding approaches were tried for synonym-tolerant matching, to replace or
supplement the alias table in keywords.py. Both underperformed it. Numbers, on the
same seven probes against Sunil's 92 real capability sentences:

  DENSE - all-MiniLM-L6-v2, cosine
    his skills      0.43-0.56      not his         0.32-0.47
    Terraform/K8s scored 0.47, ABOVE his own A2A orchestrator at 0.43 — though
    that probe was a BAD control: Terraform IS in the HarmonIQ stack, so 0.47
    was arguably correct. The verdict rests on the other two results.
    "build systems that reason over internal documents" (RAG) scored 0.32.
    Separation of ~0.1 is too narrow to threshold.

  SPARSE - opensearch-neural-sparse-encoding-doc-v2-distill (SPLADE-style)
    Apache Spark            20.10   <- HIGHEST of all seven. A genuine gap, and
                                       the result that actually condemns it.
    vLLM serving            15.69   <- his strongest area, scored lower
    RAG paraphrase           5.53   <- LOWEST. Below "lead a team".
    Worse than dense. 506s to load + encode 92 sentences (HF rate limiting).

WHY, and why more tuning would not help: both score TOPICAL SIMILARITY, but the
question is CAPABILITY POSSESSION. "Build data pipelines with Apache Spark" is
topically near-identical to his DuckDB streaming work - which is exactly why it
scores high - and the one distinguishing token (spark) is a single weight among
many matching ones. BGE-M3 is the same objective at 2.2GB; it would not separate
these either.

WHAT TO USE INSTEAD:
  * keywords.py - alias table. Measurable win (13 -> 19 matches on TII's JD),
    fully explainable, zero runtime cost, no false positives.
  * For requirement-level gap analysis, the `claude -p` pass autotailor.py already
    runs. It can reason "they want Spark, his equivalent is DuckDB, that is a real
    gap" - inference about substitutability, which no vector distance provides.

The code below still runs (--test, --company X) if someone wants to re-measure.
It is NOT wired into rank.py.
"""
import argparse
import glob
import hashlib
import os
import pickle
import re
import sys

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, MEMORY)
CACHE = os.path.join(DATA, ".embeddings.pkl")
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

_model = None
_cache = None


def model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME, device="cpu")
    return _model


def cache():
    global _cache
    if _cache is None:
        try:
            with open(CACHE, "rb") as f:
                _cache = pickle.load(f)
        except Exception:
            _cache = {}
    return _cache


def save_cache():
    if _cache is not None:
        try:
            with open(CACHE, "wb") as f:
                pickle.dump(_cache, f)
        except Exception:
            pass


def embed(sentences):
    """Embed with an on-disk cache keyed by sentence hash."""
    c = cache()
    todo = [s for s in sentences if _k(s) not in c]
    if todo:
        vecs = model().encode(todo, normalize_embeddings=True,
                              batch_size=32, show_progress_bar=False)
        for s, v in zip(todo, vecs):
            c[_k(s)] = v
    import numpy as np
    return np.vstack([c[_k(s)] for s in sentences])


def _k(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ splitting

def clean(t):
    # JDs are stored HTML-ESCAPED, so "<li>" arrives as "&lt;li&gt;" and the tag
    # stripper below never matches it. Unescape before anything else.
    import html as _html
    t = _html.unescape(_html.unescape(t or ""))
    # LaTeX COMMENT lines first — they are editorial notes about formatting and
    # were dominating the capability set ("ATS NOTE: single-column, name on the
    # FIRST text line" is not a skill).
    t = re.sub(r"(?m)^\s*%.*$", " ", t or "")
    t = re.sub(r"\\(begin|end)\{[^}]*\}", " ", t)
    t = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", " ", t)      # \textbf, \hfill, ...
    t = re.sub(r"[{}$]|\\\\", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[•\u2022\u25cf]\s*", " ", t)
    t = re.sub(r"\bzitemize\b|\bitemize\b|\bskills\b:", " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def capabilities():
    """What Sunil can actually do, one sentence per claim."""
    texts = []
    for p in sorted(glob.glob(os.path.join(TRACKING, "resume", "sections", "*.tex"))):
        texts.append(open(p, encoding="utf-8", errors="replace").read())
    for p in sorted(glob.glob(os.path.join(TRACKING, "project-memory-backup", "*.md"))):
        texts.append(open(p, encoding="utf-8", errors="replace").read())
    # A capability sentence has to describe DOING something technical. Awards,
    # hostel membership and dependency lists are not capabilities, and including
    # them made every requirement look weakly similar to something.
    from jobpilot.rank import keywords as KW
    TECH = set()
    for k, al in KW.ALIASES.items():
        TECH.add(k)
        TECH.update(al)
    ACTION = re.compile(r"\b(built|build|designed|design|developed|deployed|deploy|"
                        r"architected|implemented|optimis|optimiz|trained|train|"
                        r"fine-tuned|benchmark|evaluat|hardened|scaled|automated|"
                        r"migrated|reduced|improved|shipped|serving|orchestrat)", re.I)
    NOISE = re.compile(r"hostel|rotaract|tuition|waiver|percentile|gate\b|"
                       r"active member|certification|coursework|\bnote\b:", re.I)
    out = []
    for t in texts:
        for line in re.split(r"(?<=[.;])\s+|\n", clean(t)):
            line = line.strip(" -•\t")
            if not (45 <= len(line) <= 320) or NOISE.search(line):
                continue
            low = line.lower()
            if not ACTION.search(line) and not sum(1 for k in TECH if k in low) >= 2:
                continue
            out.append(line)
    # de-dup, keep order
    seen, uniq = set(), []
    for s in out:
        k = s.lower()[:80]
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    return uniq


REQ_HINT = re.compile(
    r"experience (with|in|building)|you (will|should|have)|proficien|familiar|"
    r"expertise|knowledge of|ability to|track record|background in|"
    r"comfortable|hands.on|strong |deep |proven ", re.I)


def requirements(jd, limit=25):
    """The lines that state what they actually want."""
    txt = clean(jd)
    parts = re.split(r"(?<=[.;])\s+|\n+", txt)
    reqs = [p.strip() for p in parts if 40 <= len(p.strip()) <= 300]
    # Headings and boilerplate are not requirements: "The impact you will have:"
    # was being reported as an uncovered gap.
    SKIP = re.compile(r"^(about|why|what|who|the impact|our |we are|benefits|"
                      r"compensation|equal opportunity|apply)|:\s*$|"
                      r"^[A-Z][a-z]+( [A-Za-z]+){0,3}:$", re.I)
    reqs = [r for r in reqs if not SKIP.search(r) and len(r.split()) >= 7]
    strong = [r for r in reqs if REQ_HINT.search(r)]
    picked = strong or reqs
    return picked[:limit]


# -------------------------------------------------------------------- scoring

def coverage(jd, caps=None, weak_below=0.35):
    """Returns (coverage 0-1, covered[], gaps[]) — gaps are the real finding."""
    import numpy as np
    reqs = requirements(jd)
    if not reqs:
        return None
    caps = caps or capabilities()
    if not caps:
        return None
    R, C = embed(reqs), embed(caps)
    sim = R @ C.T                       # both normalised, so this is cosine
    best = sim.max(axis=1)
    order = best.argsort()
    covered = [(reqs[i], float(best[i])) for i in order[::-1][:4]]
    gaps = [(reqs[i], float(best[i])) for i in order[:4] if best[i] < weak_below]
    save_cache()
    return float(best.mean()), covered, gaps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--company")
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    caps = capabilities()
    print(f"  {len(caps)} capability sentences from resume + project memories")

    if a.test:
        cases = [
            ("build systems that reason over internal documents", "paraphrase of RAG"),
            ("serve models with low latency at high throughput", "vLLM work"),
            ("orchestrate autonomous workers that call tools", "A2A orchestrator"),
            ("write Terraform to manage Kubernetes clusters", "NOT his — should be low"),
            ("lead a team of engineers and own headcount", "NOT his — should be low"),
        ]
        import numpy as np
        C = embed(caps)
        for text, note in cases:
            v = embed([text])
            best = float((v @ C.T).max())
            hit = "COVERED" if best >= 0.45 else ("weak" if best >= 0.35 else "GAP")
            print(f"  {best:.2f}  {hit:<8} {text[:46]:<48} ({note})")
        save_cache()
        return

    if a.company:
        import sqlite3
        con = sqlite3.connect(os.path.join(DATA, "state.db"))
        row = con.execute("SELECT company,title,jd FROM jobs WHERE company LIKE ? "
                          "ORDER BY score DESC LIMIT 1", (f"%{a.company}%",)).fetchone()
        if not row:
            sys.exit("no such company in state.db")
        co, title, jd = row
        cov, covered, gaps = coverage(jd, caps)
        print(f"\n  {co} — {title}")
        print(f"  requirement coverage: {cov * 100:.0f}%\n")
        print("  BEST COVERED:")
        for r, s in covered:
            print(f"    {s:.2f}  {r[:88]}")
        if gaps:
            print("\n  GAPS — nothing in his experience covers these:")
            for r, s in gaps:
                print(f"    {s:.2f}  {r[:88]}")


if __name__ == "__main__":
    main()
