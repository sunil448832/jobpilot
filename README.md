# jobpilot

Finds AI/ML roles, tailors the resume to each, fills the application form, asks for approval
on the phone, submits, and tracks follow-ups and referrals. Runs three times a day; Sunil only
reviews and taps Approve.

423 job boards across 8 ATS platforms · 56 applications submitted (first: OpenAI, 2026-09-05).
The original plan is in [docs/original-plan.md](docs/original-plan.md); how forms are filled
is in [docs/form-agent.md](docs/form-agent.md).

## Three rules

1. **Nothing is submitted without his tap.** The pipeline stops at "filled and waiting"; only an
   Approved card is ever filed.
2. **Nothing untrue goes on a resume or a form.** Tailoring rewords and reorders what his resume
   already says; a related tool or skill is never added (POLICY §2).
3. **LinkedIn is never automated.** Messages are drafted; he sends them.

## A day

| Time (IST) | What runs | What he does |
|---|---|---|
| 04:00 · 12:00 · 20:00 | `jobpilot-daily.timer`: scan → rank → screen → tailor + fill up to 6 roles | nothing |
| 07:00 · 19:00 | `jobpilot-review.timer`: Telegram "N waiting" + link; files every approved card | review on the phone |

On the phone (review site over Tailscale) each card is **Approve**, **Later** (comes back next
time) or **Reject** (dropped). Approved cards are filed at the next 07:00 / 19:00 run, or at once
with `./jobpilot submit`.

## Setup (a new machine)

Everything the tool keeps lives under `~/work`, so an OS reinstall that keeps `~/work` keeps the
secrets (`.env`), the state (`data/`: jobs, Chrome sign-ins, the Tailscale login) and every
application. Only steps 1, 4 and 8 have to be redone.

**1. System packages** (the only step that needs sudo):

```bash
sudo apt install git pandoc texlive-latex-extra texlive-fonts-extra   # pdflatex + .docx build
sudo apt install xvfb          # optional: Chrome fills forms on a hidden display (else off-screen)
```

and Google Chrome (`google-chrome-stable`, the .deb from google.com/chrome): forms are filled in
real Chrome, never Playwright's bundled Chromium.

**2. Python** (3.11+; Miniconda in `~/softwares/miniconda3`, or set `$JOBPILOT_PYTHON`). The
libraries are declared once, in `pyproject.toml` (pyyaml, requests, openpyxl, playwright,
python-docx, beautifulsoup4, claude-agent-sdk):

```bash
~/softwares/miniconda3/bin/python3 -m pip install -e .
```

**3. Resume repo** at `~/work/docs/resume_v2` (`paths.tracking_repo` in `config/config.yaml`, or
`$JOBPILOT_TRACKING`). `./jobpilot paths` shows every location and whether it exists.

**4. Claude CLI**, signed in (`claude`, then `/login`). A standalone install anywhere on the
usual paths, or the one bundled with the VSCode extension: `claude_bin()` in
`src/tailor/autotailor.py` finds either.

**5. `.env`** in the checkout: secrets, one `KEY=value` per line, mode 600, gitignored (the repo
is public: never commit it). Steps 6 and 8 write their keys; add the rest by hand.

| Key | For | Written by |
|---|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | digests, "N waiting", codes asked mid-filing | `./jobpilot setup-telegram` |
| `FORM_TOKEN` | the review site's `?t=` link | the review site, on first start |
| `WORKDAY_EMAIL`, `WORKDAY_PASSWORD` | the Workday account gate (one password used nowhere else) | by hand; without them Workday jobs are skipped |
| `BRAVE_API_KEY` or `GOOGLE_CSE_KEY` + `GOOGLE_CSE_CX` | weekly tenant discovery: web search | by hand, optional |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | weekly tenant discovery: Adzuna | by hand, optional |

**6. Telegram**: create a bot with @BotFather (`/newbot`), then run `./jobpilot setup-telegram`
in a terminal. It asks for the token (hidden), waits for you to tap START on the bot, saves both
keys to `.env` (keeping the others) and sends a confirmation. `--test` sends another.

**7. Tailscale** (the review site from any network): `./jobpilot setup-tailscale`. No sudo and no
system change: the static binaries go to `~/softwares/tailscale`, the daemon runs as you in
userspace-networking mode (no network device; routing, DNS and firewall untouched), and its
state and socket live in `data/tailscale/`. The first run prints a login URL; install the
Tailscale app on the phone with the same account. After a reboot, run it again to start the
daemon (no login). After a reinstall, remove the old machine in the
[admin console](https://login.tailscale.com/admin/machines) so the name stays free.

**8. Review site and timers.** On demand: `~/softwares/miniconda3/bin/python3 -m jobpilot.review.serve`
prints the home Wi-Fi and Tailscale links. `./jobpilot services` (the site) and `./jobpilot start`
(the timers) enable `jobpilot-form.service`, `jobpilot-daily.timer` and `jobpilot-review.timer`
in `~/.config/systemd/user/`. Those unit files are **not in this repo**, so a reinstall loses them
and they have to be written again first.

**9. Check:** `./jobpilot check` (imports, paths, config, a resume build, the tests, the CLI — no
Claude spent) ends in `ALL OK`.

## Commands

```bash
./jobpilot start | stop | status       # scheduler on / off (stop also kills a run in progress)
./jobpilot pipeline [N]                # one full run now, tailoring N roles (default 6)
    --only screen,tailor               #   just those stages (./jobpilot stages lists them)
    --source-limit 200                 #   intake stops at 200 matching postings
    --dry-run                          #   build nothing, fill nothing
./jobpilot log                         # follow the run log (logs/daily.log)
./jobpilot link                        # review-site URL and /questions

./jobpilot queue                       # what waits for approval
./jobpilot submit [N | <slug>]         # file approved cards now (all, at most N, or one)
./jobpilot dryrun <slug>               # a filing that stops before Submit
./jobpilot explore <slug> [--fresh]    # fill a form again (never submits)
./jobpilot add <url>...                # jobs he found himself: tailored first (also /add)
./jobpilot companies                   # applications per company and the limit (also /companies)
./jobpilot follow                      # follow-ups due

./jobpilot refer <slug>                # referral targets for a submitted job
./jobpilot ask <slug>                  # referral message for a new connection, once they accept
./jobpilot referrals | chase           # referral list | follow-ups due
```

`./jobpilot --help` lists the rest (intake, rank, screen, careers, tenants, keywords, ...).

## How a job flows

| Stage | Module | What it does | Claude |
|---|---|---|---|
| intake | `discover/intake.py` | pulls every board in `boards.yaml` in parallel; keeps target titles in target markets; drops "no sponsorship" / "must be based in" JDs and country-locked remote; stores new rows in `state.db` | — |
| hold | `core/quota.py` | parks the postings of a company at its application limit as `held` | — |
| rank | `rank/rank.py` | score = keyword fit + title fit + market bonus (NL/IE/DE/LU/remote 22, Gulf 18, AU/CH 12, US 2) + boosts (pay ≥ ask, sponsorship stated, YC +10) − penalties (8+ years asked, pay < 85% of ask) | — |
| screen | `screen/screen.py` | reads the top 30 JDs: keep / reject + a 0–100 fit score | Sonnet |
| tailor | `tailor/autotailor.py` | builds the resume, scores it against the JD; under 60%, up to 2 tailoring rounds, each undone if it does not raise the score, stuffs keywords or runs past 2 pages | Sonnet |
| explore | `apply/explore_agentic/` | one agent fills the form page by page in real Chrome, records every step in `calls.json`, stops before Submit; a card goes to the phone | Opus |
| draft | `apply/draft/` | 2–3 drafted answers for each question only he can answer | Sonnet |
| review | `review/serve.py` | the phone site: approve, answer, undo | — |
| submit | `apply/explore_agentic/replay.py` | redoes the record by code with his answers; the agent only where a page differs; presses Submit and waits for the portal's confirmation | only if a page differs |
| track | `core/tracker.py`, `outreach/` | follow-up 5 business days later; referral targets and messages | — |

Roles pass rank, screen (keep, fit ≥ 45) and the tailor floor (score ≥ 45) before any Claude
session is spent on them. Links he adds with `./jobpilot add` skip rank and screen.

## Where things live

```
jobpilot/
  jobpilot                     the only entry point
  config/                      edited by hand
    POLICY.md                  who he is, what is true, how to tailor — read by every agent
    answers.yaml               stored facts: what forms get (profile, education, salary per market, ...)
    targets.yaml               which jobs: titles, markets, keywords, rejects, weights
    config.yaml                how the pipeline runs: limits, models, schedule
    boards.yaml                the boards intake pulls
  src/                         core/ discover/ rank/ screen/ tailor/ apply/ review/ outreach/ agents/
  .env                         secrets: Telegram, review link, Workday (gitignored, mode 600)
  data/                        general state: state.db (jobs, referrals), connections.csv, caches,
                               chrome-profile/ (the form filler's sign-ins), tailscale/ (its login)
  applications/<slug>/         one job: JD.md, sections/ (its resume copy), sunil_resume.pdf/.docx,
                               calls.json (the form record), explore.json, cards/ (its cards and
                               screenshots), asks/ (questions its filings left)
  applications/_tenants/       per employer portal: its list picks (below)
  logs/                        daily.log, sessions/<slug>/ (each agent session) — safe to delete
~/work/docs/resume_v2/          the base resume and project notes — read-only for the tool
```

## Facts and answers

- **Stored facts** — `config/answers.yaml`, edited only by him; they win on every form. Includes
  one "No" for every relatives / affiliations / conflict-of-interest question, and sponsorship by
  country: none in India, needed everywhere else.
- **Portal picks** — `applications/_tenants/<tenant>.yaml` (e.g. `workday-crowdstrike.yaml`): his
  picks from that portal's own lists ("How did you hear about us?" → Job Board › LinkedIn), and
  the entry he approved where its list cannot hold a stored fact (no IIT Jodhpur → IIT Delhi).
  Reused on that portal's next jobs only. Written answers, essays and experience answers are
  never kept; a different pick is asked on `/questions`, never overwritten.
- The form agent answers in this order: his answers for this form → stored facts → this
  portal's entries → this portal's picks → otherwise a question on the card.
- Resume uploads: the **PDF** in the resume field; when the form has a second attachment field
  (cover letter, additional documents) the **.docx** there too.

## Tailoring

The tailor session gets the JD keywords the resume misses and decides each one
([src/agents/tailor/prompt.md](src/agents/tailor/prompt.md)):

- **same fact** in other words — use it ("Retrieval-Augmented Generation" for RAG);
- **related, not the same** — never (Triton for vLLM, distillation for quantization,
  "distributed systems" from distributed training, a level like Lead);
- headline = the JD's role at his level, "Senior" at most ("Lead AI Engineer" → "Senior AI Engineer");
- a reworded bullet keeps its kind of work.

A low score from a real gap is reported, not chased. No code adds or removes terms.

## The form and filing

- Exploring: the agent sees each page through `see`, fills it with `act`, presses Next, and ends
  on the last page with `finish`, naming the Submit button. It never presses Submit.
- Filing: code redoes `calls.json` with his approved answers (no Claude while pages match), then
  presses Submit — at most 3 presses — and reads what the portal says:

| Portal shows | Card becomes |
|---|---|
| a confirmation | **Submitted** |
| errors on the form | the agent fixes them and submits again |
| a captcha | **Failed** — nothing sent; finish by hand, then tick *I submitted this by hand* |
| an emailed code asked | asked on `/questions` and Telegram; no answer in time → filed again next run |
| no confirmation, no error | **Sent? — check your email** — never pressed again |
| a refusal (applied already, closed) | **Failed — needs a look** |

- Workday: the account gate (sign in, create account, email verification) is passed by code with
  credentials from `.env` in the checkout (gitignored); Claude never sees them.

## Review site

`jobpilot-form.service` (or `python -m jobpilot.review.serve`), port 8765, on home Wi-Fi and over
Tailscale; every page needs the `FORM_TOKEN` link. Restart it after changing its code.

| Page | Shows |
|---|---|
| `/` | Review now, For later, Approved, Failed, Submitted (10 a page, with Referrals, Referred? and After columns) |
| `/a/<id>` | one card: every value to be submitted, open questions with drafted answers, Approve / Later / Reject / Undo |
| `/questions` | what a filing needs now (a code, a verification) and notices (a captcha) |
| `/companies` | per company: submitted, sent in window, limit, room, next slot |
| `/referrals` | people to contact per role, messages to copy, status buttons |
| `/add` · `/keywords` | paste job links · tick weekly keywords (done / interest / dismiss) |

## Limits

- **Per company: 5 applications in any 30 days** (`apply.default_quota`). A full company is held
  before ranking and its approved cards wait for a slot. OpenAI: 5 per 180 days, full until
  2027-03-28 (its portal refused one).
- **Per run:** 6 roles tailored, at most 3 from one company; 10 cards filed per review run.
- **Intake:** `--source-limit N` stops after N matching postings (about 40 s for 200 instead of
  3.5 min for all), stalest boards first.

## Referrals

- After each submission, 3–4 people at the company: connections from `data/connections.csv`
  first, then engineers from GitHub or paper authors. Each gets a drafted message on `/referrals`.
- **Companies where he knows no one:** he connects without a note; the role's page has a referral
  message to send once they accept (the role, its link, a plain ask, two resume lines matching the
  JD). The Submitted table links "0 people →" to it; *Invitation sent* there tracks the person.
- Statuses: To Contact → Invite Sent → Accepted → Message Sent → Replied → Referred / No Response.
  Each status stamps a 7-day follow-up.

## Lessons from failed submits

1. A clicked button is not a submission — only the portal's confirmation text is.
2. A portal's resume parser rewrites the form: upload first, then read every field again.
3. `page.fill()` lies on React typeaheads: read every value back.
4. Yes / No is often a `<button>`, not an `<input>`.
5. Look at the screenshot before believing a log.
