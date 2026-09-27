"""explore — PASS 1: walk an application form to its last page by see / map / act,
never press Submit, queue it for approval (docs/exploration-plan.md).

    see.py        a page as a screen reader reads it (accessibility snapshot)
    act.py        one routine per kind of control, found by role + name, checked
    mapper.py     Claude maps the controls no saved map covers: [name, kind, fact]
    reuse.py      saved page maps: per company / tenant, platform standard fields
    walk.py       the walk: page by page, the record, the queue item
    facts.py      the applicant's facts for one job, by key
    record.py     applications/<slug>/explore.json — what submit replays
    browser.py    the browser, dead postings, verification codes, outcome messages
    __main__.py   python -m jobpilot.apply.explore <slug>
"""
from jobpilot.apply.explore.walk import explore, Walk  # noqa: F401
