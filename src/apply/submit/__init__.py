"""submit — PASS 2: file an approved application by replaying its recorded actions
(applications/<slug>/explore.json) with the true values, then Submit. Code only.

    replay.py     the replay, the Submit press, the confirmation check
    __main__.py   python -m jobpilot.apply.submit [QUEUE_ID] [--limit N]
"""
from jobpilot.apply.submit.replay import submit_approved, submit_one  # noqa: F401
