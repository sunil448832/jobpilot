# agents/ — every Claude session the pipeline starts

One folder per agent. The code only fills in the blanks and runs it
(`src/core/agents.py`); what the agent is told and what it may use live here.

| agent | runs when | may write |
|---|---|---|
| `screen/` | screening scraped roles (keep / reject + fit) | nothing |
| `tailor/` | a role's resume scores below target | the application's `sections/*.tex` |
| `draft_answers/` | an exploration left questions without a stored answer | that item's questions file |
| `explore/` | every exploration of a form (live browser session) | the application's `hooks.py`, and its `answer_map.yaml` through `map` |
| `platform_learn/` | a platform with no module could not be walked | the platform module, the application's hooks |
| `submit_resolve/` | the code replay of an approved submit did not go through | the application's `hooks.py` |

Each folder:

- `prompt.md` — the prompt. `{name}` is filled in by the code; any other brace
  (JSON, LaTeX) is left as written.
- `tools.yaml` — how it runs: `llm` (the `config.yaml` `llm.<key>` giving model
  and effort), `max_turns` (`true` = `llm.max_turns`), `timeout` (seconds or a
  config key), `add_dirs`, `allowed_tools`, and `params` — the blanks the prompt
  needs; a missing one stops the run instead of sending a prompt with a hole.
- other `*.md` — parts the code chooses between: `explore/toolkit_<platform>.md`
  (helper signatures + a worked example for that platform's hooks; `generic`
  otherwise), `platform_learn/situation_<mode>.md`, `targets_<mode>.md`.

Rules every agent is given, and the code enforces where it can: never submit
(except `submit_resolve`, capped presses), never invent an answer (values come
from the resolver, stored facts, or the applicant), never handle credentials.
An exploring or submit session may not change anything under `src/` — this
folder included: `src/` is snapshotted before the session and restored after it.
