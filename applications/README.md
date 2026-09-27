# Tailored resumes per company

Base resume in the resume repo's `resume/sections/` (see `src/jobpilot/core/paths.py`) is the **source of truth**. Each company gets a
folder here that overrides only what it needs, and builds two outputs:

- `sunil_resume_<company>.pdf` — polished LaTeX, for **humans**: LinkedIn, Wellfound, Instahyre, recruiters, email.
- `sunil_resume_<company>_ATS.docx` — plain single-column, for **ATS portals**: Workday, Greenhouse, Taleo, iCIMS.

## Why two formats
Managed platforms (LinkedIn / Wellfound / Instahyre) put a human on your rich
profile — the styled PDF shines there. ATS portals parse a resume by machine and
filter on keywords, location, and years; a LaTeX PDF parses badly, so those get a
clean `.docx` with standard headings and spelled-out keywords.

## Add a new company
```bash
cp -r _template <company>            # e.g. cp -r _template stripe
# edit <company>/objective.tex  -> lead with the JD's exact job title
# edit <company>/skills.tex     -> reorder so JD keywords come first
# edit <company>/projects.tex   -> lead with the most JD-relevant project
# edit <company>/ats.md         -> mirror the same tailoring (this becomes the .docx)
# edit <company>/resume.tex     -> set the location / visa line for the role
# fill  <company>/JD.md and <company>/notes.md for the record
python build.py <company>
```

## Check the ATS match score
`ats_score.py` scores a resume against a JD the way 2026 ATS / Jobscan-style tools do:
weighted keyword overlap (hard skills > job title > education-if-required > soft skills
> other), synonym/acronym aware (RAG == Retrieval-Augmented Generation), a TF-IDF
cosine "semantic" second opinion, an anti-stuffing penalty (over-repetition LOWERS the
score, like Workday's 2026 density flag), plus parseability and years/degree checks.

```bash
python ats_score.py --company capital-one          # scores <company>/ats.md vs <company>/JD.md
python ats_score.py --resume x/ats.md --jd jd.txt  # ad-hoc
python ats_score.py --company capital-one --json    # machine-readable
```
Aim for **75%+**. It prints the missing HARD skills to add — add only ones that are TRUE.
For the truest semantic number, put the **full JD prose** in `JD.md` (not just the meta bullets).
It is an approximation, not the employer's real ATS.

## Golden rule
Tailor by **reordering, emphasizing, and matching the JD's wording** — using only
things that are already TRUE on the base resume. Never invent experience, tools,
or metrics.

## Files in each company folder
| file | purpose |
|------|---------|
| `resume.tex` | wires base sections + local overrides; holds contact + location line |
| `objective.tex` | tailored summary (local override) |
| `skills.tex` | tailored/reordered skills (local override) |
| `projects.tex` | tailored project order (local override) |
| `ats.md` | ATS-clean source -> `.docx` via pandoc |
| `JD.md` | the job description + meta (platform, visa, salary, link) |
| `notes.md` | keywords mirrored, what changed, fit/gaps, follow-up |
| `explore.json` | what the exploration did, page by page — what submit replays (machine-written, not versioned) |
| `pages/` | each form page as it was seen, for offline replay of its mapping (machine-written, not versioned) |
| `hooks.py`, `replay.json` | the old fill engine's per-application code and record; the new engine does not read them |

Everything not overridden (education, experience, certifications, achievements)
comes from the resume repo via the `\BASE` macro automatically, so base edits flow to every company.
