"""python -m jobpilot.apply.explore_agentic <slug> [--model=opus --effort=low --max-turns=200 --fresh]
        [--feedback-file applications/<slug>/feedback.json --draft]

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
    ap.add_argument("--feedback-file", help="his feedback on the last version (a redraft from the card)")
    ap.add_argument("--draft", action="store_true", help="draft answers to the new card's open questions too")
    a = ap.parse_args()
    feedback = []
    if a.feedback_file:
        import json
        try:
            feedback = [f.get("text", "") for f in json.load(open(a.feedback_file))]
        except (OSError, ValueError):
            feedback = []
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
    out = asyncio.run(session.run(a.slug, a.model, a.effort, a.max_turns, a.fresh, feedback))
    if "posting is closed" in (out.get("note") or ""):
        sys.exit(f"EXPIRED: {out['note']}")
    if a.draft:                                     # the card is told once its answers are drafted
        out["defer_tell"] = True
    item = card.write(a.slug, out, answers)
    if a.draft:
        from jobpilot.apply.draft import draft
        from jobpilot.tailor.autotailor import claude_bin
        draft.draft_for(a.slug, claude_bin(), feedback=feedback)
        from jobpilot.core import cards as CD
        item = CD.load(item["id"]) or item
        card.tell(item)
    print(f"\n  [explore] {item['id']}: {item['status']} — {len(item['fields'])} filled, "
          f"{len(item['questions'])} question(s)")


if __name__ == "__main__":
    main()
