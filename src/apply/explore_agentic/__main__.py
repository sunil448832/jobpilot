"""python -m jobpilot.apply.explore_agentic <slug> [--model=opus --effort=low --max-turns=200 --fresh]

Explore one application with the agent (session.py) — resuming from its record when it has
one, never sending it — and queue its card for approval (card.py)."""
import argparse
import asyncio
import sys

from jobpilot.core.answers import load, preflight, read_jd
from jobpilot.core.config import cfg
from jobpilot.apply import platforms
from jobpilot.apply.explore_agentic import card, session


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("slug", help="the application folder under applications/")
    ap.add_argument("--model", default=cfg("llm.form_agent.model", "opus"))
    ap.add_argument("--effort", default=cfg("llm.form_agent.effort", "low"))
    ap.add_argument("--max-turns", type=int, default=200)
    ap.add_argument("--fresh", action="store_true", help="ignore the record: start from the posting")
    a = ap.parse_args()
    answers = load("answers.yaml")
    preflight(answers)
    meta, _jd = read_jd(a.slug)
    url = meta.get("Apply URL") or meta.get("Link")
    portal = (meta.get("Platform / How applying") or "").split("—")[0].strip()
    if not platforms.get(portal):
        portal, _known = platforms.detect(url)
    if platforms.is_manual(portal):
        sys.exit(f"Portal '{portal}' is not supported for autofill — applied by hand, "
                 f"never automated (README rule 1).")
    out = asyncio.run(session.run(a.slug, a.model, a.effort, a.max_turns, a.fresh))
    if "posting is closed" in (out.get("note") or ""):
        sys.exit(f"EXPIRED: {out['note']}")
    item = card.write(a.slug, out, answers)
    print(f"\n  [explore] {item['id']}: {item['status']} — {len(item['fields'])} filled, "
          f"{len(item['questions'])} question(s)")


if __name__ == "__main__":
    main()
