# Operating policy — read before touching any application

Standing instructions for every automated run and every Claude session working on
an application. **If a rule here conflicts with a clever idea, the rule wins.**

Layout (since 2026-09-22): the tool is `~/work/projects/jobpilot` — `src/`,
`config/` (this file lives there, with everything else hand-edited), `data/`, and its outputs `applications/<slug>/` and
`tracking/*.xlsx`. The resume repo, `~/work/docs/sunil_resume_v2`, is **read-only**
for the tool: `resume/sections/*.tex` (the base resume), `project-memory-backup/`,
`target-companies/`. Both trees are passed as `--add-dir`; prompts give absolute
paths. `config.yaml`, `targets.yaml`, `answers.yaml`, `learned.yaml` live in `config/`.

---

## 1. Who this is for — the truth boundary

Sunil Kumar Sharma. Everything below is TRUE and is the outer limit of what may
appear on a resume, a form answer, or a message.

| | |
|---|---|
| Experience | **~5 years total** (career start Jul 2021). Never say 6+. |
| GenAI / agentic / LLM | **3 years** (Aria from Dec 2023) |
| Current | Senior Data Scientist, Aria Intelligent Solutions (Dec 2023–now) |
| Before | Applied Scientist I, **Amazon** (Oct 2022–Jun 2023) |
| Before that | Computer Vision Engineer, Beltech AI (Jul 2021–Sep 2022) |
| Education | **M.Tech in AI, IIT Jodhpur**; B.Tech ECE, SMIT |
| Citizenship | Indian, resident in India. **Needs visa sponsorship outside India.** |
| Notice | 2 months |

**Lead framing:** ex-Amazon Applied Scientist + M.Tech AI from IIT Jodhpur.
**The 2023 gap** (Jun–Dec) is answered, not hidden: a planned break for GenAI/LLM
upskilling plus freelance vision-language work (LLaVA/BLIP fine-tuning).

**What he has is the base resume — `resume/sections/*.tex` — nothing more and nothing
less.** Read it; do not work from a summary of it. It is current (RL post-training
project added 2026-09-22) and every line on it may be used as-is.

**What he does NOT have — never claim:** Apache Spark. Azure. GCP. Databricks
platform. JavaScript / TypeScript. scikit-learn. Kubernetes at depth. Team
management. If a JD demands one, leave it out and let the score be lower — a
resume that wins the keyword match and loses the interview is worse than an
honest one.

### `project-memory-backup/` — last resort, not a second resume
Code-grounded notes on the same work, holding detail the resume omits. **Do not
open them by default.** Tailor from the resume alone first. Open a note only when,
after that, the JD *requires* a specific hard skill or tool the resume has no
wording for, and only to check whether real evidence of it exists — if it does,
add it in the resume's register; if not, leave the gap and let the score be lower.
Never use them to pad a resume that already matches. Files: `project-harmoniq.md`
(Aria), `project-roche-content-tagging.md`, `project-roche-pipeline.md`,
`project-grpo.md` (RL post-training), `project-uls-llm.md` (freelance VLM).

**Attribution boundaries — these bind whether or not a note is opened:**
- HarmonIQ **memory-orchestrator is a teammate's build**; never his.
- Roche pipeline: **he did ONLY the `tests/` work** (TurboQuant eval + migration,
  Qdrant OOM fix, LLM-as-judge). He *compressed and migrated* the retrieval index —
  the resume already says exactly that; never upgrade it to *built*.
- `uls_llm/LLaVA/` is a vendored upstream clone — **never claim authorship of
  LLaVA**; that project has **no accuracy or quality metrics — cite none**.
- **No latency, throughput, traffic or dataset benchmarks exist in HarmonIQ.** The
  resume's "98% success / 500 concurrent sessions" is user-supplied and may be
  used; **never invent new numbers of that kind** or attach a metric to work that
  has none.

### Dated entries
- **Customer-facing work — CLAIMABLE** (confirmed 2026-09-06). Describe it
  concretely from what the resume and notes show; do not stretch it into
  enterprise consulting or pre-sales.
- **Terraform / IaC — CONFIRM BEFORE CLAIMING.** Listed project-wide in
  `project-harmoniq.md`, not attributed to Sunil. Ask him before it goes on a resume.
- **RL post-training — CLAIMABLE since 2026-09-22.** GRPO + verifiable rewards on
  Qwen3-4B is on the resume with its numbers (38.6%→41.0% pass rate, 280 steps,
  QLoRA r=16). Use those numbers; the note `project-grpo.md` has the caveats.

---

## 2. The golden rule

> Tailor ONLY by reordering, re-emphasising, and matching the JD's wording, using
> things already true in `resume/sections/*.tex` — or, for detail the resume omits,
> in `project-memory-backup/` within its attribution boundaries (§1).

Never add a skill, tool, employer, metric, or responsibility that is not already
there. A fabricated resume is a fired employee.

**Allowed:** reorder skills so the JD's priorities come first; use the JD's
vocabulary for a thing he has ("GenAI" if the JD says GenAI); spell out an acronym
he already uses; lead with the most relevant project; put the JD's exact title in
the header.
**`targets.yaml` has three kinds of keyword.** `strong`, `strong_recent`,
`learned` and `confirmed` are things he HAS — `confirmed` being work he ticked as
DONE on the Sunday keyword page that the resume never wrote up (his own
confirmation; usable in the JD's wording, and listed in `tracking/resume_todo.md`
until the base resume catches up). `interest` is things the market asks for that
he ticked to *rank for* — a search signal, never evidence. Nothing in `interest` may
appear on a resume, in a form answer, or in a message; `optimize.py` excludes it
from the truth vocabulary for exactly that reason.

**Forbidden:** any new tool or skill; inflating years; inventing metrics; implying
scope he did not own; claiming management experience.

---

## 3. Tailoring procedure

Every application is `applications/<slug>/` holding **`sections/`, a copy of the
base resume's sections taken at scaffold time**, plus `resume.tex` (imports
`sections/`; its `\location` line is set by code from the role's market) and
`JD.md`. The `.pdf` and the `.docx` are both built from those same `.tex` files —
`ats.md` is *generated* from them on every build and must never be edited.

**Build and score come first.** The untailored copy is built and scored against
the JD. If it already meets `pipeline.ats_target`, nothing is edited and no LLM
session runs. Only a below-target score starts a tailoring session, which gets
the scorer's report: the missing JD keywords, split into SAFE (already true,
word it the JD's way) and NOT SAFE (a new claim — never add).

A session edits **only these four files in `sections/`**, in place:

- **`objective.tex`** — 3–4 lines. Open with the JD's exact job title in bold.
  Name the 3–4 capabilities the JD leads with, in the JD's words. Close with
  M.Tech AI from IIT Jodhpur + Applied Scientist at Amazon. "5+ years" as digits.
- **`skills.tex`** — reorder buckets so the JD's priorities come first; rename
  bucket labels to the JD's framing. Keep the `\skills{Label:} ... \par\vspace{2pt}`
  structure. Every entry already true.
- **`experience.tex`** — reorder bullets within a job so the JD-relevant ones
  lead; reword a bullet into the JD's vocabulary *for the same fact*. Never change
  an employer, title, date or number; never add a bullet for work not already
  there; never drop the `\jobentry` / `zitemize` structure.
- **`projects.tex`** — lead with the most JD-relevant project. Exactly three exist
  (RL post-training with GRPO; VLM fine-tuning; RAG QA bot). Reorder; do not
  invent a fourth.

Not touched: `_header`, `education`, `achievements`, `resume.tex`, `ats.md`.
After a session: `optimize.py --apply` adds SAFE terms to `skills.tex`, the
application is rebuilt and rescored. Up to `pipeline.ats_rounds` rounds; stop
early if the score does not improve or the anti-stuffing penalty leaves zero.

Output filenames are always **`sunil_resume.pdf`** / **`sunil_resume.docx`**.

**Scores:** the target is a target, not a floor. A low score against a JD
demanding Spark or enterprise consulting is CORRECT — report the gap, do not
chase it. The `.docx` and the `.pdf` are the same content by construction; if
they ever differ, the build is broken, not the resume.

---

## 4. Markets, salary, sponsorship

Ordered by how realistically sponsorship happens, **not** by pay:

| Pri | Market | Ask | Reality |
|---|---|---|---|
| 1 | Netherlands | EUR 85,000 + 8% holiday | HSM via recognised sponsor, 2–4 weeks |
| 1 | Remote from India | USD 120,000 (EOR/contract) | No visa, no wait |
| 1 | Ireland | EUR 90,000 | Critical Skills permit; ML on the CSOL → no labour-market test; 4–8 weeks |
| 1 | Germany | EUR 88,000 | EU Blue Card, IT-shortage floor ~EUR 43,760; no LMT |
| 1 | Luxembourg | EUR 98,000 | EU Blue Card, routine — tiny market; watch, don't work it |
| 2 | UAE / Saudi | AED 40,000 / SAR 41,000 per month | Sponsorship is the norm |
| 3 | Australia | AUD 185,000 + super | Subclass 482, approved sponsors only, slower |
| 3 | Switzerland | CHF 155,000 | Best pay, hardest permit: federal quota + must prove no Swiss/EU candidate |
| 4 | USA | USD 200,000 base | H-1B lottery — hardest by far |

- **UK and Canada are out of scope** (removed 2026-09-07) — do not re-add them or
  treat a London/Toronto posting as in scope.
- **Switzerland is high-value, low-volume**: apply only on exceptional fit. Roche and
  Novartis (Basel) are a real door because he shipped production work *for* Roche
  through Aria — never describe it as employment by Roche, and keep the
  content-tagging (his) vs pipeline (tests only) distinction.
- **"Remote – <country>" is not an opening** — it requires residency the visa would
  grant. The intake rejects these; do not re-add one by hand.
- **YC companies get +10.** They sponsor readily in NL and the Gulf and hire
  remote contractors; a US YC role is remote-or-nothing unless the JD says otherwise.
- **Application quotas are scarce.** OpenAI allows **6 per 180 days**; 1 spent
  (Abu Dhabi, 2026-09-05), **5 remain**. The pipeline counts only applications that
  go through its queue. Spend a slot only on the best few of the ~50 OpenAI roles.
- **OpenAI hold lifted 2026-09-22** — the RL project it was waiting on is on the
  resume. The 5 remaining slots still go only to the best few roles.
- **Never quote INR to a foreign employer** — it anchors the negotiation to an
  Indian band. Per-market asks: `answers.yaml:compensation.by_market`.
- **Currency is never a reason to reject** — everything is converted to annual USD
  and judged on value.

---

## 5. Asking the human — form and Telegram

Ask **only what `answers.yaml` / `learned.yaml` cannot answer**; every avoidable
question costs him evening time. When a question is genuinely open:

1. **Draft 2–3 complete answers**, not hints — tap one and done. Each true and
   self-contained.
2. **Vary them meaningfully** (emphasis or tone, not rewording). For a gap question,
   one variant states the gap plainly up front.
3. **Always offer "write my own"** last.
4. **Multi-select → curated combinations**, never raw checkboxes.
5. **Never auto-answer compliance, sanctions or export-control questions** without a
   stored answer — a guess there is a false legal declaration.

Style: plain, specific, no filler. Flag real gaps honestly on the review card ("the
JD centres on Spark, which you do not have" beats a number). Never oversell.
Telegram: role, company, market, ask, field count, what still needs input; link to
the form for anything longer; confirm every decision.

---

## 6. Reusing what he has already answered

`learned.yaml` is the memory: an answer given once is never asked again
(`learn.py` folds submitted forms back in; matching is normalised label → keyword →
fuzzy overlap ≥0.72). **A stored answer beats every heuristic, including the
compliance hard-stop** — that guard prevents guessing; a confirmed answer is not a
guess. Stored answers are his words: never rewrite them silently; surface anything
that looks stale. `answers.yaml` is the structured profile: add durable new facts,
never overwrite a value he set.

---

## 7. Referrals and outreach

**Never automate LinkedIn sending** — no invitations, DMs or connection requests.
Scripts draft; he sends. Sourcing order, always **scoped to the hiring office**:
IIT Jodhpur alumni → ex-Amazon → paper authors (open on their actual paper) → ML
engineers there (GitHub org / active committers) → hiring manager → recruiter.
Rank by shared context and locality; **never by inferred nationality**. ~100
invites/week limit; aim for 5–10 good ones; a bare request often out-accepts one
with a note — message after they accept.

---

## 8. Hard nevers

1. **Never submit an application without human approval.** Filling is automated;
   submitting requires a tap.
2. **Never claim anything untrue** (§1, §2).
3. **Never automate LinkedIn sending** (§7).
4. **Never quote INR** to a foreign employer.
5. **Never auto-answer sanctions / export-control / legal declarations** without a
   stored answer.
6. **Never trust a log over a screenshot.** A clicked button is not a submission.
7. **Never overwrite something he set** in `answers.yaml` or `learned.yaml`.

---

## 9. Reporting

Report honestly, including failures — which stage broke and why. A low score from
a real gap is reported as a real gap. Silence is indistinguishable from success.
