# The original automation plan (2026-09-05)

Kept as design history. The README describes what is built; where this plan differs, the README is current.

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
