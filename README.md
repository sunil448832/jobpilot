# jobpilot — job application pipeline

Finds roles, tailors a resume to each, fills the form, asks for approval on a phone,
submits, and tracks the result. Runs overnight so mornings cost nothing.

Design rationale: the original plan is preserved as the last section of this file.

**19 modules, ~5,500 lines. 203 boards across 7 ATS platforms.**
First real application submitted and confirmed 2026-09-05 (OpenAI Applied AI Engineer,
Abu Dhabi).

---

## Where things live

Two trees, on purpose. The **tool** owns everything it produces; the **resume
repo** is read-only source of truth.

```
~/work/projects/jobpilot/            THE TOOL  (pip install -e .; run stages as python -m jobpilot.<pkg>.<mod>)
  jobpilot                           the only entry point — ./jobpilot <command>
  README.md  pyproject.toml
  src/                               the package (imported as `jobpilot`), one sub-package per stage:
    core/       paths.py config.py daily.py dedupe.py tracker.py      — locations, settings, orchestration
    discover/   intake.py careers.py find_careers.py startups.py       — find roles
    rank/       rank.py keywords.py salary.py keyword_learn.py …       — score a JD against what Sunil has
    screen/     screen.py llm_eval.py                                  — Claude: eligibility + fit score
    tailor/     autotailor.py apply.py optimize.py build.py tex2md.py ats_score.py — per-application work
    fill/       autofill.py browser.py learn.py                        — Playwright fill, submit, learn answers
    review/     serve.py form.py bot.py keyword_form.py referral_form.py — what Sunil sees
    outreach/   referrals.py referral_tracker.py prospects.py outreach.py — referral drafting (never sending)
  config/                            what you edit: POLICY.md config.yaml targets.yaml answers.yaml learned.yaml boards.yaml
  data/                              machine-written: state.db queue/ daily.log connections.csv …
  applications/<slug>/               one application: sections/ (copy of the base), resume.tex, JD.md, built pdf+docx
  tracking/                          job-tracker.xlsx  referral-tracker.xlsx
  scripts/                           one-off setup

~/work/docs/sunil_resume_v2/         THE RESUME REPO — read-only for the tool  (config.yaml -> paths.tracking_repo)
  resume/sections/*.tex              the base resume
  project-memory-backup/             code-grounded notes on real work (last resort, see POLICY §1)
  target-companies/                  market research, hand-written
```

Dependencies between stages point one way — `core` ← `discover` ← `rank` ← `screen` ←
`tailor` ← `fill` ← `review`/`outreach` — and every stage runs as a module
(`python -m jobpilot.tailor.autotailor --limit 2`), so there are no path hacks: `src/core/paths.py`
is the only file that knows where anything is. Each application carries its own `sections/`
copy of the base resume and `resume.tex` imports that locally, so an application is
self-contained and hand-compilable; the `.docx` is derived from the same `.tex` files
(`tex2md.py` → `ats.md` → pandoc), nobody writes `ats.md`. Tailoring is double-gated
(screener fit score, then ATS score) — see *Prepare* below. `./jobpilot paths` prints every root.

Study material is deliberately **not** here — `~/work/study-materials/ai/`.

## The two rules everything else follows

**1. Automate submits on external ATS. Never on LinkedIn.**
There is no account to lose on Greenhouse or Ashby — a bad application is just a
rejection. LinkedIn is the only channel that has converted in four years, and automated
invites and DMs are the most heavily detected behaviour on it. So `outreach.py` writes
messages and a human presses send. Always.

**2. Never claim anything untrue.**
Tailoring is reordering, emphasis, and matching the JD's wording — never a new skill.
`optimize.py` enforces this mechanically: a keyword is added only if it is the same fact
in different words, and new claims are reported, never written.

---

## Architecture

```
 ┌─ DISCOVERY ─ overnight, unattended ────────────────────────────────────┐
 │                                                                        │
 │  careers.py ──► boards.yaml ──► intake.py ──► rank.py ──► state.db     │
 │  fingerprint     203 boards      9,900+        score +      207 live   │
 │  the ATS         7 platforms     postings      route        matches    │
 │       ▲                             │                                  │
 │  startups.py                    salary.py ── any currency → USD,       │
 │  6,203 YC cos                                estimate when unstated    │
 └────────────────────────────────────┼───────────────────────────────────┘
                                      ▼
 ┌─ PREPARE ─ per application ────────────────────────────────────────────┐
 │                                                                        │
 │  apply.py ──► applications/<company>/ ──► Claude tailors ──► build.py  │
 │  fetch JD      JD.md + overrides          (judgment, in-        PDF +  │
 │  detect ATS                                session)             docx   │
 │                       │                                                │
 │                  optimize.py ── add only TRUE keywords, rescore        │
 └───────────────────────┼────────────────────────────────────────────────┘
                         ▼
 ┌─ FILL ─ headed Chrome, persistent profile ─────────────────────────────┐
 │                                                                        │
 │  autofill.py ──► browser.py ──► queue/<id>.json + .png                 │
 │  resolve every    find the ATS iframe, upload CV FIRST, wait for the   │
 │  field from       portal's own parser, re-extract, fill, screenshot.   │
 │  answers.yaml     NEVER submits on this path.                          │
 │  + learned.yaml                                                        │
 └───────────────────────┬────────────────────────────────────────────────┘
                         ▼
 ┌─ APPROVE ─ phone, any network via Tailscale ───────────────────────────┐
 │                                                                        │
 │  serve.py ──► one page: job, flags, every value, open questions        │
 │  (systemd)    with drafted options + "write my own"                    │
 │       │                                                                │
 │       ├── approve/skip ──► queue JSON + Telegram confirmation          │
 │       └── answers ──► learn.py ──► learned.yaml (never asked again)    │
 └───────────────────────┬────────────────────────────────────────────────┘
                         ▼
 ┌─ SUBMIT + TRACK ───────────────────────────────────────────────────────┐
 │                                                                        │
 │  autofill.py --submit-approved ──► re-fill deterministically, click,   │
 │                                    VERIFY, screenshot the confirmation │
 │  tracker.py ──► job-tracker.xlsx + follow-ups at 5 business days       │
 └────────────────────────────────────────────────────────────────────────┘

 ┌─ REFERRALS ─ parallel track, sending always manual ────────────────────┐
 │  referrals.py   who you already know      (LinkedIn CSV export)        │
 │  prospects.py   who to meet, scoped to the hiring office               │
 │  outreach.py    the message, drafted; you press send                   │
 └────────────────────────────────────────────────────────────────────────┘
```

### The loop

```
04:00 / 12:00 / 20:00   scan → rank → tailor → fill → queue     (unattended)
07:00 / 19:00           📱 "N waiting" + link                   (your cue)
                             │
                        review on the phone over Tailscale
                             │
                   Approve ──┴── Later (stays) ── Reject (dropped)
                        │
                   laptop re-fills, submits, verifies
                        │
                   referral targets found automatically → one link
```

---

## Components and their algorithms

### Discovery

**`careers.py`** — *careers-site-first ATS detection*
1. Match the URL against known ATS hosts (greenhouse.io, lever.co, …) — no fetch needed.
2. Otherwise GET the page and fingerprint the markup (`phenompeople`, `hcmRestApi`,
   `myworkdayjobs`, …).
3. For Phenom, **confirm by calling its search endpoint** rather than trusting the
   fingerprint; try the path prefix and the bare host.
4. Write the resolved platform + slug/base into `boards.yaml`.

> Target the company's own careers site, not a job board. It is canonical, and carries
> requisitions no board ever sees. This is how TII and G42 were found after board-slug
> probing missed them entirely — UAE roles went 3 → 8.

`find_careers.py --market <name>` sweeps a curated domain list per country:
`gulf`, `netherlands`, `ireland`, `germany`, `luxembourg`, `switzerland`,
`australia`. The US is deliberately absent — it is oversupplied and H-1B-gated, so
discovery effort spent there displaces reachable markets.

### Pausing the pipeline

```
./jobpilot stop      # timers off, kills anything mid-run, form stays up
./jobpilot start     # timers back on
./jobpilot status    # says ON or OFF outright
./jobpilot pipeline  # one full end-to-end run, in the foreground
```

`stop` is the Claude-usage control. The cost is `autotailor.py`: 2 Claude
sessions per role x 6 roles x 3 scans a day. `stop` also disables the units, so
a reboot does not quietly restart them. It leaves `jobpilot-form.service` up, so
approving already-queued work still functions while the scheduler is off.

Note `jobpilot-daily.timer` is `Persistent=true` — if a scheduled run was missed
while the machine was off, `start` fires the catch-up immediately. That is the
intended behaviour for a laptop that sleeps, but it means `start` is not free.

**`startups.py`** — *YC company sweep*

Postings from YC companies are tagged with their batch in `state.db` and get a **+10
boost** in ranking. The premise splits by market, which is why the boost is modest
rather than large: startups sponsor readily in **NL and the Gulf**, hire
internationally as contractors far more willingly than enterprises (the
**remote-from-India** channel), but in the **US** an early-stage company often cannot
do H-1B at all. The market weighting already handles that, so this only tips
otherwise-equal roles.

1. Fetch the public YC dataset (6,203 companies), cache it.
2. Keep Active + AI/ML-tagged + `team_size >= N` (default 20). Headcount is a filter,
   not a detail: a 5-person seed company cannot sponsor anyone.
3. Derive slug candidates from name, YC slug, and website domain.
4. Probe all six board APIs in parallel; append hits to `boards.yaml`.

**`intake.py`** — *pull, filter, dedupe*
1. Fetch every board in `boards.yaml` (7 platform adapters) plus two open aggregators,
   **in parallel** (`--workers`). This loop was serial and fine at 46 boards; at 296 one
   scan ran past 16 minutes printing nothing, because each board waited on the previous
   board's network round-trip. `discovery.deny_boards` drops slugs that resolve but are
   not employers — `agency` returns 829 *"Freelance AI Trainer Project"* gigs and its req
   count cleared the `enterprise` scale tier, so every gig collected the largest
   company-scale bonus in the ranker.
2. **Title filter** on word boundaries — substring matching let "AI Engineering" satisfy
   "AI Engineer" and pulled in Ruby backend roles.
3. **Market classify** from the location string. Country-locked remote
   (`Remote - US`, `Remote, United Kingdom`, `Remote – Ireland`) is REJECTED **before**
   the location hints run: it means resident-there-already and is unreachable from India.
   The ordering matters — `Remote – Ireland` contains "ireland", so with the hints first
   it would have classified as the Ireland market and collected a priority-1 bonus for a
   role that is the opposite of a sponsorship opening.
4. Hard-reject on JD phrases (`no visa sponsorship`, `must be based in`, …).
5. Dedupe on `company|title|location` into SQLite; only new rows are stored.

**`rank.py`** — *score and route*
```
score = 40 x keyword fit      (GenAI/agentic set weighted 60/40 over core ML)
      + 20 x title fit
      +      market bonus     NL/IE/DE/LU/remote 22 · Gulf 18 · AU/CH 12 · US 2
      +      boosts           sponsorship stated, fully remote, GenAI focus,
                              pay ≥ ask, YC company (+10)

Keyword matching is synonym-aware via `keywords.py`: ~55 canonical terms, each expanded
to acronym/expansion, hyphen/space, plural/gerund and vendor/category forms. A JD saying
"Retrieval-Augmented Generation", "vector store" or "parameter efficient fine tuning"
matches RAG, Qdrant and LoRA respectively.
      -      penalties        asks 8+ years, pay < 85% of ask
```
Market weights are wide on purpose: US postings state the highest salaries and were
collecting the pay boost every time, pushing roles to the top that the H-1B lottery
mostly gates out. A salary you cannot access is worth little. A **max-2-per-company cap**
stops one employer with nine near-identical reqs owning the shortlist.

**`salary.py`** — *currency is never a rejection reason*
1. Parse pay in any currency: symbols, ISO codes, `k`/`m`, **Indian lakh/crore grouping**
   (`45,00,000` is 2-2-3, not 3-3-3).
2. Infer the period from nearby words; fall back to magnitude.
3. Convert to annual USD via live ECB rates (cached daily, static fallback; AED and SAR
   are USD-pegged and held fixed).
4. Compare to the per-market ask from `answers.yaml`.
5. **When pay is unstated**, estimate: median of that company's stated pay *in the same
   market* (medium confidence), else the market band from Sunil's own research (low).
   Estimates move the score at half weight and are always labelled.

> `INR 1,20,00,000/yr = USD 126,998` — beats the remote ask, so it passes. Judged on
> value, never on the symbol.

### Prepare

**`apply.py`** — URL in, buildable application folder out. Detects the portal, pulls the
JD through its native API (Greenhouse `gh_jid` on self-hosted domains included), scaffolds
`applications/<company>/` from `_template`, writes `JD.md` with an autofill route.

**Tailoring is deliberately not automated.** Claude does it in-session so the
never-fabricate rule is enforced by judgment, not a prompt string.

**`optimize.py`** — *raise the ATS score without lying*
1. Build a truth vocabulary from the base resume, `answers.yaml`, `learned.yaml`.
2. For each missing JD keyword, drop extraction noise, then classify:
   - **SAFE** — same fact, different words: an acronym already spelled out, a plural, a
     synonym, or the target job title. Applied automatically.
   - **UNSAFE** — a new claim. Reported, never written.
3. Insert SAFE terms beside the form already on the resume, rebuild, rescore, and warn if
   the anti-stuffing penalty moves off zero.

### Fill

**`autofill.py` + `browser.py`** — the field resolver and the browser half.

Resolution order per label, first match wins:
```
learned.yaml  →  compliance/negation HARD STOP  →  sponsorship & screeners
              →  identity  →  address  →  links  →  availability & pay
              →  EEO  →  else: ask the human
```
Headed real Chrome on a persistent profile, never headless.

**Order matters inside the page too:**
1. Upload the CV **first**, then wait for the portal's own parser to finish and
   **re-extract the DOM** — Ashby rewrites the form when parsing completes, silently
   undoing everything filled before it.
2. Poll for the ATS iframe (up to 20s); a fixed sleep falls back to the site nav and
   fills nothing.
3. Fill, then **read every value back**. `page.fill()` sets `el.value` on a React
   typeahead without re-rendering, so it reports success on a visibly empty field.
   Comboboxes get real keystrokes.
4. Yes/No is often `<button>`, not `<input>`; checkboxes hide under styled overlays, so
   ticking escalates: native check → click the `<label>` → set + dispatch events.
5. Pass 1 has **no submit path at all**. `fill_application()` asserts it.

### Approve

**`serve.py`** — the review form, served from the laptop. No login, because an artifact
with a database is org-internal and always demands one. Answers POST straight back into
the queue file and run `learn.py`. Stable token in `~/.config/jobbot/env`.

**`form.py`** — renders one queue item as one page: header, honest flags, every value
that will be submitted, and each open question with 2-3 drafted answers plus
"write my own". Dual transport: the artifact `db` when hosted, a same-origin POST when
local.

**`learn.py` / `learned.yaml`** — every answer given once is matched back by normalised
label, then keyword, then fuzzy token overlap (≥0.72). Stored answers beat every
heuristic including the compliance hard-stop: that guard exists to prevent *guessing*,
and a confirmed answer is not a guess.

> Effect on one real form: 13 fields / 4 questions → **17 fields / 0 questions**.

**`bot.py`** — Telegram. Card-per-question flow (superseded by the form for bulk review,
kept for notifications), approval gating, persisted update offset and per-update
deduplication so a network timeout cannot replay a tap.

### Submit and track

**`autofill.py --submit-approved`** — acts only on `approved`. Re-fills deterministically
(same script, same data), clicks, then **verifies**: confirmation text present, no visible
submit button, no `aria-invalid` fields. A clicked button is not a submission — that
mistake produced a false "submitted" on a form with five required fields empty.

**`tracker.py`** — syncs `job-tracker.xlsx` using its existing columns and Stage
vocabulary. Submitted → `Applied` with a follow-up **5 business days** out. Idempotent.

**`daily.py`** — intake → rank → tracker → one Telegram digest, non-US first. It never
tailors or fills: filling a form unattended at 2am, with nobody to read the screenshot,
is how a wrong answer gets submitted.

### Referrals

**`referrals.py`** — joins the LinkedIn connections export against companies in the
queue, ranked by role usefulness (works in your field > hiring manager > senior engineer
> engineer > recruiter).

**`prospects.py`** — people you do not know yet, **scoped to the hiring office** (a
referral only carries weight inside the office that owns the req):
1. IIT Jodhpur alumni there — highest accept rate available
2. Ex-Amazon people there
3. **Paper authors via OpenAlex** — named researchers with their actual publications, so
   the opener is specific. Strongest angle for TII (913 recent AI papers), MBZUAI, G42.
4. ML engineers there — GitHub org members, else active commit authors
5. Hiring manager, then recruiter

Ranked by shared context and locality, never inferred nationality.

**`referral_tracker.py` + `referral_form.py`** — the loop from *applied* to *seen*.
On every successful submit it picks 3-4 people at that company (warm from the
connections export, always at least one cold from GitHub or published papers),
drafts a message for each, writes them into `tracking/referral-tracker.xlsx`, and
sends **one Telegram link** to a page listing them all.

The page is the tracker: tap to copy the message, tap to open the profile, tap a
status — which writes straight back to the sheet and stamps a 7-day follow-up.
Status flow: `To Contact → Invite Sent → Accepted → Message Sent → Replied →
Referred | No Response`. The evening digest lists who is due to chase.

```bash
./jobpilot refer <slug>      # build targets for a submitted application
./jobpilot referrals         # list + link to the page
./jobpilot chase             # follow-ups due
```

**`outreach.py`** — drafts the 300-char connection note (with character count), referral
ask, post-accept opener, recruiter note, and paper-author note. Every claim true to the
resume. Sending is manual.

---

## Running it

### Automatic — nothing to do

Two systemd **user** timers, both enabled, with lingering on so they run when you are
logged out and survive reboots. No terminal open, no Claude session, nothing.

| Timer | IST | What happens |
|---|---|---|
| `jobpilot-daily.timer` | **04:00, 12:00, 20:00** | scan every 8h: pull 203 boards → rank → tailor + fill up to 6 roles |
| `jobpilot-review.timer` | **07:00, 19:00** | "N applications waiting" + the link, plus referral follow-ups |

Each scan lands 2–3 hours before a review prompt, so tailoring and filling have
finished by the time you are asked. Up to **18 roles/day**; tune with `--tailor-limit`.

`Persistent=true` means a scan missed because the laptop was asleep runs as soon as it
wakes, rather than being skipped.

**What one scan actually does**

```
jobpilot-daily.timer
  └─ daily.py                          (exclusive lock: two runs never collide)
       ├─ intake.py     203 boards, 7 platforms → filter → dedupe into state.db
       ├─ rank.py       score, route, market-weight, per-company cap
       ├─ autotailor.py for each of the top N roles:
       │                  claude -p  #1  tailor the resume   (reads POLICY.md)
       │                  claude -p  #2  draft answers for open questions
       │                  optimize → build → autofill → queue
       ├─ tracker.py    sync job-tracker.xlsx, follow-ups at 5 business days
       └─ referral_tracker.py --digest
```

`claude -p` runs from `jobs/.auto/` with `Read,Edit,Write,Glob,Grep` and **no Bash**, so
its transcripts stay out of `claude --resume` and it can only edit the application files.
It is the CLI binary, not an interactive session — it runs at 04:00 with nobody logged in.

**It never submits.** Every chain stops at "filled and queued". Submitting needs a tap.

### On demand — a normal terminal

Nothing here needs Claude Code. Plain bash:

```bash
cd ~/work/projects/jobpilot
./jobpilot pipeline 10                     # full run now; tailor 10 roles today (default 6)
./jobpilot pipeline 5 --only screen,tailor # re-run just those stages on what is already in the store
./jobpilot pipeline --dry-run              # walk every stage, build/fill nothing
./jobpilot log                             # follow it live
```

`./jobpilot pipeline` runs in the foreground; the timer runs the same thing silently — use `./jobpilot log`.
`./jobpilot stages` prints the stage names `--only` accepts (dedupe, intake, rank, screen,
tailor, tracker, referrals, digest — always executed in that order, whichever subset you pick).

Each stage on its own:

```bash
./jobpilot intake          # pull all boards (no Claude, no cost)
./jobpilot rank 20         # re-score, show the top 20
./jobpilot scan            # intake + rank in one go
./jobpilot screen          # Claude screens until 10 are usable
./jobpilot prep 3          # tailor + build + fill the top 3   (alias: tailor)
./jobpilot tracker         # sync the xlsx tracker
./jobpilot digest          # Telegram review prompt only
./jobpilot apply <url>     # one job you found yourself
./jobpilot fill <slug>     # re-fill an application that is already built
./jobpilot submit 4        # file at most 4 of the approved items (oldest first); bare `submit` files all
./jobpilot queue           # what is waiting
./jobpilot link            # the review URL
./jobpilot refer <slug>    # referral targets for a submitted application
./jobpilot chase           # referral follow-ups due
./jobpilot status          # services, timer, counts, CLI
```

### Your day

```
04:00  scan + tailor            (asleep)
07:00  📱 "N waiting"           review over breakfast
12:00  scan + tailor            (at work)
19:00  📱 "N waiting"           review in the evening, outside
20:00  scan + tailor            (asleep)
```

Open one link over Tailscale, read, tap. Three outcomes:

- **Approve** — the laptop re-fills and submits, verifying the confirmation
- **Later** — stays in the queue and comes back next time. **Nothing is discarded.**
- **Reject** — dropped permanently, behind a confirmation

Only Reject discards. An application you never got to is still there tomorrow.

### Checking on it

```bash
./jobpilot status
./jobpilot log
journalctl --user -u jobpilot-daily -n 40
systemctl --user list-timers 'jobbot*'
```

Every failure path is loud: a broken stage names itself in the Telegram digest, because
silence is indistinguishable from success.

## Config

**[`POLICY.md`](POLICY.md) is the standing operating policy** — who Sunil is, the truth
boundary, the tailoring procedure, market and salary rules, how to ask him things, how
learned answers are reused, and the hard nevers. Every automated run reads it first.
It exists because the reasoning behind these rules otherwise lives in conversations that
end. **Edit it to change how the scheduler behaves** — do not edit prompts in code.

| file | answers |
|---|---|
| `answers.yaml` | *What do I type into this form?* — contact, per-market salary, visa status, skill-years, EEO |
| `targets.yaml` | *Is this job worth applying to?* — markets, titles, keywords, hard rejects, scoring weights |
| `learned.yaml` | *Have I answered this before?* — grows automatically |
| `boards.yaml` | 203 boards across 7 platforms |
| `connections.csv` | LinkedIn export (not in repo; `install_connections.sh`) |

Resume outputs are always **`sunil_resume.pdf`** / **`sunil_resume.docx`** — a recruiter
sees the filename, and a company-suffixed one reads as machine-generated.

### Growing the alias table from evidence — `mine_keywords.py`

The aliases started as a hand-written list, which only catches synonyms someone
thought of. `mine_keywords.py` mines the ~250 real JDs already in `state.db`:

```bash
python3 src/mine_keywords.py --for rag        # what co-occurs with one keyword
python3 src/mine_keywords.py --title "forward deployed"
python3 src/mine_keywords.py --top 40         # frequent uncovered terms
```

The `--for` mode is the useful one: it ranks co-occurring terms by **lift**, which
surfaces genuine synonyms (`retrieval` 4.6 with RAG, `gpu` 11.8 with vLLM, `loops`
4.8 and `tool` 5.0 with multi-agent). Plain frequency just returns "systems",
"data", "engineering".

It is a DISCOVERY tool, never a scorer — every candidate is reviewed, because an
alias asserts that two phrases mean the same thing. Two high-lift terms were
deliberately **rejected**: `customer-facing` (lift 2.3 on forward-deployed roles) is
a real gap for Sunil rather than a synonym, and `reinforcement`/RLHF (lift 3.7 on
fine-tuning) is work he does not do.

**Result: 1,054 -> 1,512 keyword hits across 251 JDs (+43%)**, from 39 canonical
keywords. The biggest additions by coverage: `customer-facing` (45% of postings),
`RLHF` (19%), `post-training` (18%).

## Tried and rejected

**Embedding-based matching** (`semantic.py`, kept unwired). Dense MiniLM and sparse
SPLADE were both measured against the alias table and both lost. Sparse ranked
"Apache Spark" — Sunil's best-known gap — as the single best-covered probe, above
vLLM serving, while the RAG paraphrase came last. Both score topical similarity when
the question is capability possession; "build data pipelines with Spark" is topically
near-identical to his DuckDB work, which is why it wins. BGE-M3 is the same objective
at 2.2GB and would not separate them either. Full numbers in that file's docstring.

## Lessons that cost a failed submit each

1. **A clicked button is not a submission.** Verify confirmation text; a visible submit
   button or any `aria-invalid` field means FAILURE.
2. **The portal's own resume parser rewrites the form.** Upload first, wait, re-extract.
3. **`page.fill()` lies on React typeaheads.** Read every value back.
4. **Yes/No is often `<button>`, not `<input>`.**
5. **Screenshot and look before believing anything.**

---

# Appendix — the original automation plan (2026-09-05)

Kept as the design rationale. Where it conflicts with the sections above, the sections above are current.

## Job Application Automation — Full Plan

**Goal:** reclaim ~8 hrs/week. Mornings stay free for real work; evenings are phone-only
approval from outside. The desktop does the labour, the phone does the deciding.

**Status:** ALL SIX PHASES BUILT 2026-09-05. First real application submitted and
confirmed (OpenAI Applied AI Engineer, Abu Dhabi).
Decision A resolved: Claude tailors in-session (no API key, no per-job cost).
See [../jobs/README.md](../jobs/README.md) for usage and phase status.

---

### 1. The core split

An asymmetry decides the whole architecture:

| Channel | Automate submit? | Why |
|---|---|---|
| **External ATS** (Greenhouse, Lever, Ashby, Workday) | **Yes** | No account to lose. Worst case = a rejected application. This is the actual pain: re-typing the same details into every portal. |
| **LinkedIn** (Easy Apply, DMs, connection requests) | **No** | Automation risks the one channel that has converted in 4 years. Easy Apply is ~30s on the phone anyway, and hand-sent messages convert better. |

So: the machine applies where it's safe and tedious. The phone handles LinkedIn natively,
where it's risky to automate and pleasant to do by hand.

Supporting evidence (from 4 years of results): ATS-portal applications never got
shortlisted; managed platforms (LinkedIn / Wellfound / Instahyre) did. Evening phone time
therefore lands on the highest-converting activity by design, not by accident.

---

### 2. Architecture

```
                    ┌─ MORNING (unattended, desktop) ────────────────┐
                    │                                                │
  LinkedIn job      │  intake.py ──► rank.py ──► apply.py            │
  alert emails ────►│  (IMAP)        (score,     (tailor + build     │
  Greenhouse/Lever/ │                 route)      PDF + ATS docx)    │
  Ashby board APIs  │                                │               │
                    │                                ▼               │
                    │                          autofill.py           │
                    │                    (Playwright fills the       │
                    │                     external form, screenshots,│
                    │                     STOPS before submit)       │
                    │                                │               │
                    │  referrals.py ─► outreach.py   │               │
                    │  (connections    (draft DMs)   │               │
                    │   CSV match)          │        │               │
                    └───────────────────────┼────────┼───────────────┘
                                            ▼        ▼
                                        queue/*.json + *.png
                                            │
                    ┌───────────────────────▼────────────────────────┐
                    │  bot.py  (long-lived daemon, python-telegram-bot)│
                    └───────────────────────┬────────────────────────┘
                                            │  push
                    ┌─ EVENING (phone, outside) ─────────────────────┐
                    │  Telegram card: role, score, form screenshot   │
                    │  [Approve] [Skip] [Fix field]                  │
                    │     └─ Approve ──► desktop re-fills + submits  │
                    │                    ──► appends to job-tracker  │
                    │                                                │
                    │  Referral cards: tap-copy message ──► LinkedIn │
                    │  app, paste, send by hand                      │
                    └────────────────────────────────────────────────┘
```

#### Why two passes on the form
The morning pass fills and screenshots but does **not** hold a browser open for hours
waiting on approval. On approve (evening), the desktop re-opens and re-fills from the same
deterministic `answers.yaml`, then submits — ~20s, reliable because it is the same script
and the same data. The morning pass is effectively a dry run that proves the form is
fillable and shows exactly what will be sent.

---

### 3. File layout

All new code lives in `jobs/` at the repo root. Existing `applications/` tooling
(`build.py`, `ats_score.py`, `_template/`) is reused, not replaced.

| File | Purpose | Phase |
|---|---|---|
| `jobs/targets.yaml` | Market strategy + discovery filters: 5 sponsorship markets + remote-from-India, titles, keywords, hard reject rules, scoring weights. Read by `intake.py` and `rank.py`. | 1 |
| `jobs/answers.yaml` | Canonical profile: every repeated form field written once — contact, work authorization, notice period, salary expectation, EEO, links, per-question stock answers. Single source of truth for autofill. | 1 |
| `jobs/apply.py` | `apply.py <url>` → fetch JD, detect portal, scaffold `applications/<company>/` from `_template`, fill `JD.md`, build PDF + ATS docx via `build.py`, report `ats_score.py`. | 1 |
| `jobs/autofill.py` | Playwright, **headed** real Chrome with persistent profile. Fills one external application from `answers.yaml`, uploads the tailored resume, screenshots, stops before submit. `--submit` does the second pass. | 2 |
| `jobs/bot.py` | Telegram daemon. Watches `queue/`, pushes approval cards with inline buttons, drives approve/skip/fix, triggers submit, writes results. | 3 |
| `jobs/intake.py` | Passive job discovery: parses LinkedIn saved-search alert emails over IMAP + polls Greenhouse/Lever/Ashby board APIs for target companies. Dedupes against the tracker. | 4 |
| `jobs/rank.py` | Scores each job with `ats_score.py`, routes it: `easy-apply` / `external-mobile` / `external-desktop` (Workday-class) / `drop`. | 4 |
| `jobs/referrals.py` | Joins the LinkedIn connections CSV export against target companies → who you already know, their role, tie strength. | 5 |
| `jobs/prospects.py` | Shortlists NEW people to connect with at a company: builds LinkedIn alumni/ex-employer/title search URLs, plus named candidates from GitHub orgs, paper authors, and the JD's recruiter. Ranked by shared context. See §5b. | 5 |
| `jobs/outreach.py` | Drafts personalised referral / recruiter messages + 300-char connection notes. Draft only — never sends. | 5 |
| `jobs/queue/` | Working state: one JSON + screenshot per pending application. | 2 |
| `jobs/state.db` | SQLite: seen jobs, applied, skipped, follow-up dates. Feeds `job-tracker.xlsx`. | 4 |

---

### 4. Build phases

Each phase is independently useful. Mobile approval — the main want — lands at Phase 3.

#### Phase 0 — Setup (Sunil, ~30 min, blocking)
1. **Telegram bot**: message `@BotFather` → `/newbot` → save the token. Then message the new
   bot once and grab your chat id (`https://api.telegram.org/bot<TOKEN>/getUpdates`).
2. `pip install python-telegram-bot` (only missing dependency).
3. `playwright install chromium` (package present, browser binaries need confirming).
4. **Request LinkedIn data export** — Settings → Data Privacy → *Get a copy of your data* →
   **Connections** → CSV. Takes ~10 min to arrive; needed for Phase 5. Do this early.
5. Fill the `TODO` fields in `answers.yaml` once it exists.
6. *Optional:* Tailscale on laptop + phone, for SSH/GUI fallback when something breaks.

#### Phase 1 — `answers.yaml` + `apply.py`  ✅ DONE
The foundation. Usable immediately by hand: paste an external JD URL, get a tailored,
scored, built resume pair. **Payoff: external application prep drops from ~20 min to ~3.**

#### Phase 2 — `autofill.py`  ✅ DONE
Playwright fills external portals from `answers.yaml` and screenshots. Still desktop-only
and manual. **Payoff: form-filling drops from ~3 min to ~20s.**

#### Phase 3 — `bot.py`  ✅ DONE (built before Phase 2)
The Telegram approval loop. Reordered ahead of Phase 2: the bot is testable immediately
with a synthetic queue item, so the Telegram integration is de-risked early and the phone
loop works before autofill exists. Built dependency-free on `requests` — the planned
`pip install python-telegram-bot` is no longer needed.

#### Phase 4 — `intake.py` + `rank.py`  ✅ DONE
Passive discovery so the queue fills itself overnight. **Payoff: morning cost hits zero.**

#### Phase 5 — `referrals.py` + `prospects.py` + `outreach.py`  ✅ DONE
Referral matching and drafted messages, delivered as tap-to-copy Telegram cards.
**Payoff: the highest-converting activity becomes the easiest one.**

#### Phase 6 — Scheduling + tracker sync  ✅ DONE
Cron for the morning run, auto-append to `job-tracker.xlsx`, follow-up reminders at 5
business days.

---

### 5. One job, end to end

1. **02:00** `intake.py` parses last night's LinkedIn alert email → 14 new roles.
2. `rank.py` scores them → 5 above threshold. Routes: 2 Easy Apply, 2 Greenhouse, 1 Workday.
3. `apply.py` tailors + builds resumes for the 3 external roles.
4. `autofill.py` fills the 2 Greenhouse forms, screenshots each. Workday goes to the
   desktop queue (fragile, and it's the dead channel).
5. `referrals.py` finds a 1st-degree connection at one company; `outreach.py` drafts the ask.
6. **19:30, outside, phone.** Telegram has 5 cards. Two form screenshots → tap Approve,
   desktop submits. One referral card → tap copy, paste into LinkedIn, send. Two Easy
   Applies → tap through in the LinkedIn app.
7. **Elapsed: ~6 minutes.** Everything lands in `job-tracker.xlsx` with follow-up dates set.

---

### 5b. Finding people to connect with (no existing connection)

`referrals.py` only covers people already in the network. For companies where Sunil knows
nobody, `prospects.py` shortlists new people to send connection requests to.

**Sourcing rule: never scrape LinkedIn people-search.** It is the most heavily detected
behaviour on the platform. Two legitimate routes instead:

#### Route 1 — build the URL, let LinkedIn do the search (primary)
LinkedIn's own tools beat any scraper because they apply the network graph. `prospects.py`
constructs these per target company and sends them as tappable Telegram links:
- **Alumni filter** — `linkedin.com/school/<school>/people/` filtered by company →
  IIT Jodhpur alumni at the target. Highest accept rate available.
- **Ex-employer filter** — same on Amazon's company page → shared-employer angle.
- **Company people search** filtered by title (e.g. "Machine Learning Engineer").

#### Route 2 — named candidates from public, non-LinkedIn sources
- **The job posting** — Greenhouse / Lever JSON often names the recruiter or hiring manager.
- **GitHub** — `github.com/orgs/<company>/people` + commit authors on company repos.
  High-signal for AI/ML targets: real engineers with visible evidence of their work.
- **Papers** — Semantic Scholar API filtered by author affiliation. A technical comment on
  someone's paper is the highest-converting cold outreach there is, and it plays to the
  M.Tech-in-AI / ex-Amazon background.
- **Team pages, conference speaker lists.**
- *Optional:* Apollo.io / RocketReach free tiers return LinkedIn URLs without touching
  LinkedIn. Usable, but weaker than the above for AI/ML roles.

#### Ranking (accept-likelihood x referral value)
1. IIT Jodhpur alum at the company
2. Ex-Amazon
3. Engineer **on the hiring team** — beats a recruiter: referral bonus + internal weight
4. Hiring manager for the specific role
5. Recruiter
Skip VPs / C-level: they will not accept and it burns invite quota.

#### Platform limits that shape the design
- **~100 invites/week**, and a high ignore-rate triggers "withdraw your invitations"
  warnings. Target **5-10 well-chosen per week**, never bulk.
- **Connection notes cap at 300 chars**; free accounts have had monthly limits on
  *personalised* invites (verify current state before relying on notes).
- **A bare request often out-accepts one with a note** — then message after acceptance.
  `outreach.py` should draft both paths: a 300-char note, and a post-accept opener.

#### Delivery
Telegram card per candidate: name, role, **why them** (the shared-context reason), tap-copy
note, tap-open profile URL. Sending stays manual, always.

---

### 6. Open decisions

**A. Who does the tailoring judgment in the unattended run?**
`apply.py` can scaffold and score without an LLM, but choosing which project leads and how
to reorder skills needs judgment — and the golden rule (never fabricate) has to hold.
- *Option 1 (recommended):* a scheduled `claude -p` run does the tailoring in-session. No
  API key, no per-job cost, and the no-fabrication rule is enforced by judgment rather than
  by a prompt.
- *Option 2:* Anthropic API call from inside `apply.py`. Needs a key, costs a few cents per
  job, simpler to schedule.

**B. Submit confirmation depth.** Screenshot-only, or also a diff of every field value in
the card text? Screenshot is faster to scan on a phone; the field list is safer for
screening questions. Probably: screenshot + flag any field the script was unsure about.

**C. Prospect sourcing depth.** Route 1 (LinkedIn search URLs) alone is free and zero-risk
and probably covers 80% of the value. Route 2 (GitHub / papers / recruiter extraction) is
more build effort but produces *named* people with a real reason to reach out. Start with
Route 1, add Route 2 only if accept rates disappoint?

**D. Workday.** Build fragile automation for it, or formally drop the channel? Given 4
years of zero conversions, dropping is defensible and saves the most maintenance.

---

### 7. Risks and caveats

- **Machine must be awake** for the morning run and for evening submits. Sunil is leaving
  the system on.
- **Playwright selectors break** when portals redesign. Greenhouse / Lever / Ashby are
  stable and simple. Workday is the fragile one — different tenant configs, session
  timeouts, occasional bot checks even with a real Chrome profile.
- **A wrong auto-filled answer can be submitted.** The screenshot approval step is the only
  thing preventing this — do not skip it later for speed.
- **Never automate LinkedIn sending.** Connection requests and DMs are the most heavily
  detected behaviours; a restriction would cost the only channel that has worked.
- **Truthfulness rule carries over unchanged:** tailoring is reordering, emphasis, and
  keyword-matching only. Never invent experience, tools, or metrics.

---

### 8. Environment (verified 2026-09-05)

Present: `playwright`, `yaml`, `requests`, `bs4`, `openpyxl`, `docx`, `pandoc`, `pdflatex`,
`google-chrome`, `chromium`, Python 3.12.2.
Missing: `python-telegram-bot`. Unconfirmed: Playwright browser binaries.
