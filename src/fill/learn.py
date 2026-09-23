#!/usr/bin/env python3
"""
learn.py — fold answers from a submitted review form back into learned.yaml.

The review form writes each submission to the artifact's db. Read it with the
Artifact tool (`read_db`, collection "submissions") into a JSON file, then run
this: every answer Sunil gave is stored against the question, so autofill.py
resolves it automatically next time and never asks again.

Usage:
    python jobs/learn.py submission.json
    python jobs/learn.py --dir out/submissions/     # a whole read_db dump
"""
import argparse
import glob
import json
import os
import re
import sys

import yaml

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)
LEARNED = os.path.join(CONFIG, "learned.yaml")
STOP = {"the", "a", "an", "of", "to", "in", "and", "or", "for", "with", "you",
        "your", "do", "are", "is", "have", "any", "this", "that", "please",
        "select", "all", "apply", "following", "which", "what", "if", "at"}


def keywords(label, n=8):
    words = re.findall(r"[a-z0-9][a-z0-9.+-]{2,}", (label or "").lower())
    seen, out = set(), []
    for w in words:
        if w in STOP or w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= n:
            break
    return out


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[*∗]", "", s or "")).strip().lower()


def load():
    if not os.path.isfile(LEARNED):
        return {"answers": []}
    with open(LEARNED) as f:
        return yaml.safe_load(f) or {"answers": []}


def merge(store, label, answer, source):
    label_n = norm(label)
    for e in store["answers"]:
        if norm(e.get("match", "")) == label_n:
            if e.get("answer") != answer:
                e["answer"] = answer
                e["source"] = source
                return "updated"
            return "unchanged"
    store["answers"].append({
        "match": label_n[:200],
        "keywords": keywords(label),
        "answer": answer,
        "source": source,
    })
    return "added"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir")
    a = ap.parse_args()

    paths = list(a.files)
    if a.dir:
        paths += sorted(glob.glob(os.path.join(a.dir, "**", "*.json"), recursive=True))
    if not paths:
        sys.exit("give a submission JSON file or --dir")

    store = load()
    counts = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0}

    for p in paths:
        sub = json.load(open(p))
        who = sub.get("company") or "?"
        when = (sub.get("decidedAt") or "")[:10]
        for ans in sub.get("answers", []):
            text = (ans.get("text") or "").strip()
            if not text or ans.get("kind") == "unanswered":
                counts["skipped"] += 1
                continue
            src = f"answered by Sunil on the {who} form, {when}"
            counts[merge(store, ans.get("label", ""), text, src)] += 1

    with open(LEARNED, "w") as f:
        f.write("# ============================================================"
                "================\n"
                "# learned.yaml — answers Sunil has given once, reused forever.\n"
                "# Grown automatically by learn.py from submitted review forms.\n"
                "# Edit freely — these are Sunil's own words and must stay true.\n"
                "# ============================================================"
                "================\n\n")
        yaml.safe_dump(store, f, sort_keys=False, allow_unicode=True, width=88)

    print(f"  learned.yaml: {counts['added']} added, {counts['updated']} updated, "
          f"{counts['unchanged']} unchanged, {counts['skipped']} skipped")
    print(f"  total stored: {len(store['answers'])}")


if __name__ == "__main__":
    main()
