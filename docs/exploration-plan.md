# Exploration and submit: see / act / replay

Status: **decided 2026-09-26, being built**. Replaces the old `src/fill` engine
(walker, live sessions, hooks, replay recipes by DOM index, Claude submit-resolve
sessions, platform learning). Background: `docs/see-step-claude-json.md`; offline
demo: `tests/see_act/`.

## The flow

**Exploration (pass 1, never submits)** — for every page of the form:

1. **See** — code reads the page's accessibility snapshot (every frame): each
   control's role and name.
2. **Map** — which stored fact answers each control:
   - a **cached map** for this page is applied first (see *Reuse*);
   - the controls it does not cover — and only those — go to **Claude**, which
     returns `[name, kind, fact]` per control (a fact KEY, never a value);
   - the code checks every entry against the live page; failures go back to
     Claude with the exact error, only the failed entries, up to 3 rounds.
3. **Act** — code, one fixed routine per kind (fill, choose, pick, check, press,
   attach). A menu is mapped first (open it and each category: the option tree),
   then the answer is picked by its path. Each action is checked; a failed action
   goes back to Claude like a failed entry.
4. **Placeholder** — a control no stored fact answers gets a placeholder so the
   walk can go on, and becomes a question on the phone with the real choices.
5. **Next** — the platform's next button (Workday: Save and Continue) or the
   page's own; a refused page (the portal's error) goes back to Claude as well.
6. On the last page: screenshot, queue item for approval. Submit is never pressed.

**Approval** — his answers to the placeholder questions are written into the
application's record.

**Submit (pass 2, code only)** — the recorded actions run again with the true
values (placeholders replaced by his answers; submit refused while one is
unanswered), then Submit, then a positive confirmation is required — an error or
refusal banner (OpenAI's application limit) is a failure, not a submission. If a
recorded action fails because the page changed, only that page is re-mapped
(the same see/map/act step), and the replay goes on.

## Where things are kept

- `applications/<slug>/explore.json` — this application's record: per page the
  map, the menu trees, and the actions done (with each value's source: a fact
  key, a placeholder, the resume); the placeholders and his answers; the attempts.
  The submit replays it.
- `data/maps/<platform>/<company>/<page>.json` — the reusable map of a page:
  entries only (name, kind, fact key — no values, safe to reuse across roles).
- `data/maps/<platform>/_standard.json` — for Greenhouse, Lever, Ashby: the
  standard fields every company asks (name, email, phone, resume, location,
  links, EEO), shared across companies.

## Reuse (decisions)

1. **Per page.** The cache key is the page (Workday: the step name; one-page
   forms: the page), not the whole form.
2. **Only questions go to Claude.** On Greenhouse / Lever / Ashby the standard
   fields come from `_standard.json`; only the company's own questions are mapped
   by Claude. On Workday a tenant's pages are reused across its roles.
3. **Verified every time, never trusted blindly.** A cached entry is used only if
   it still resolves on the live page; controls on the page that no cached entry
   covers are counted by code and go to Claude. A cached map is kept until this
   check fails; then the company's map is **replaced** by the corrected one.
4. **Old engine removed**, not kept beside the new one.

## What Claude gets

One page's snapshot, and the facts for THIS job: his answers (answers.yaml,
flattened), his learned answers, and job-derived facts — this market's salary,
whether this job needs sponsorship, previously employed here — computed by the
existing resolver. Other markets' rows are left out (tokens). One turn, no tools,
reply `[[name, kind, fact], ...]`. Prompt and settings: `src/agents/map/`.

## Kept from the old code

The resolver (`autofill.py`: market, stored answers, learned answers), `learn.py`,
`manual.py`, the platform modules' link detection and JD fetch, Workday's account
gate (sign in / create / email verification) and step detection, the browser
setup (persistent profile, Xvfb), dead-posting detection, verification-code entry.
