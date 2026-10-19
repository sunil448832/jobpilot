# jobpilot

Finds AI/ML roles, tailors the resume to each, fills the application form, asks for approval
on the phone, submits, and tracks follow-ups and referrals. Scans twice a day; Sunil only
reviews and taps Approve.

520 job boards across 9 ATS platforms plus 5 cross-company feeds · 117 applications submitted
(first: OpenAI, 2026-09-05).
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
| 07:00 · 17:00 | `jobpilot-daily.timer`: discover employers (daily) → scan → rank → screen → tailor + fill up to 20 roles (at most 60% silent on visas, of which at most 3 in the US) | nothing |
| 09:00 · 19:00 | `jobpilot-review.timer`: Telegram "N waiting" + link; files every approved card | review on the phone |

On the phone (review site over Tailscale) each card is **Approve**, **Later** (comes back next
time) or **Reject** (dropped). Approved cards are filed at the next 09:00 / 19:00 run, or at once
with `./jobpilot submit`.

## What he is optimising for

Two goals, weighed equally (his call, 2026-10-19): **a settled status within about 5–6 years**
(PR, a settlement permit, citizenship) and **net savings at least twice what he saves in India**.
Australia serves both and is the first choice; Germany, Ireland and the Netherlands serve the
first; the UAE, Switzerland, USD-paid remote work and the US serve the second. The UK and Canada
are out of scope. All of it is data: `targets.yaml goals` (the weights, how each goal is measured,
the note the screener reads) and a `goal:` block per market (`pr_years`, `savings_x`). Ranking adds
up to 20 points from it, the screener weighs it in FIT, and the run mix caps follow it
(`pipeline.market_max_share`). To chase a different goal, change the weights and the note.

## Setup (a new machine)

Everything the tool keeps lives under `~/work`, so an OS reinstall that keeps `~/work` keeps the
secrets (`.env`), the state (`data/`: jobs, Chrome sign-ins, the Tailscale login) and every
application. Only steps 1, 4 and 8 have to be redone.

**1. System packages** (the only step that needs sudo):

```bash
sudo apt install git pandoc texlive-latex-extra texlive-fonts-extra   # pdflatex + .docx build
sudo apt install xvfb x11-utils   # Chrome fills forms on a hidden display
```

Xvfb is what keeps Chrome out of sight. Without it Chrome asks for an off-screen window, which
an X11 desktop honours and a Wayland desktop (Ubuntu's default) ignores: Chrome opens in front
of you. The run says so in its log, and `./jobpilot check` fails until it is installed.

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
| `WORKDAY_EMAIL`, `WORKDAY_PASSWORD` | the Workday account gate (one password used nowhere else); SAP's careers site (SuccessFactors) signs in with the same pair unless `SUCCESSFACTORS_EMAIL` / `SUCCESSFACTORS_PASSWORD` are set | by hand; without them Workday and SAP jobs are skipped |
| `BRAVE_API_KEY` or `GOOGLE_CSE_KEY` + `GOOGLE_CSE_CX` | employer discovery: web search | by hand, optional |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | employer discovery: Adzuna | by hand, optional |

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

**8. Review site and timers.** The unit files live in `systemd/` in this repo.
`./jobpilot services` installs them into `~/.config/systemd/user/` (user units only, no sudo,
nothing system-wide) and starts the review site (`jobpilot-form.service`) and Tailscale
(`jobpilot-tailscale.service`, so no `setup-tailscale` after a reboot). `./jobpilot start`
turns on the scans and review runs (`jobpilot-daily.timer`, `jobpilot-review.timer`);
`./jobpilot stop` turns them off. The runs are low priority (nice, idle I/O); a run
missed while the laptop was off or asleep starts as soon as it is back. While plugged in, closing the lid does not suspend the laptop
(`jobpilot-lid.service`, a user inhibitor lock held only on AC); on battery it sleeps as before. User units run while he is logged in; to keep them
running after logout, `loginctl enable-linger`.

**9. Check:** `./jobpilot check` (imports, paths, config, a resume build, the tests, the CLI — no
Claude spent) ends in `ALL OK`.

## Commands

```bash
./jobpilot start | stop | status       # scheduler on / off (stop also kills a run in progress)
./jobpilot pipeline [N]                # one full run now, tailoring N roles (default 20)
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
| intake | `discover/intake.py` | pulls every board in `boards.yaml` in parallel plus the cross-company feeds (`aggregators:`); keeps target titles (the `titles` phrases, or an AI word with an IC noun: "Software Engineer II, AI/ML", "Forward Deployed Software Engineer" — managers, architects, consultants, interns and infra roles excluded) in target markets; drops "no sponsorship" / "must be based in" JDs and country-locked remote; stores new rows in `state.db` | — |
| hold | `core/quota.py` | parks the postings of a company at its application limit as `held` | — |
| rank | `rank/rank.py` | score = keyword fit + title fit + market bonus (NL/IE/DE/LU/AU/remote 22, Gulf 18, CH 12, US 2) + goal points (up to 20: PR path and savings, `targets.yaml goals`) + boosts (pay ≥ ask, sponsorship stated, fully remote 18, YC) − penalties (8+ years asked, pay < 85% of ask) | — |
| screen | `screen/screen.py` | reads the top 30 JDs, non-US first, digging to floor 35 until 30 non-US are usable: keep / reject + a 0–100 fit score + the visa tag, with the goals in mind | Sonnet |
| tailor | `tailor/autotailor.py` | builds the resume, scores it against the JD; under 60%, up to 2 tailoring rounds, each undone if it does not raise the score, stuffs keywords or runs past 2 pages | Sonnet |
| explore | `apply/explore_agentic/` | one agent fills the form page by page in real Chrome, records every step in `calls.json`, stops before Submit; a card goes to the phone | Opus |
| draft | `apply/draft/` | 2–3 drafted answers for each question only he can answer | Sonnet |
| review | `review/serve.py` | the phone site: approve, answer, undo | — |
| submit | `apply/explore_agentic/replay.py` | redoes the record by code with his answers; the agent only where a page differs; presses Submit and waits for the portal's confirmation | only if a page differs |
| track | `core/tracker.py`, `outreach/` | follow-up 5 business days later; referral targets and messages | — |

Roles pass rank, screen (keep; fit ≥ 55 in the US, ≥ 50 elsewhere) and the tailor floor (the
pick score 0.7 × fit + 0.3 × rank ≥ 45) before any Claude session is spent on them. A US posting
silent on sponsorship gets one of 3 slots a run; a silent posting by an employer on its market's
sponsor register counts as explicit. Links he adds with `./jobpilot add` skip rank and screen.

## Where things live

```
jobpilot/
  jobpilot                     the only entry point
  config/                      edited by hand
    POLICY.md                  who he is, what is true, how to tailor — read by every agent
    answers.yaml               stored facts: what forms get (profile, education, salary per market, ...)
    targets.yaml               which jobs: titles, markets, keywords, rejects, weights, GOALS
    config.yaml                how the pipeline runs: limits, models, schedule, sponsor registers
    boards.yaml                the boards intake pulls, and the cross-company feeds (aggregators)
    employers.yaml             the employer sweep lists per market (discovery's `sweep` source)
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
- Resume uploads: the resume field gets the **.docx or the PDF, 50/50** — a coin per application,
  fixed for it, so its fill, submit and redrafts agree; a second attachment field (cover letter,
  additional documents) gets the other format. A field that accepts only one format gets that one.

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

- A press that opens a new tab (an aggregator's "Apply now") is followed: the form is there.
- Sites that need an account (Google careers, Talentmate): `./jobpilot signin google` opens the
  filler's own browser profile on the desktop; sign in once by hand and close it — fills reuse the
  session. jobpilot never sees the password.
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
  credentials from `.env` in the checkout (gitignored); Claude never sees them. SAP's careers site
  (SuccessFactors, `platforms/successfactors.py`) likewise: sign in only, never an account created;
  its consent overlay is removed (accepting it reloads the posting) and its "Loading…" after Sign In
  can take over 30 s.

## Review site

Three ways in, all needing the `FORM_TOKEN` link (`./jobpilot link` prints them):
**any network** — `jobpilot-tunnel.service` (src/review/tunnel.py): his fixed ngrok address
(`NGROK_AUTHTOKEN` + `NGROK_DOMAIN` in `.env`, ngrok in `~/softwares/ngrok`; the bookmark — the
first visit in a browser shows ngrok's "You are about to visit" page once), else a Cloudflare
quick tunnel whose address changes on restart and is sent on Telegram; **this laptop** —
`localhost:8765`; **Tailscale** — still running, but the phone does not need it.

`jobpilot-form.service` (or `python -m jobpilot.review.serve`), port 8765, on home Wi-Fi and over
Tailscale; every page needs the `FORM_TOKEN` link. Restart it after changing its code.

| Page | Shows |
|---|---|
| `/` | Review now, For later (5 a page), Approved, Failed, Submitted (10 a page, with Referrals, Referred? and After columns) |
| `/a/<id>` | one card: every value to be submitted, open questions with drafted answers, Approve / Later / Reject / Undo, and **Redraft**: a note ("fill the optional fields", "rephrase it shorter") — the form is filled again with it and the answers redrafted; the new card comes back (Telegram says so). Notes are kept in `applications/<slug>/feedback.json` and apply to later redrafts too |
| `/questions` | what a filing needs now (a code, a verification) and notices (a captcha) |
| `/companies` | per company: submitted, sent in window, limit, room, next slot |
| `/referrals` | people to contact per role, messages to copy, status buttons |
| `/add` · `/keywords` | paste job links · tick weekly keywords (done / interest / dismiss) |

## Limits

- **Per company: 5 applications in any 30 days** (`apply.default_quota`). A full company is held
  before ranking and its approved cards wait for a slot. OpenAI: 5 per 180 days, full until
  2027-03-28 (its portal refused one).
- **Per run:** 20 roles tailored, at most 3 from one company, at most 60% silent on visas (of which
  at most 3 US-silent, `pipeline.explicit_only_silent_max`), and no country above its share
  (`pipeline.market_max_share`: US 30%; DE, NL, IE 40%; UAE, CH 27%; remote 30%; LU 20%; Australia
  uncapped); 20 cards filed per review run.
- **Goals** (`targets.yaml goals`, since 2026-10-19): what he is optimising for on top of sponsorship
  odds — a settled status within reach (`pr_years`) and net savings as a multiple of India
  (`savings_x`), both equally. Every market carries its two facts in its `goal:` block; `goals.weights`
  says how much each counts and `goals.max_points` (20) what the best market earns in rank; the
  screener reads `goals.screener_note`. To chase a different goal, change the weights and the note —
  no code. Australia serves both goals and is priority 1; UAE and Switzerland earn their points on
  savings; the US, UAE and Switzerland score nothing on PR.
- **Sponsor registers** (`config.yaml visa.sponsor_registers`, read by `rank/sponsors.py`, refreshed
  monthly into `data/sponsors.json`; kinds `ind_html`, `dete_xlsx`, `csv`): the IND public register of
  recognised sponsors (NL, ~13,000), Ireland's employment permits issued to companies (~12,700), and
  `data/registers/australia_sponsors.csv` for Australian standard business sponsors (empty until a
  usable list is found: Home Affairs publishes only scanned FOI snapshots). A silent posting by a listed employer counts as **explicit** — Meta, Google,
  Workday, Stripe, Adyen, Booking … rarely say it in the JD. `python -m jobpilot.rank.sponsors --who <name>`.
  The full list is written to `data/sponsoring_companies.csv` (company, country, permits, why it is a
  lead, whether its jobs are already scanned). Discovery's `sponsors` source looks up their job
  boards, largest sponsors first, 120 a run. Switzerland has no register (permits are case by case,
  under quotas): a silent Swiss posting by an employer on any register counts as implicit.
- **Sourcing lists are data:** `config/employers.yaml` holds the sweep list per market (Australia 67,
  UAE 57, NL 27, DE 25, LU 23, CH 22, IE 19); `boards.yaml aggregators:` the cross-company feeds
  (Remotive, Arbeitnow, RemoteOK, We Work Remotely — `json_api` with a field map, or `rss`). Add an
  employer, a feed or a market there; nothing in `src/` names one.
- **Visa:** US roles only when the posting says it sponsors; a silent posting in the Netherlands,
  Ireland, Germany, Luxembourg or the UAE counts as a likely yes. The resume header says only
  "Open to relocation to X" — the forms answer the sponsorship question.
- **Never applied to** (`apply.never_apply` in config.yaml, now Amazon / AWS): intake rejects their
  postings, the pick skips them (links he adds too), the filer refuses their cards; matched on whole
  words from the start of the name. Their careers-site reader is off too (`discovery.deny_boards`).
- **Frontier labs:** at most 2 applications a month each (Anthropic, OpenAI, DeepMind, xAI,
  Mistral, Reflection, Meta, Cohere).
- **Employer discovery** (`tenants.py`: the sweep lists in `config/employers.yaml`, the sponsor
  registers 300 a run, LinkedIn guest search for names, and web search / Adzuna when their keys are
  in `.env`) runs from the scan once a day (`discovery.every_days`), each source within
  `discovery.tenant_budget_s`, the whole run within `discovery.run_timeout_s`; the US is not
  searched (it has the most).
- **Own careers sites** (`custom:` in `boards.yaml`, read by code; the form agent applies on them as
  anywhere): Amazon (amazon.jobs JSON), Google (the results page's embedded data), Microsoft
  (Eightfold JSON — `site: eightfold` works for other Eightfold sites too), and `site: generic` for any
  other site (Apple ...): it finds the listing's job links (rendered in headless Chrome when drawn by
  script) and reads each posting's schema.org JobPosting, else its rendered text. Postings already in
  `state.db` and titles intake would drop are not fetched. Sponsor-register discovery registers an
  employer's own careers site as a generic entry when it finds job links on it, and records every
  careers page in `data/sponsor_careers.json` (the `careers_url` / `platform` columns of the CSV).
  Meta's site is not readable this way (no plain job links).
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
6. A reCAPTCHA "I'm not a robot" checkbox is a captcha even though it is a strip, not a puzzle (BambooHR, Giotto 2026-10-13).
7. Every ATS words its thank-you page its own way: Greenhouse says "We appreciate your application" (Telnyx 2026-10-13).
