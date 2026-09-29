# The form agent: exploring and filing applications

Code: `src/apply/explore_agentic/`. Commands: `./jobpilot explore <slug>` (explore, never
submits; resumes from the record, `--fresh` starts over), `./jobpilot submit [N]` (file what he
approved, oldest first), `./jobpilot submit <slug>` (one job), `./jobpilot dryrun <slug>` (a
filing that stops before Submit), `./jobpilot link` (the review list and `/questions`). The
tailoring stage runs the explore for every role it prepares; the review run files approved cards.

```
posting ──► EXPLORE: one Claude agent, tools on the live page ──► calls.json (the record)
                                                               └─► card on the phone (+ Telegram)
                          he answers, approves on the phone
                                        │
            FILE: code redoes calls.json, page by page ──► the agent only where a page differs
                                        │
                     code presses Submit ──► watch: submitted / not accepted (agent fixes,
                                                ≤ 3 presses) / refused / captcha / code-needed /
                                                unclear
```

## 1. Exploring (`session.py`, `form.py`, `prompt.md`)

One Claude Agent SDK session per application, started once. Its system prompt is sent once:
`prompt.md` (how to work, the answer grammar, the rules), his approved answers for this form,
and every fact (`facts.py`: answers.yaml flattened, learned answers, this job's market,
`job.today`). What its tools return is its observation; it works in any order it likes.
Model and effort: `llm.form_agent` in config.

| tool | what it does |
|---|---|
| `see(scope)` | the page, a section, an outline or given ids: each control with a lasting id (`c7`), what its HTML says, what it shows, a list's choices |
| `act(rows)` | only these rows; reports what took and what else changed on the page |
| `options(id)` / `search(id, patterns)` | a list read whole / searched; nothing picked |
| `clear(id)` / `inspect(id)` | one field emptied / its HTML |
| `press(id)` | a page button (Apply, Next, a block's Delete); never one that sends |
| `finish(outcome, note, submit_id)` | the end; names the Submit button (recorded, never pressed) |
| `replay()` | only when a record exists (see 2) |
| `submit(id)` | only when filing (see 3) |

A row is `[id, write|select, answer]`, optionally with a 4th item (see 4). An answer is a fact
key, `option:<choice or a › b chain>`, `guess:<top-5 candidates> | <question>` (the first
stands in; the question goes to the phone), `search:<regex>; …`, `text:<his answer>`,
`file:resume`, `add:<n>`, `keep:<answer>` (already shown), or the question itself (a
placeholder). Page buttons are not rows: they are pressed with `press`.

Rules the prompt holds: history is never removed (employment, education); a school or degree
a list does not hold is "Other" or a guess, never another school; identity questions only from
his own facts; work authorization by the job's own market; website links a portal rejects as
duplicates are removed (the portal read them from the resume); signing in is not the agent's.

**Sign-in** is done by code (`platforms/workday.py`), with credentials from
`~/.config/jobbot/env` that the agent never sees: sign in, or create the account; an email
verification, a refused password, a locked account or a missing account form is asked on
`/questions` (he fixes the account himself and answers *done* / *skip*; never a password asked,
never a second account, no second try in a session after a refusal).

**At the end** the placeholders go into `applications/<slug>/explore.json` (his earlier answers
kept), the card into `data/queue/<id>.json` (card.py) with the last page's screenshot, and a
Telegram message says a new card is ready (with the card's and the review list's links). A
re-exploration supersedes the job's unsent cards.

## 2. The record and resuming (`calls.py`)

Every tool call goes through `Form.call` and is saved at once into
`applications/<slug>/calls.json`: the tool, its arguments, the page, the lasting identity of
each control it names (role, name, which one of that name, its group, the question above it),
and what it did (each act row's outcome, the value read back, the technique). A session
stopped anywhere resumes: with a record, the agent gets `replay` and `resume.md` and starts
there. The record keeps the former record's pages a session has not reached (a page a portal
shows only some days, like Workday's *Start Your Application* before a draft exists).

**Redo** (`Redo.forward`) — from the page shown, page after page: each page's recorded act /
clear / press, each control found again by its identity inside its own block (a Delete in
*Certifications 1*, never "the 6th Delete"); then a check that every field holds what it held;
then the page's recorded Next. It stops at a page that differs, one the record does not know,
a Next that does not move on, or the record's last page (a Review with no calls counts).

- a placeholder takes his approved answer (`text:` / `option:`); a Yes/No or radio placeholder
  presses the choice his answer names (the nearest control with that label)
- a row that only searched, a page button row, and a row that failed during exploration are
  skipped; a row whose answer is one of his approved questions takes his answer
- an Add is redone only for the numbered blocks still missing (`Websites 1, 2, …`); a Delete
  only while its section differs from what the press left
- a control is "not on the page" only if it is still missing once the page is redone; a call
  that found nothing is not recorded again

## 3. Filing (`replay.py`, `form.submit`, `filing.md`)

`./jobpilot submit` files approved cards, oldest first (`replay.py <slug> --submit` one job;
`<slug>` alone is a dry run that stops before Submit).

**Before a browser opens**: the card approved and never pressed before; approved after the
exploration it would file; every placeholder answered; the record names a Submit button; the
application's record shows no submitted or unconfirmed attempt. Otherwise it is skipped.

**Redo** by code (no Claude, no tokens while the pages are as recorded); where it stops, the
agent resumes that page with every tool and `replay` to go on. On the last page code presses
the recorded Submit. The press is written to the card first, so a rerun never presses blind.

**Watch** — only text that appeared after the press counts (a standing "application limits"
banner is not a refusal):

| outcome | when | then |
|---|---|---|
| submitted | a confirmation ("Thank you for applying", "Application Submitted", …) | card submitted; the outcome to Telegram; referral targets looked up |
| not accepted | errors that stay on the form, or sent back to one of the form's own steps with fields — after a last look for a confirmation | the agent fixes what the page says and submits again (`submit` tool, code-checked); at most 3 presses in all |
| refused | "already applied", an application limit, … | failed; never pressed again |
| captcha | a captcha frame after Submit | not sent; a notice on `/questions` with the job's link to finish by hand; the press does not count |
| code-needed | an emailed code asked for (on every poll) and none came | not sent; stays approved, filed again later |
| unclear | none of these | card *unconfirmed*: never pressed again; he checks his email |

The fix agent (`filing.md`) may fill a missed required field from his facts or answers, fix a
format, redo a page it was sent back to; it never changes the meaning of his answer and never
invents one (a new question stops the filing for his answer).

## 4. Acting on a control (`act.py`, `see.py`, `controls.py`)

`see.py` reads the accessibility snapshot (`controls.py` parses it) and each control's HTML;
the routine for a control comes from that HTML (tick, select, list, digits, type, press,
file), never from its name. Each routine has techniques in order, tried by `attempt()` until a
check says the control holds what was wanted; a technique that raises or does not take gives
way to the next:

| control | techniques |
|---|---|
| text box | set in one step · key by key · by script (input / change events) |
| tick box / radio | the drawn control that disagrees · real click · its label · set checked · forced · script click (never undoing what took); done only when the input and any control drawn for it (`aria-checked`, `data-state`) agree |
| Yes/No button | click · forced · script |
| `<select>` | by label · by matching text · keyboard |
| list | its entries clicked (a nested chain step by step) · the entry typed and picked from the suggestions |
| date / number part | keys · slower keys · fill · script (checked by `aria-valuenow`) |
| file | its input · the file chooser |

- The technique that worked is remembered per platform (`data/techniques.json`) and tried first
  there next time. A control that still does not take is reported with what was tried; the
  agent may name another as a row's 4th item (`technique:<name>`) or give its own keys
  (`keys:12{Tab}2023`).
- A type-ahead that draws its suggestions as plain elements in its field (Lever's location) is
  read too; the suggestion that is the value, or starts with it, is picked; a box that drops
  the value when left is typed again key by key, then reported.
- The box is found again before every read, so a box that redraws while typed (Workday) is
  never waited on.
- **Dates**: after every act all date parts are read again. On Workday typing the year puts its
  first key in the month (12 → 2), so a date a later part undid is typed in one go into its
  first part (the widget moving on). Parts are grouped by date: next to each other in one group,
  a second part of the same kind (a second MM) beginning the next date.

## 5. Questions during filing (`review/ask.py`, `/questions`)

What a filing needs from him at that moment — an emailed code, a Workday email verification, a
sign-in fix — is written to `data/asks/<key>.json` and shown on its own page, `/questions`:
waiting ones first with an answer box and the time the wait ends (15 min for a code, 30 for a
sign-in fix), then notices (a captcha, a filing the agent could not resolve, with the job's
link), then the answered and expired ones of the last 7 days. Each new one sends Telegram a
short message with that page's link and the review list's.

## 6. The review list (`review/serve.py`)

Sections: Failed (with a tick *I submitted this by hand*), Sent? — check your email
(unconfirmed, same tick), Review now, Apply by hand, For later, Filing now, Approved and
Submitted. Approved is a table (date approved, company, role, portal, where its filing stands:
ready / explore first / its last try). Submitted is a table, newest first (date, company, role,
filed or ✋ by hand, **Referrals** — how many people could refer him, linking to that role's own
referral page — and **Referred?** — ✅ with the name, else how far it got). The card's list of
values is what the filing will enter, from the record (`calls.values_by_page`).

## 7. What keeps a filing safe

- nothing is sent without his approval of a card made from the record being filed
- a Submit that may have gone through is never pressed again: the press is recorded before the
  click; unclear → *unconfirmed*; the queue dedupe never removes a sent or unconfirmed card, and
  filing refuses a job whose record shows one
- only a confirmation the portal shows after the press counts as submitted
- his answers keep their meaning; a question nobody answered stops the filing
- credentials never reach the agent; the agent never types into a sign-in form
- per-portal application caps (`apply.quotas`, counting sent and maybe-sent cards; a portal's own
  limit refusal blocks the company until its window passes), and a posting picked once per batch

## 8. Files

| file | what |
|---|---|
| `applications/<slug>/calls.json` | the record: every tool call (redone at filing) |
| `applications/<slug>/explore.json` | placeholders, his answers, submit attempts |
| `applications/<slug>/agentic.json` | the last exploration: outcome, pages, the Submit button |
| `applications/<slug>/filing_agentic.json` | the last filing: redo, presses, agent calls, cost |
| `data/queue/<id>.json` / `.png` | the card and its screenshots |
| `data/asks/<key>.json` | questions and notices from filings |
| `data/techniques.json` | which technique works on which platform |
| `tests/maps/<slug>/` | each session's log and system prompt (review) |

## 9. Known limits

- Lever shows a captcha on Submit: filed by hand (the notice links the job).
- A date widget whose parts do not move on by themselves, or a calendar picker, is not typed
  as one date; the check reports it and the agent can give its own keys.
- A portal's own limits (OpenAI: 5 applications in 180 days) refuse a filing; nothing is sent,
  and the company is blocked for tailoring until the window has passed
  (`data/quota_blocks.json`), since the portal also counts applications made outside jobpilot.
