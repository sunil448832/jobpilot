#!/usr/bin/env python3
"""
tex2md.py — derive the ATS-clean markdown (and so the .docx) FROM the LaTeX.

One source of truth. The .docx an ATS parses used to come from a separate,
hand-written ats.md that the LLM re-authored per application; it drifted from
the PDF (it claimed TorchServe, lacked the RL project) and nothing checked the
two agreed. Now applications/<slug>/sections/*.tex is the only thing anyone
edits, and this turns it into markdown mechanically.

Handles exactly the macro set the resume uses: \\jobentry, \\subsection with
\\hfill\\href, zitemize/\\item, \\skills{Label:}, \\textbf/\\textit/\\texttt,
\\href, \\hfill, TeX escapes and dashes. Anything else is stripped to its text.

    python tex2md.py <app-dir>            # prints markdown
    python tex2md.py <app-dir> -o ats.md
"""
import os
import re
import sys

# Spell each acronym out once — ATS keyword matchers look for both forms.
ACRONYMS = {
    "RAG": "Retrieval-Augmented Generation", "LLM": "Large Language Model",
    "LLMs": "Large Language Models", "NLP": "Natural Language Processing",
    "SFT": "Supervised Fine-Tuning", "GRPO": "Group Relative Policy Optimization",
    "RLVR": "Reinforcement Learning with Verifiable Rewards",
    "RL": "Reinforcement Learning", "A2A": "Agent-to-Agent",
    "LoRA": "Low-Rank Adaptation", "QLoRA": "Quantized Low-Rank Adaptation",
    "ETL": "Extract, Transform, Load", "HITL": "Human-in-the-Loop",
    "OCR": "Optical Character Recognition", "CI/CD": "Continuous Integration / Continuous Delivery",
}
SECTION_ORDER = [("objective", "Summary"), ("skills", "Skills"), ("experience", "Experience"),
                 ("projects", "Projects"), ("education", "Education"),
                 ("achievements", "Achievements")]


def strip_preamble(t):
    out = []
    for l in t.splitlines():
        s = l.strip()
        if s.startswith("%"):
            continue
        if s.startswith(("\\documentclass", "\\usepackage", "\\begin{document}", "\\end{document}")):
            continue
        l = re.sub(r"(?<!\\)%.*$", "", l)          # trailing comments
        out.append(l)
    return "\n".join(out)


def detex(s):
    """LaTeX inline markup -> plain text."""
    s = re.sub(r"\\href\{([^}]*)\}\{([^}]*)\}", lambda m: f"{m.group(2)} ({m.group(1)})", s)
    for _ in range(3):                              # nested \textbf{\texttt{x}}
        s = re.sub(r"\\(?:textbf|textit|texttt|emph|bfseries|normalsize|HUGE|color\{[^}]*\})\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\(?:needspace|vspace|hspace)\{[^}]*\}", "", s)
    s = s.replace("\\hfill", " — ").replace("$\\vert$", "|")
    s = re.sub(r"\\(?:par|noindent|newline|linebreak)\b", "", s)
    # a bare ~ is LaTeX's non-breaking space; \textasciitilde is a real tilde — in that order,
    # or "(\textasciitilde1 GPU-day)" came out as "( 1 GPU-day)"
    s = s.replace("~", " ").replace("\\textasciitilde", "~")
    s = s.replace("\\&", "&").replace("\\%", "%").replace("\\$", "$")
    s = s.replace("\\_", "_").replace("\\#", "#").replace("\\,", " ").replace("\\ ", " ")
    s = s.replace("---", "—").replace("--", "–").replace("\\\\", "\n")
    s = re.sub(r"\\[a-zA-Z]+\*?", "", s)            # any leftover command name
    s = re.sub(r"[{}]", "", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" ?— ?", " — ", s)
    return s.strip()


def items(block):
    """zitemize body -> list of bullet strings."""
    body = re.sub(r"\\(?:begin|end)\{zitemize\}", "", block)
    parts = re.split(r"\\item\s+", body)
    return [detex(p) for p in parts if p.strip()]


def conv_experience(t):
    out = []
    # split on \jobentry
    chunks = re.split(r"(\\jobentry\{[^}]*\}\{[^}]*\}\{[^}]*\})", t)
    for i in range(1, len(chunks), 2):
        m = re.match(r"\\jobentry\{([^}]*)\}\{([^}]*)\}\{([^}]*)\}", chunks[i])
        title, company, dates = (detex(x) for x in m.groups())
        out.append(f"### {title}, {company}\n{dates}\n")
        out.extend(conv_subsections(chunks[i + 1]))
    return "\n".join(out)


def conv_subsections(t):
    out = []
    pieces = re.split(r"(\\subsection\{.*?\})\s*(?=\\begin\{zitemize\})", t, flags=re.S)
    for i in range(1, len(pieces), 2):
        head = detex(re.sub(r"^\\subsection\{(.*)\}$", r"\1", pieces[i].strip(), flags=re.S))
        out.append(f"**{head}**\n")
        blk = re.search(r"\\begin\{zitemize\}.*?\\end\{zitemize\}", pieces[i + 1], re.S)
        if blk:
            out.extend(f"- {b}" for b in items(blk.group(0)))
        out.append("")
    return out


def conv_skills(t):
    out = []
    for l in t.splitlines():
        m = re.match(r"\s*\\skills\{([^}]*)\}\s*(.*)", l)
        if m:
            out.append(f"- {detex(m.group(1))} {detex(m.group(2))}".rstrip())
    return "\n".join(out)


def conv_education(t):
    out = []
    for l in t.splitlines():
        if "\\skills{" in l:
            out.append(f"- {detex(l)}")
    return "\n".join(out)


def conv_bullets(t):
    blk = re.search(r"\\begin\{zitemize\}.*?\\end\{zitemize\}", t, re.S)
    return "\n".join(f"- {b}" for b in items(blk.group(0))) if blk else detex(t)


def conv_paragraph(t):
    return "\n".join(l for l in (detex(x) for x in t.splitlines()) if l)


CONV = {"objective": conv_paragraph, "skills": conv_skills, "experience": conv_experience,
        "projects": lambda t: "\n".join(conv_subsections(t)), "education": conv_education,
        "achievements": conv_bullets}


def header(app_dir):
    # an application folder has resume.tex; the base resume folder has sunil_resume.tex
    main = next((os.path.join(app_dir, f) for f in ("resume.tex", "sunil_resume.tex")
                 if os.path.isfile(os.path.join(app_dir, f))), os.path.join(app_dir, "resume.tex"))
    tex = open(main, encoding="utf-8").read()
    g = lambda k: (re.search(r"\\def\\" + k + r"\{(.*)\}", tex) or [None, ""])[1]
    name = (re.search(r"\\author\{([^}]*)\}", tex) or [None, "Sunil Kumar Sharma"])[1]
    loc = detex(g("location")).replace("|", " | ")
    loc = re.sub(r"\s*\|\s*", " | ", loc)
    return name, g("phone"), g("email"), g("LinkedIn"), g("github"), loc


def expand_acronyms(md):
    seen = set()
    def rep(m):
        w = m.group(0)
        if w in seen or w not in ACRONYMS:
            return w
        seen.add(w)
        return f"{ACRONYMS[w]} ({w})"
    # skip the Skills/Summary title lines? no — first occurrence anywhere is fine.
    return re.sub(r"(?<![\w(/-])(?:" + "|".join(re.escape(k) for k in sorted(ACRONYMS, key=len, reverse=True)) + r")(?![\w)/-])", rep, md)


def convert(app_dir):
    sec = os.path.join(app_dir, "sections")
    name, phone, email, li, gh, loc = header(app_dir)
    obj = strip_preamble(open(os.path.join(sec, "objective.tex"), encoding="utf-8").read())
    title = (re.search(r"\\textbf\{([^}]*)\}", obj) or [None, ""])[1]
    md = [f"# {name}", "", detex(title), "", f"{loc} | {phone} | {email}",
          f"LinkedIn: linkedin.com/in/{li} | GitHub: github.com/{gh}", ""]
    for fn, heading in SECTION_ORDER:
        p = os.path.join(sec, fn + ".tex")
        if not os.path.isfile(p):
            continue
        body = CONV[fn](strip_preamble(open(p, encoding="utf-8").read()))
        md += [f"## {heading}", "", body.strip(), ""]
    text = "\n".join(md)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return expand_acronyms(text)


BASE_MD = "sunil_resume.md"


def base_md():
    """The BASE resume as markdown, RESUME/sunil_resume.md — what every agent that
    needs to know what he has done reads (screening, answer drafting). Rebuilt
    from sections/*.tex whenever any of them is newer, so it never drifts."""
    from jobpilot.core.paths import RESUME
    out = os.path.join(RESUME, BASE_MD)
    srcs = [os.path.join(RESUME, "sections", f) for f in os.listdir(os.path.join(RESUME, "sections"))
            if f.endswith(".tex")] + [os.path.join(RESUME, "sunil_resume.tex")]
    newest = max((os.path.getmtime(s) for s in srcs if os.path.isfile(s)), default=0)
    if not os.path.isfile(out) or os.path.getmtime(out) < newest:
        md = convert(RESUME)
        with open(out, "w", encoding="utf-8") as f:
            f.write("<!-- GENERATED by tex2md.py from sections/*.tex. DO NOT EDIT — edit the .tex -->\n\n" + md)
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("app_dir")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    md = convert(a.app_dir)
    banner = ("<!-- GENERATED by tex2md.py from sections/*.tex at build time. DO NOT EDIT — "
              "edit the .tex files; this file is overwritten on every build. -->\n\n")
    if a.out:
        open(a.out, "w", encoding="utf-8").write(banner + md)
    else:
        print(md)


if __name__ == "__main__":
    main()
