#!/usr/bin/env python3
"""
ats_score.py — Local ATS match-rate scorer (mirrors how 2026 ATS + Jobscan-style
tools actually score a resume against a job description).

Modeled on public descriptions of modern ATS (Workday / Greenhouse / iCIMS) and
Jobscan's match-rate methodology:

  Layer 1 — PARSE:   can standard sections/title/years be extracted from the resume.
  Layer 2 — MATCH:   weighted keyword overlap with the JD, priority order:
                       hard skills / tools / domain terms  (highest weight)
                       job title match
                       education level (only if the JD requires a degree)
                       soft skills
                       other JD keywords                    (lowest weight)
  Semantic:          synonym/acronym aware (RAG == Retrieval-Augmented Generation),
                     plus a TF-IDF cosine "content similarity" second opinion.
  Anti-stuffing:     unnaturally high repetition of a keyword LOWERS the score
                     (Workday 2026 flags keyword-density manipulation).

It is an APPROXIMATION — no public tool is the employer's real ATS. Use it to find
missing keywords and parse issues, not as a guaranteed real-world number. Never
keyword-stuff: add only terms that are TRUE for the candidate.

Usage:
    python ats_score.py --company capital-one          # scores <company>/ats.md vs <company>/JD.md
    python ats_score.py --resume path/ats.md --jd path/jd.txt
    python ats_score.py --company capital-one --json    # machine-readable output
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from collections import Counter

from jobpilot.core.paths import APPLICATIONS as APP_DIR  # noqa: E402

# ---------------------------------------------------------------------------
# Domain lexicons. Extend freely — hard skills drive most of the score.
# Keep entries lowercase. Multi-word entries are matched as phrases.
# ---------------------------------------------------------------------------
HARD_SKILLS = {
    # languages
    "python", "sql", "c++", "c#", "java", "golang", "go", "scala", "rust",
    "javascript", "typescript", "bash",
    # ml / ai
    "machine learning", "deep learning", "nlp", "natural language processing",
    "computer vision", "generative ai", "genai", "llm", "llms",
    "large language model", "large language models", "rag",
    "retrieval-augmented generation", "retrieval augmented generation",
    "multi-agent", "multi agent", "agentic", "agent orchestration",
    "agents", "ai agents", "prompt engineering", "fine-tuning", "fine tuning",
    "peft", "lora", "qlora", "quantization", "fp8", "distillation",
    "foundation model", "foundation models", "post-training", "rlhf", "rl",
    "reinforcement learning", "grpo", "diffusion", "multimodal",
    "vision-language", "embeddings", "vector database", "vector databases",
    "vector search", "semantic search", "similarity search", "reranking",
    "guardrails", "hallucination", "model evaluation", "evals", "evaluation",
    "observability", "inference", "inference optimization", "latency",
    "throughput", "model serving", "serving", "model deployment",
    "tool calling", "function calling", "knowledge base", "recommendation",
    "supervised learning", "unsupervised learning", "feature engineering",
    "distributed training", "experimentation", "a/b testing", "mlops", "aiops",
    "model governance", "governance", "monitoring",
    # frameworks / tools
    "pytorch", "tensorflow", "jax", "huggingface", "hugging face", "transformers",
    "langchain", "llamaindex", "llama-index", "crewai", "pydanticai", "vllm",
    "sglang", "ray", "deepspeed", "accelerate", "fastapi", "flask", "duckdb",
    "pandas", "numpy", "spark", "airflow", "mlflow", "wandb", "weights & biases",
    "faiss", "pinecone", "qdrant", "milvus", "weaviate", "chroma", "elasticsearch",
    "bm25", "colbert", "clip", "bedrock", "sagemaker", "vertex ai", "nemo",
    "triton", "tensorrt", "onnx", "torchserve", "openai", "anthropic", "claude",
    # infra / cloud / data
    "aws", "azure", "gcp", "google cloud", "docker", "kubernetes", "k8s",
    "terraform", "ci/cd", "github actions", "jenkins", "microservices",
    "distributed systems", "rest api", "rest apis", "api", "apis", "grpc",
    "redis", "postgresql", "postgres", "mysql", "mongodb", "dynamodb",
    "snowflake", "athena", "bigquery", "s3", "kafka", "sqs", "lambda",
    "etl", "data pipeline", "data pipelines", "data engineering",
    "prometheus", "grafana", "linux", "git",
    # cv / classic ml
    "opencv", "arcface", "deepsort", "resnet", "cnn", "lstm", "transformer",
    "bert", "gpt", "mistral", "llama", "phi-3", "llava", "flava", "whisper",
    "ocr", "textract", "docling", "pymupdf",
}

SOFT_SKILLS = {
    "communication", "collaboration", "problem-solving", "problem solving",
    "leadership", "ownership", "stakeholder", "cross-functional", "mentoring",
    "fast-paced", "independent", "self-starter", "adaptability", "curiosity",
    "analytical", "detail-oriented", "teamwork", "autonomy",
}

# Canonicalization: map many surface forms to ONE token so resume and JD match
# even when they use different wording (acronym <-> expansion, hyphenation).
SYNONYMS = {
    "large language model": "llm", "large language models": "llm", "llms": "llm",
    "retrieval-augmented generation": "rag", "retrieval augmented generation": "rag",
    "natural language processing": "nlp",
    "generative ai": "genai",
    "hugging face": "huggingface",
    "weights & biases": "wandb", "weights and biases": "wandb",
    "google cloud": "gcp",
    "k8s": "kubernetes",
    "multi agent": "multi-agent", "multiagent": "multi-agent",
    "fine tuning": "fine-tuning", "finetuning": "fine-tuning",
    "rest apis": "rest api", "apis": "api",
    "vector databases": "vector database",
    "large language models": "llm",
    "problem solving": "problem-solving",
    "golang": "go",
    "postgres": "postgresql",
    "sr": "senior", "snr": "senior",
    "llama-index": "llamaindex",
    "inference optimization": "inference",
}

# Generic words never treated as meaningful JD keywords.
STOPWORDS = set("""
a an the and or of to in on for with as at by from into over under is are be been
being this that these those it its their our your you we they he she them his her
will shall can could should would may might must do does did done have has had
not no nor so than then too very just also more most other some such only own same
about above after again against all any because before below between both during
each few further here how if into itself once out through until up down while who
whom why what which when where whose across per via etc using use used based new
role team work working experience years year strong ability able help build built
building including include includes across within without upon around among along
company platform product products solution solutions system systems technology
technologies engineer engineering candidate candidates responsibilities requirements
qualifications preferred required nice must plus join looking seeking hire hiring
opportunity impact deliver drive design develop developing development own owning
end mission deep hands-on world real great good best fast high level like want
yrs yr months month day days ago apply applicants applicant remote onsite hybrid
""".split())


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def strip_markup(text: str) -> str:
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)   # html comments
    text = re.sub(r"[#>*`_|]", " ", text)                        # md syntax
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)             # md links
    return text


def normalize(text: str) -> str:
    text = text.lower()
    # keep + # . / - inside tokens (c++, c#, ci/cd, node.js, fine-tuning)
    text = re.sub(r"[^a-z0-9+#./&\- ]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def canon(term: str) -> str:
    t = term.strip().lower()
    return SYNONYMS.get(t, t)


def ngrams(tokens, n):
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def extract_jd_keywords(jd_norm: str):
    """Return {canonical_keyword: {'category','jd_count','display'}} for JD terms
    that matter: known skills, or repeated/domain phrases."""
    tokens = [t for t in jd_norm.split() if t]
    phrases = Counter()
    for n in (3, 2, 1):
        for g in ngrams(tokens, n):
            phrases[g] += 1

    keywords = {}
    for phrase, count in phrases.items():
        words = phrase.split()
        if all(w in STOPWORDS for w in words):
            continue
        c = canon(phrase)
        is_hard = phrase in HARD_SKILLS or c in HARD_SKILLS
        is_soft = phrase in SOFT_SKILLS or c in SOFT_SKILLS
        # keep: known skill, OR a repeated multi-word phrase, OR repeated single word
        keep = is_hard or is_soft or (len(words) >= 2 and count >= 2) or count >= 3
        if not keep:
            continue
        if len(phrase) < 2:
            continue
        cat = "hard" if is_hard else "soft" if is_soft else "other"
        # collapse a phrase into its canonical; keep the strongest category seen
        prev = keywords.get(c)
        if prev is None or _cat_rank(cat) > _cat_rank(prev["category"]):
            keywords[c] = {"category": cat, "jd_count": count, "display": phrase}
        else:
            prev["jd_count"] = max(prev["jd_count"], count)
    # drop 1-grams that are substrings already covered by a kept 2/3-gram skill
    return _dedupe_subsumed(keywords)


def _cat_rank(cat):
    return {"hard": 3, "soft": 2, "other": 1}[cat]


def _dedupe_subsumed(keywords):
    keys = sorted(keywords, key=len, reverse=True)
    kept = {}
    for k in keys:
        if any(k != longer and re.search(rf"\b{re.escape(k)}\b", longer) for longer in kept):
            # k is a word inside an already-kept longer skill phrase -> skip only
            # if the longer one is a hard skill (keeps signal clean)
            if any(kept[l]["category"] == "hard" for l in kept if k in l):
                continue
        kept[k] = keywords[k]
    return kept


CATEGORY_WEIGHT = {"hard": 5.0, "title": 4.0, "education": 3.0, "other": 2.0, "soft": 1.0}


def present_in_resume(keyword: str, resume_norm: str, resume_canon_terms: set) -> bool:
    if keyword in resume_canon_terms:
        return True
    # word-boundary phrase match on canonical resume text
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", resume_norm) is not None


def build_resume_canon_terms(resume_norm: str) -> set:
    tokens = [t for t in resume_norm.split() if t]
    terms = set()
    for n in (1, 2, 3):
        for g in ngrams(tokens, n):
            terms.add(canon(g))
    return terms


def detect_required_years(jd_norm: str):
    m = re.findall(r"(\d+)\s*\+?\s*(?:years|yrs)", jd_norm)
    return max((int(x) for x in m), default=None)


def detect_resume_years(resume_norm: str):
    m = re.findall(r"(\d+)\s*\+?\s*(?:years|yrs)", resume_norm)
    return max((int(x) for x in m), default=None)


def detect_degree_required(jd_norm: str):
    req = re.search(r"(bachelor|master|phd|ph\.d|m\.?s\.?|b\.?s\.?|m\.?tech|degree)", jd_norm)
    return bool(req)


def detect_resume_degree(resume_norm: str):
    return bool(re.search(r"(bachelor|master|phd|ph\.d|m\.tech|b\.tech|m\.sc|b\.sc|degree)", resume_norm))


def _clean_title(s: str) -> str:
    s = re.sub(r"[*#>_`]", "", s)             # strip markdown
    s = s.split("|")[0].split("(")[0]         # drop trailing "| loc" or "(...)"
    s = re.sub(r"^\W+", "", s).strip()        # leading punctuation
    return s


def detect_title(jd_text: str):
    # try "Role / Title:" line first (our JD.md format), else first non-empty line.
    # Require the label to be immediately followed by a colon so "Role / Title: X"
    # captures X, not "Title: X". Prefer 'title' over 'role'.
    clean = re.sub(r"[*#>_`]", "", jd_text)
    for label in ("title", "position", "role"):
        m = re.search(rf"\b{label}\s*:\s*(.+)", clean, re.IGNORECASE)
        if m:
            return _clean_title(m.group(1))
    for line in jd_text.splitlines():
        line = _clean_title(line.strip(" #-*"))
        if len(line) > 3 and not line.lower().startswith(("company", "location", "hiring", "we ")):
            return line
    return ""


def stuffing_penalty(resume_norm: str, matched_hard: list):
    """Return (penalty_fraction, warnings). Penalize over-repeated keywords."""
    words = resume_norm.split()
    total = max(len(words), 1)
    warnings = []
    penalty = 0.0
    for kw in matched_hard:
        # count phrase occurrences
        c = len(re.findall(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", resume_norm))
        density = c / total
        if c >= 12 or density > 0.03:
            warnings.append(f"'{kw}' appears {c}x (density {density:.1%}) — looks like stuffing")
            penalty += 0.02
    return min(penalty, 0.15), warnings


def parse_checks(resume_text: str):
    """Layer-1 parseability checks."""
    checks = []
    low = resume_text.lower()
    checks.append(("Has an Experience section", bool(re.search(r"\bexperience\b", low))))
    checks.append(("Has an Education section", "education" in low))
    checks.append(("Has a Skills section", "skills" in low))
    checks.append(("Has an email", bool(re.search(r"[\w.\-]+@[\w.\-]+", resume_text))))
    checks.append(("Has a phone number", bool(re.search(r"\+?\d[\d\s\-()]{7,}", resume_text))))
    checks.append(("Has dated roles (year ranges)", bool(re.search(r"(19|20)\d\d", resume_text))))
    checks.append(("No pipe/tab tables (ATS-safe)", "\t" not in resume_text))
    return checks


def score(resume_text: str, jd_text: str):
    resume_norm = normalize(strip_markup(resume_text))
    jd_norm = normalize(strip_markup(jd_text))
    resume_terms = build_resume_canon_terms(resume_norm)

    jd_keywords = extract_jd_keywords(jd_norm)

    # Title as its own weighted item
    title = detect_title(jd_text)
    title_norm = canon(normalize(title))
    title_present = False
    if title_norm:
        # match on token overlap: present if >=60% of the title's meaningful words
        # (or its head phrase) appear in the resume — robust to "..., Agentic Platform"
        title_words = [canon(w) for w in title_norm.split() if w not in STOPWORDS and len(w) > 1]
        if title_words:
            hits = sum(1 for w in title_words
                       if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", resume_norm))
            title_present = hits / len(title_words) >= 0.6

    # Score accumulation
    total_w = 0.0
    got_w = 0.0
    matched, missing = [], []
    matched_hard = []
    for kw, meta in jd_keywords.items():
        w = CATEGORY_WEIGHT[meta["category"]] * (1.0 + 0.15 * (meta["jd_count"] - 1))
        total_w += w
        if present_in_resume(kw, resume_norm, resume_terms):
            got_w += w
            matched.append((kw, meta["category"], meta["jd_count"]))
            if meta["category"] == "hard":
                matched_hard.append(kw)
        else:
            missing.append((kw, meta["category"], meta["jd_count"]))

    # Title contribution
    if title_norm:
        total_w += CATEGORY_WEIGHT["title"]
        if title_present:
            got_w += CATEGORY_WEIGHT["title"]

    # Education contribution (only if JD requires a degree)
    edu_note = None
    if detect_degree_required(jd_norm):
        total_w += CATEGORY_WEIGHT["education"]
        if detect_resume_degree(resume_norm):
            got_w += CATEGORY_WEIGHT["education"]
            edu_note = "Degree required by JD and present on resume."
        else:
            edu_note = "JD requires a degree — none detected on resume."

    # Years mapping (informational + small effect)
    req_years = detect_required_years(jd_norm)
    res_years = detect_resume_years(resume_norm)
    years_note = None
    if req_years is not None:
        years_note = f"JD asks ~{req_years}+ yrs; resume shows ~{res_years or 0} yrs."

    base = (got_w / total_w) if total_w else 0.0
    penalty, stuff_warn = stuffing_penalty(resume_norm, matched_hard)
    match_rate = max(0.0, base - penalty)

    # TF-IDF cosine as a semantic second opinion
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        v = TfidfVectorizer(ngram_range=(1, 2), stop_words="english")
        m = v.fit_transform([resume_norm, jd_norm])
        cosine = float(cosine_similarity(m[0], m[1])[0][0])
    except Exception:
        cosine = None

    return {
        "match_rate": round(match_rate * 100, 1),
        "keyword_coverage": round(base * 100, 1),
        "stuffing_penalty": round(penalty * 100, 1),
        "semantic_similarity": round(cosine * 100, 1) if cosine is not None else None,
        "title": title, "title_present": title_present,
        "matched": sorted(matched, key=lambda x: (-_cat_rank(x[1]), -x[2])),
        "missing": sorted(missing, key=lambda x: (-_cat_rank(x[1]), -x[2])),
        "parse_checks": parse_checks(resume_text),
        "edu_note": edu_note, "years_note": years_note,
        "stuffing_warnings": stuff_warn,
        "counts": {"jd_keywords": len(jd_keywords),
                   "matched": len(matched), "missing": len(missing)},
    }


def resolve_paths(args):
    if args.company:
        cdir = os.path.join(APP_DIR, args.company)
        resume = args.resume or os.path.join(cdir, "ats.md")
        jd = args.jd or os.path.join(cdir, "JD.md")
        return resume, jd
    if not (args.resume and args.jd):
        sys.exit("Provide --company, or both --resume and --jd.")
    return args.resume, args.jd


def band(rate):
    if rate >= 80: return "STRONG — likely to pass keyword filters"
    if rate >= 70: return "GOOD — competitive; close a few gaps"
    if rate >= 55: return "FAIR — add missing hard skills"
    return "WEAK — significant keyword gaps"


def print_report(r):
    print("=" * 64)
    print(f"  ATS MATCH RATE:  {r['match_rate']}%   ({band(r['match_rate'])})")
    print("=" * 64)
    print(f"  Keyword coverage : {r['keyword_coverage']}%")
    if r["semantic_similarity"] is not None:
        print(f"  Semantic (TF-IDF): {r['semantic_similarity']}%  (content-overlap second opinion)")
    if r["stuffing_penalty"]:
        print(f"  Stuffing penalty : -{r['stuffing_penalty']}%")
    print(f"  JD keywords: {r['counts']['jd_keywords']} | matched: {r['counts']['matched']} | missing: {r['counts']['missing']}")
    print()
    print(f"  Job title: {r['title'] or '(none found)'}  ->  {'MATCH' if r['title_present'] else 'NOT on resume'}")
    if r["years_note"]:  print(f"  {r['years_note']}")
    if r["edu_note"]:    print(f"  {r['edu_note']}")
    print()

    print("  PARSEABILITY (Layer 1):")
    for name, ok in r["parse_checks"]:
        print(f"    [{'x' if ok else ' '}] {name}")
    print()

    if r["stuffing_warnings"]:
        print("  ⚠ STUFFING WARNINGS:")
        for w in r["stuffing_warnings"]:
            print(f"    - {w}")
        print()

    def show(items, label):
        if not items: return
        print(f"  {label}:")
        for kw, cat, cnt in items[:40]:
            tag = {"hard": "HARD", "soft": "soft", "other": "kw"}[cat]
            print(f"    - {kw}  [{tag}]" + (f" (JD x{cnt})" if cnt > 1 else ""))
        print()

    show([m for m in r["missing"] if m[1] == "hard"], "MISSING HARD SKILLS (add if TRUE — highest impact)")
    show([m for m in r["missing"] if m[1] != "hard"], "Other missing keywords")
    show(r["matched"], "MATCHED (already covered)")
    print("  NOTE: approximation only — not the employer's real ATS. Never add")
    print("  a keyword that isn't genuinely true for the candidate.")


def main():
    ap = argparse.ArgumentParser(description="Local ATS match-rate scorer.")
    ap.add_argument("--company", help="company folder under applications/ (uses its ats.md + JD.md)")
    ap.add_argument("--resume", help="path to resume text/markdown (default: <company>/ats.md)")
    ap.add_argument("--jd", help="path to JD text (default: <company>/JD.md)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args()

    resume_path, jd_path = resolve_paths(args)
    for p in (resume_path, jd_path):
        if not os.path.isfile(p):
            sys.exit(f"File not found: {p}")

    r = score(read_text(resume_path), read_text(jd_path))
    if args.json:
        print(json.dumps(r, indent=2))
    else:
        print_report(r)


if __name__ == "__main__":
    main()
