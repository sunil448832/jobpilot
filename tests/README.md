# tests

    /home/sunil/softwares/miniconda3/bin/python3 tests/test_agents.py    # the agents folder renders; the job facts the form agent sees
    bash tests/agentic_batch.sh <slug> [<slug> ...]           # the form agent on real applications, one after another
                                                               # (live: a browser, Claude; never submits)

`./jobpilot check` runs `test_agents.py` with the other smoke checks.

`logs/sessions/<slug>/` (at the top of the tool) keeps each live session for review: `agentic-<time>.log` (the agent's words,
every tool call and its result), `agentic-<time>.system.txt` (the system prompt as sent),
`replay-<time>.log` (a filing: the redo, and the agent where it was needed).
