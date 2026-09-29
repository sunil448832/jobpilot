# agents/ — the Claude CLI sessions the pipeline starts

One folder per agent. The code only fills in the blanks and runs it
(`src/core/agents.py`, through the `claude` CLI); what the agent is told and what it may
use live here.

| agent | runs when | may write |
|---|---|---|
| `screen/` | screening scraped roles (keep / reject + fit) | nothing |
| `tailor/` | a role's resume scores below target | the application's `sections/*.tex` |
| `draft_answers/` | an exploration left questions without a stored answer | that item's questions file |

The form agent — the one that explores and files application forms — is not here: it is a
Claude Agent SDK session with tools on the live page, in `src/apply/explore_agentic/`
(`prompt.md`, `filing.md`, `resume.md`; see `docs/form-agent.md`).

Each folder:

- `prompt.md` — the prompt. `{name}` is filled in by the code; any other brace
  (JSON, LaTeX) is left as written.
- `tools.yaml` — how it runs: `llm` (the `config.yaml` `llm.<key>` giving model
  and effort), `max_turns` (`true` = `llm.max_turns`), `timeout` (seconds or a
  config key), `add_dirs`, `allowed_tools` (`tools: none` for none), and `params` — the
  blanks the prompt needs; a missing one stops the run instead of sending a prompt with a
  hole.

Rules every agent is given, and the code enforces where it can: never submit, never invent
an answer (values come from stored facts or the applicant), never handle credentials.
