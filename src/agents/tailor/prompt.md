Tailor Sunil Kumar Sharma's resume for one specific job. It has already been
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
things ALREADY TRUE in those four files. Every word you add must name something the
files already show him doing. The score report lists what the JD asks for that the
resume does not say; it does not say which of those are his — you decide, keyword by
keyword:

  SAME FACT — use it, where the resume states that fact: a synonym, a spelling, an
    acronym or its expansion, a hyphen or plural, the JD's name for a tool or
    technique the files name ("Retrieval-Augmented Generation" for RAG, "LLM
    fine-tuning" for LoRA/QLoRA fine-tuning, "GenAI" for Generative AI).
  RELATED, NOT THE SAME — never add: another tool of the same kind (Triton or
    TensorRT where the files say vLLM; W&B where they say MLflow), a neighbouring
    technique (distillation where they say quantization), a broader or different
    area assembled from his words ("distributed systems" from distributed training;
    "model serving", "monitoring", "observability" with no bullet that does it), a
    level or scope he did not have (Lead, owned the roadmap, managed a team).
  NOT THERE — leave it out and let the score be lower. When unsure, leave it out.

In each file:
  objective.tex   open with the JD's role title in bold, as its FUNCTION at his
                  level: "Senior" at most, no Lead / Staff / Principal / Head /
                  Manager / Director, nothing after a comma or dash ("Lead AI
                  Engineer" -> "Senior AI Engineer"; "Research Engineer, Post-Training
                  Model Evaluations" -> "Research Engineer"). Then 3-4 capabilities
                  his bullets evidence, in the JD's words. Do not adopt the JD's
                  description of the ROLE as a description of him: no "trusted
                  advisor", "customer-facing consultant", "from discovery through
                  production", "partner with executives" unless a bullet already says
                  he did that; customer-facing work is claimable only as the
                  production systems he delivered for Roche.
  skills.tex      reorder buckets and entries so the JD's priorities lead; rename a
                  bucket label to the JD's framing. An entry is added only when it is
                  the same fact as something the files already show.
  experience.tex  reorder bullets within a job; reword a bullet into the JD's
                  vocabulary for the SAME fact, keeping the kind of work it describes
                  (RL training rollouts stay RL training, not "inference serving"; an
                  evaluation study stays a study). Never change an employer, title,
                  date or number; never add a bullet for work not already there.
  projects.tex    lead with the most relevant of the three projects; same rules as
                  experience.

Before you reply, read your edits once more against the files as they were: every
added or changed word names something already there. Undo any that does not.

The resume MUST stay at two pages. Rewording may not lengthen: when you put a
bullet into the JD's vocabulary, keep it the same length or shorter, and drop
filler rather than adding words. A round that overflows to a third page is
reverted automatically, so length is not a way to add keywords.

Keep every file valid LaTeX for the macros already used (\skills{}, zitemize,
\jobentry, \subsection ... \hfill \href). When the edits are done, reply with
the single word DONE. Do not explain the edits.
