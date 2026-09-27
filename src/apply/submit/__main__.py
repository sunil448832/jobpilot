"""python -m jobpilot.apply.submit [QUEUE_ID] [--limit N] — PASS 2: file approved
applications by replaying their recorded actions with the true values. Code only."""
import argparse

from jobpilot.core.answers import load, load_learned, preflight
from jobpilot.apply.submit import submit_approved


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("id", nargs="?", help="one approved queue item (default: every approved one)")
    ap.add_argument("--limit", type=int, metavar="N", help="file at most N now (oldest approved first)")
    a = ap.parse_args()
    answers = load("answers.yaml")
    preflight(answers)
    submit_approved(answers, load_learned(), one=a.id, limit=a.limit)


if __name__ == "__main__":
    main()
