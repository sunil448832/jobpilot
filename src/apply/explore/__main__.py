"""python -m jobpilot.apply.explore <slug> [--url URL] — PASS 1 for one application:
walk its form by see / map / act, never submit, queue it for approval."""
import argparse
import sys

from jobpilot.core.answers import load, load_learned, preflight, read_jd, detect_market
from jobpilot.apply import platforms
from jobpilot.apply.explore import explore
from jobpilot.apply.explore.facts import pay_text


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("slug", help="the application folder under applications/")
    ap.add_argument("--url", help="override the apply URL")
    ap.add_argument("--model", help="the map's model for this run (default: config llm.map)")
    ap.add_argument("--effort", help="the map's effort for this run (default: config llm.map)")
    a = ap.parse_args()
    answers = load("answers.yaml")
    preflight(answers)
    meta, jd_text = read_jd(a.slug)
    url = a.url or meta.get("Apply URL") or meta.get("Link")
    portal = (meta.get("Platform / How applying") or "").split("—")[0].strip()
    if not platforms.get(portal):
        portal, _known = platforms.detect(url)
    if platforms.is_manual(portal):
        sys.exit(f"Portal '{portal}' is not supported for autofill — applied by hand, "
                 f"never automated (README rule 1).")
    market = detect_market(meta.get("Location", ""), jd_text)
    ctx = {"market": market, "company": meta.get("Company", a.slug), "role": meta.get("Role / Title", ""),
           "portal": portal, "location": meta.get("Location", ""), "url": url, "company_slug": a.slug,
           "map_model": a.model, "map_effort": a.effort}
    explore(ctx, answers, load_learned(), {"expected_text": pay_text(answers, market)})


if __name__ == "__main__":
    main()
