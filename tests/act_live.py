"""tests/act_live.py — one round of see -> map -> act -> see on a live form page, and the
map's second look. Nothing is pressed that moves on or sends (no Next, no Submit).

    python tests/act_live.py <slug> [--model=opus --effort=low]

    see 1    see.describe(open_lists=True): the page, every list read whole
    map 1    Claude: [id, write|select, answer] per control
    act      act.act_rows: every row on its control, each read back
    see 2    the page again: what the act left empty or wrong
    map 2    Claude again, with the placeholders the act set (so it knows they are not
             the applicant's answers) — what would be acted on next

Writes under tests/maps/<slug>/:
    pageNN-<page>.prompt.txt / .reply.txt          map 1
    pageNN-<page>.round2.prompt.txt / .reply.txt   map 2
    pageNN-<page>.act.txt                          what each row did (the act's own log)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import map_page as MP                                     # noqa: E402
import see_live as SL                                     # noqa: E402
from jobpilot.core.answers import load, load_learned      # noqa: E402
from jobpilot.apply.explore import browser as B, act as A, mapper as M   # noqa: E402


def mapped(slug, step, frame, facts, learned, suffix, placeholders="(none)", last_act="(none)"):
    """see + map, through mapper.map_page: the round's result; its prompt and reply written."""
    m = M.map_page(frame, step.replace("-", " "), facts, learned, entries=[], placeholders=placeholders,
                   last_act=last_act, open_lists=True, model=MP.flag("model"), effort=MP.flag("effort"))
    base = os.path.join(MP.OUT, slug, f"page{MP.number(slug, step):02d}-{step}{suffix}")
    os.makedirs(os.path.dirname(base), exist_ok=True)
    for ext, text in ((".prompt.txt", m["prompt"]), (".reply.txt", m["reply"])):
        with open(base + ext, "w", encoding="utf-8") as f:
            f.write(text)
    return m


def main():
    slug = next(a for a in sys.argv[1:] if not a.startswith("--"))
    answers, learned = load("answers.yaml"), load_learned()
    facts = MP.job_facts(slug, answers, learned)
    resume = B.resume_path(answers, slug)
    with B.session() as br:
        page = br.new_page()
        frame, step, _ = SL.open_form(page, slug)
        print(f"  live page: {step}")
        m1 = mapped(slug, step, frame, facts, learned, "")
        print("  act:")
        outcomes = A.act_rows(frame, m1["controls"], m1["rows"], facts, resume)
        B.wait_quiet(frame, max_s=6)
        m2 = mapped(slug, step, frame, facts, learned, ".round2", placeholders=M.placeholders_text(outcomes),
                    last_act=M.last_act_text(outcomes, m1["problems"]))
        rows2 = m2["rows"]
    base = os.path.join(MP.OUT, slug, f"page{MP.number(slug, step):02d}-{step}")
    with open(base + ".act.txt", "w", encoding="utf-8") as f:
        for o in outcomes:
            f.write(f"{o.get('id')!s:4} {'ok ' if o.get('ok') else 'BAD'} {o.get('control', '')[:50]:50} "
                    f"{str(o.get('answer'))[:70]:70} -> {o.get('shown') or o.get('error') or ''}"
                    + (f"   PLACEHOLDER {o['placeholder']}" if o.get("placeholder") else "")
                    + (f"   OFFERED {o['offered']}" if o.get("offered") else "") + "\n")
    bad = [o for o in outcomes if not o.get("ok")]
    print(f"  act: {len(outcomes) - len(bad)} ok, {len(bad)} not; "
          f"{sum(1 for o in outcomes if o.get('placeholder'))} placeholder(s)")
    print(f"  see 2 -> map 2: {len(rows2)} rows still to do")
    print(f"  {base}.act.txt, .prompt.txt/.reply.txt, .round2.prompt.txt/.reply.txt")


if __name__ == "__main__":
    main()
