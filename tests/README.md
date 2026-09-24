# tests

Fixture tests for the fill engine. Each is a plain script: it starts headless Chrome
(the same `channel="chrome"` the tool uses), serves a small HTML form locally, and
prints one line per check. No network beyond localhost, no LLM.

    /home/sunil/miniconda3/bin/python3 tests/test_replay.py   # explore/replay of one page: placeholders, recipes, gate, untick
    /home/sunil/miniconda3/bin/python3 tests/test_walk.py     # multi-page route: Apply -> Next -> Submit, guard, learned module

`./jobpilot check` runs both.
