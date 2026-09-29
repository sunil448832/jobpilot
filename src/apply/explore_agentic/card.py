"""explore_agentic/card.py — the phone's card for an application (core/cards.py: where it lives): what
an exploration left for his approval, and the queue the filing takes approved cards from.

    write(slug, out, answers)   the card for a finished exploration (session.run's result):
                                what was filled, the questions only he can answer (the
                                placeholders, with the agent's candidates), the last page's
                                screenshot; earlier unsent cards of the job are superseded;
                                Telegram is told (tell): a short message with the review link
    questions_from(record)      the record's unanswered placeholders as the card's questions
    save(item) / load(id)       one card
    approved(slug=None)         approved cards, oldest first (one job's newest, with a slug)
"""
import datetime as dt
import os

from jobpilot.core import cards as CD
from jobpilot.core.answers import read_jd, detect_market
from jobpilot.core.paths import TOOL
from jobpilot.apply import platforms
from jobpilot.apply.explore_agentic import facts as F, record as R

save, load, cards = CD.save, CD.load, CD.cards


def approved(slug=None):
    """Approved cards not yet sent, oldest first; with a slug, that job's newest only."""
    out = [it for it in cards() if it.get("status") == "approved" and not it.get("submitted_at")
           and (slug is None or it.get("company_slug") == slug)]
    out.sort(key=lambda it: it.get("created") or it["id"])
    return out[-1:] if slug else out


def questions_from(record, answers=None):
    """The unanswered placeholders as the card's questions: the agent's candidates (whole
    chains for a nested list) and what stood in while exploring. A stand-in for a stored fact
    the form's list could not hold (its "for") says so: his pick is for this form only."""
    stored = F.flatten(answers or {})
    out = []
    for i, (q, p) in enumerate((record.get("placeholders") or {}).items()):
        if (p.get("answer") or "").strip():
            continue
        cands = list(p.get("candidates") or [])
        note = f"explored with placeholder {str(p.get('used'))[:40]!r} — needs your answer"
        if p.get("for"):
            note = (f"your stored {p['for']} ({stored.get(p['for'], '?')!r}) is not in this portal's list — the nearest "
                    "entry is picked; the one you approve is kept for this portal and used on its next jobs "
                    "(never replaces the stored fact)")
        out.append({"qid": i, "label": q, "options": cands, "kind": "select" if cands else "text",
                    "required": True, "status": "open", "page": p.get("page") or "", "field": p.get("field") or "",
                    "value": str(p.get("used") or ""), "note": note, **({"fact": p["for"]} if p.get("for") else {})})
    return out


def write(slug, out, answers):
    """The card for a finished exploration. One that did not reach the last page is failed."""
    meta, jd = read_jd(slug)
    market = detect_market(meta.get("Location", ""), jd)
    record = R.load(slug)
    reached = out["outcome"] == "last-page"
    questions = questions_from(record, answers)
    for it in cards():                                   # one card per job on the phone: this run's
        if it.get("company_slug") == slug and it.get("status") not in ("submitted", "superseded") \
                and not it.get("submitted_at"):
            it["status"] = "superseded"
            save(it)
    item = {
        "id": f"{slug}-{dt.datetime.now():%m%d%H%M}", "company": meta.get("Company", slug), "company_slug": slug,
        "role": meta.get("Role / Title", ""), "location": meta.get("Location", ""),
        "url": meta.get("Apply URL") or meta.get("Link"), "portal": out.get("platform"), "market": market,
        "tenant": platforms.tenant(out.get("platform"), meta.get("Apply URL") or meta.get("Link")),
        "salary_quoted": F.pay_text(answers, market), "score": None, "resume": out.get("resume"),
        "screenshot": out.get("screenshot"), "fields": {k.split(" :: ", 1)[-1]: v for k, v in (out.get("filled") or {}).items()},
        "warnings": [out["note"]] if out.get("note") else [], "questions": questions,
        "pages": len(out.get("pages") or []), "reached_end": reached,
        "replay": os.path.relpath(R.path_for(slug), TOOL),
        "status": ("needs_input" if questions else "pending") if reached else "failed",
        "fail_reason": None if reached else f"exploration did not reach the last page — {out.get('note', '')[:200]}",
        "created": dt.datetime.now().isoformat(timespec="seconds"),
    }
    save(item)
    tell(item)
    return item


def tell(item):
    """A new card to review: one short Telegram message with the review list's link, so it is
    not forgotten. A failed exploration says so instead."""
    try:
        from jobpilot.core.daily import form_link, telegram
        link = form_link()
        nq = len([q for q in item.get("questions", []) if q.get("status") != "answered"])
        head = {"pending": "📝 <b>New application to review</b>",
                "needs_input": f"📝 <b>New application to review</b> — {nq} question(s) for you",
                "failed": "⚠ <b>Exploration stopped short</b>"}.get(item["status"])
        if head:
            telegram(f"{head}\n{item.get('company')} — {item.get('role')}"
                     f"\nOpen it: {link.replace('/?', '/a/' + item['id'] + '?')}\nReview list: {link}")
    except Exception as e:
        print(f"  [warn] telegram: {type(e).__name__}: {e}")
