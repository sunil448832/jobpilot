# See step: Claude reads the page as JSON

Status: **planned, not built** (decided 2026-09-25).
Related code: `tests/see_act/` (offline demo of see / act / record / replay),
`src/fill/browser.py` `EXTRACT_JS` (today's code-based reading),
`src/agents/explore/` (the exploring agent).

## Problem

Exploration first has to *see* a page: list every control, its label, the question
it belongs to, whether it is required, and its options. Today that is done by code
(`EXTRACT_JS` in the engine; a regex over Playwright's accessibility snapshot in the
demo). When code misreads a control it does not know it misread anything, so there
is no error to hand to Claude. Claude hears about it only later and indirectly,
as the portal's "field rejected" after Save and Continue.

Seen on 2026-09-25:

| Misread | Effect |
|---|---|
| Adobe's "Have you been employed by Adobe in the past?*" — radios whose only required mark is the `*` | treated as optional and skipped; both Adobe explorations stopped on My Information |
| Ashby's "Gender" radios — no fieldset, question in a sibling label | question lost; the phone was asked "Male" / the field's internal id |

Gaps found by testing the snapshot parser on awkward controls:

- escaped quotes in a name (`I agree "fully"`) kept their backslashes, so acting on the name fails
- radios without a group lose their question (it is only in the text line above)
- `aria-required` without a `*` is reported as optional (the snapshot does not show required)
- roles not listed: `spinbutton` (Workday date boxes), `switch`, `searchbox`, multi-select `listbox`
- a form inside an `iframe` is missing (the snapshot shows only `- iframe`)

Every new portal brings more of these. Patching the parser for each is the wrong
direction.

## Decision

On a page's **second run** — the code's reading led to a stuck page, a rejected
save, or anything that looks wrong — the explore agent does the *see* step itself.
It reads the raw page and returns the controls as **JSON**. No regex or label
matching in code for that page.

- **First pass stays code.** Most pages are read correctly by code; that is free
  and fast. Claude is used only where code failed.
- **Submit stays code.** The replay uses the recipes recorded during exploration
  and never calls a model.

## Design

### Input to the agent

A session command (working name `snapshot`) returns, for the current page:

- the accessibility snapshot of every frame on the page (main document and iframes),
  as Playwright prints it
- the path of a full-page screenshot (the agent opens it with Read)
- the portal's current error messages, if any

### Output: the controls JSON

```json
{
  "step": "My Information",
  "controls": [
    {"role": "combobox", "name": "How Did You Hear About Us?*", "question": "How Did You Hear About Us?",
     "required": true, "kind": "search-and-pick", "value": "", "options": []},
    {"role": "radio", "name": "No", "question": "Have you been employed by Adobe in the past?",
     "required": true, "kind": "radio-group", "value": "", "options": ["Yes", "No"]},
    {"role": "spinbutton", "name": "Month", "question": "From", "group": "Work Experience 1",
     "required": true, "kind": "date-part", "value": ""}
  ]
}
```

Fields: `role` and `name` exactly as the snapshot shows them (they are what `act`
locates by); `question` the text a person reads as the question; `required` from the
`*`, the snapshot, or the screenshot; `kind` the widget (text, search-and-pick,
dropdown, radio-group, checkbox, date-part, file); `frame` when not the main document.

### Menus: see the whole option tree

A snapshot shows only what is on the page, and a menu's options are not there
until it is opened — a category's children not until the category is opened. So a
menu is seen by a loop, **see -> act -> see**:

1. open the menu and read the top-level options;
2. for each option marked as a category (`aria-haspopup`, `aria-expanded`, or a
   chevron), open it and read its children; a leaf is only read, never clicked
   (a click picks it, and a Workday draft keeps the pick);
3. close the menu. The result goes into the question's `options` as a tree:

```json
"options": {"Social Media": {"LinkedIn": null, "Facebook": null},
            "Job Board": {"Indeed": null, "Glassdoor": null},
            "Employee Referral": null, "Company Website": null}
```

Then the stored answer is found by its **path** (`Social Media › LinkedIn`) and
acted on directly; the recipe records the path, so the replay needs no search. A
stored answer not in the tree becomes a placeholder, asked on the phone with every
real choice (`Career Site › Company Website`, ...). A menu that shows nothing
until you type is search-only: search for the stored value instead. Very long
lists (Workday's virtualised 250-country menus) are searched, not mapped.

Worked in the offline demo: `tests/see_act/see_act.py` (`map_menu`, `find_path`,
`leaves`) and `tests/see_act/test_see_act.py`.

### What the code does with it

1. Validates the JSON: every `role` + `name` must resolve to exactly one element
   with `get_by_role` (in its frame). Unresolvable entries go back to the agent.
2. Resolves each `question` to a value — stored facts, learned answers, the
   application's answer map, else a placeholder that becomes a question for the
   applicant. The agent never supplies a value.
3. Acts with the fixed routine for each `kind` and checks the result, as in
   `tests/see_act/see_act.py`.
4. Records a recipe per action (step, role, name, action, value source). The submit
   replay runs these recipes as code.

## Cost

One extra Claude read per stuck page, not per page. A snapshot is a few hundred to
a few thousand tokens; the screenshot only when the agent opens it.

## Open questions

- What counts as "looks wrong" on a page that did save? Candidates: a required
  question with no control, a control with no question, a radio group of one.
- Cache the JSON (and each menu's tree) per portal page, so the next application on
  the same employer's form skips the Claude read and the menu mapping?
- Where the JSON lives: in `replay.json` next to the recipes, or its own file.
