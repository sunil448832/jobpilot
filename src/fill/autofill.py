#!/usr/bin/env python3
"""
autofill.py — fill an external job application from answers.yaml (Phase 2).

Two passes, by design (see README.md, appendix):

  PASS 1  --fill <company>     Opens the form in real Chrome, fills every field it
                               can map, screenshots it, and writes a PENDING queue
                               item. NEVER submits. The browser closes afterwards.
  PASS 2  --submit-approved    For each queue item the phone marked APPROVED,
                               re-opens and re-fills (deterministic — same script,
                               same data), then submits.

Holding a browser open for hours waiting on approval is not viable, so pass 1 is
effectively a dry run that proves the form is fillable and shows exactly what will
be sent. Re-filling in pass 2 takes ~20s.

Safety rules baked in:
  - Pass 1 has no code path that clicks submit.
  - Any unresolved TODO value aborts before the browser opens.
  - Salary is chosen per the job's country; INR is never quoted.
  - "Previously employed here" respects the per-company override (Amazon = yes).

Usage:
    python jobs/autofill.py --selftest              # check field matching, no browser
    python jobs/autofill.py --fill <company>        # pass 1
    python jobs/autofill.py --fill <company> --url <override>
    python jobs/autofill.py --submit-approved       # pass 2
    python jobs/autofill.py --submit <queue-id>
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

import yaml

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)
APPLICATIONS_DIR = APPLICATIONS
QUEUE_DIR = os.path.join(DATA, "queue")
PROFILE_DIR = os.path.expanduser("~/.config/jobbot/chrome-profile")

SUPPORTED = {"greenhouse", "lever", "ashby"}


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def load(name):
    with open(os.path.join(CONFIG, name)) as f:
        return yaml.safe_load(f)


def read_jd(company):
    """Pull the meta block apply.py wrote into applications/<company>/JD.md."""
    path = os.path.join(APPLICATIONS_DIR, company, "JD.md")
    if not os.path.isfile(path):
        sys.exit(f"No JD.md for '{company}'. Run: python jobs/apply.py <url>")
    text = open(path, encoding="utf-8").read()
    meta = {}
    for key in ("Company", "Role / Title", "Location", "Platform / How applying",
                "Autofill route", "Apply URL", "Link"):
        m = re.search(rf"^- \*\*{re.escape(key)}:\*\*\s*(.*)$", text, re.M)
        if m:
            meta[key] = m.group(1).strip()
    return meta, text


# Checked against the LOCATION first and only then the JD body. Order matters:
# specific markets before the USA, whose old " us " hint matched ordinary prose
# like "work with us" and priced an Abu Dhabi role in USD.
MARKET_HINTS = [
    ("uae", ["united arab emirates", "uae", "dubai", "abu dhabi", "sharjah"]),
    ("saudi", ["saudi", "riyadh", "neom", "jeddah", "dhahran", "ksa"]),
    ("netherlands", ["netherlands", "amsterdam", "eindhoven", "utrecht",
                     "rotterdam", "holland", "the hague", "den haag"]),
    ("australia", ["australia", "sydney", "melbourne", "brisbane", "canberra",
                   "perth", "adelaide"]),
    ("usa", ["united states", "u.s.a", "usa", "california", "new york",
             "seattle", "austin", "boston", "san francisco", "chicago",
             ", ca", ", ny", ", wa", ", tx", ", ma", ", il", ", va"]),
]


def detect_market(location, jd_text=""):
    """Which salary block applies. The LOCATION decides; the JD is a fallback only."""
    loc = (location or "").lower()
    for market, hints in MARKET_HINTS:
        if any(h in loc for h in hints):
            return market
    if "remote" in loc:
        return "remote_india"
    # Only if the location said nothing useful, and only on the opening lines
    # where a real posting states where the role sits.
    head = (jd_text or "")[:1200].lower()
    for market, hints in MARKET_HINTS:
        if any(h in head for h in hints):
            return market
    return "default"


def preflight(answers):
    """Refuse to open a browser if any value would type the literal 'TODO'."""
    bad = []

    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")
        elif o == "TODO" and not path.endswith(".gpa"):
            bad.append(path)

    walk(answers)
    if bad:
        sys.exit("Unresolved TODO fields in answers.yaml:\n  " + "\n  ".join(bad))


# --------------------------------------------------------------------------
# field matching
# --------------------------------------------------------------------------

def load_learned():
    path = os.path.join(CONFIG, "learned.yaml")
    if not os.path.isfile(path):
        return []
    with open(path) as f:
        return (yaml.safe_load(f) or {}).get("answers") or []


def _tokens(s):
    return set(re.findall(r"[a-z0-9][a-z0-9.+-]*", (s or "").lower()))


def match_learned(label, learned):
    """Exact-ish, then keyword, then fuzzy token overlap. Returns answer or None."""
    L = norm(label)
    if not L:
        return None
    lt = _tokens(L)
    if not lt:
        return None
    best, best_score = None, 0.0
    for entry in learned:
        ans = entry.get("answer")
        if not ans:
            continue
        m = norm(entry.get("match", ""))
        if m and (m in L or L in m):
            return ans
        kws = [k.lower() for k in (entry.get("keywords") or [])]
        hits = sum(1 for k in kws if k in L)
        if kws and hits >= max(2, len(kws) // 3):
            return ans
        overlap = len(lt & _tokens(m)) / max(len(lt | _tokens(m)), 1)
        if overlap > best_score:
            best, best_score = ans, overlap
    return best if best_score >= 0.72 else None


def norm(s):
    s = re.sub(r"[\*∗]", "", s or "")
    s = re.sub(r"\(required\)|\(optional\)", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip().lower()


YES, NO, SKIP = "__YES__", "__NO__", None


def build_resolver(answers, ctx, learned=None):
    """Return fn(label) -> value|YES|NO|None, closed over the job's context."""
    learned = load_learned() if learned is None else learned
    p = answers["personal"]
    loc = answers["location"]
    links = answers["links"]
    comp = answers["compensation"]
    avail = answers["availability"]
    eeo = answers["eeo"]
    scr = answers["screening"]
    skill = answers["skill_years"]
    exp = answers["experience_summary"]

    market = ctx["market"]
    pay = comp["by_market"].get(market, comp["default"])
    company_l = (ctx.get("company") or "").lower()

    prev_employed = scr["previously_employed_here"]
    for key, val in (scr.get("previously_employed_overrides") or {}).items():
        if key.lower() in company_l or company_l in key.lower():
            prev_employed = val
            break

    # ORDER MATTERS: first match wins.
    #
    # Question-shaped rules come FIRST. A question like "authorized to work in the
    # United States?" otherwise collides with field-label patterns — "United" was
    # matching the address-line-2 rule ("unit") and returning "", and "States"
    # matched a loose "\bstate" pattern. Both are now boundary-anchored, and the
    # yes/no questions are matched before any field label.
    company_rx = re.escape(company_l) if company_l else r"(?!x)x"

    RULES = [
        # --- HARD STOPS, matched before anything else ---
        # Caught on the real Databricks form: a sanctions checkbox reading
        # "Ordinarily a resident of Russia or Belarus and NOT willing to relocate"
        # matched the "willing to relocate" rule and was nearly ticked. Checking a
        # box wrongly is a false declaration; leaving it blank is merely incomplete.
        # So: compliance questions are never auto-answered, and any negated label
        # is handed to a human rather than matched by a positive rule.
        # Compliance stays hard-stopped ONLY when nothing is on file. A stored
        # answer is Sunil's own confirmed wording, so it is used (checked first,
        # above the regex table, in resolve()).
        (r"russia|belarus|sanction|export.control|ofac|denied party|embargo"
         r"|restricted party|debarr", SKIP),
        (r"\bnot willing\b|\bnot able\b|\bunable to\b|\bnot authorized\b"
         r"|\bdo not\b|\bdon't\b|\bneither\b|\bnone of the above\b", SKIP),

        # --- sponsorship / authorization (polarity is easy to get backwards) ---
        (r"(require|need).*(sponsor|visa)|sponsorship.*(require|need)", YES),
        (r"will you (now or in the future )?require", YES),
        (r"(legally )?(authorized|authorised|eligible).*(work|employment)", NO),
        (r"do you (currently )?have.*(right to work|work permit|work authorization)", NO),
        (r"visa status|immigration status", answers["work_authorization"]["visa_status_note"]),

        # --- yes/no screeners ---
        (rf"(previously|ever|formerly).*(employed|worked)|former employee"
         rf"|worked (at|for) {company_rx}", YES if prev_employed else NO),
        (r"relat(ed|ive).*(employee|work here)", NO),
        (r"(18 years|age of 18|over 18)", YES),
        (r"background check", YES),
        (r"drug (test|screen)", YES),
        # Compound "based in X or willing to relocate" questions are often free
        # text, where a bare "Yes" wastes the field. Answer with the useful version.
        (r"(confirm|are you|do you).*(based in|located in|reside).*(relocat|willing)"
         r"|(relocat).*(based in|located in)",
         f"Yes - willing to relocate. {answers['work_authorization']['visa_status_note']}"),
        (r"willing to relocate|open to relocation", YES),
        (r"willing to travel", YES),
        (r"non-?compete|restrictive covenant", NO),
        (r"accommodation", NO),

        # --- identity ---
        (r"\bfirst\s*name|\bgiven name|^fname", p["first_name"]),
        (r"\bmiddle\s*(name|initial)", p["middle_name"]),
        (r"\blast\s*name|\bsurname|\bfamily name|^lname", p["last_name"]),
        (r"\bpreferred\s*name|\bnick", p["preferred_name"]),
        (r"\bfull\s*name|\blegal\s*name|^name$|your name", p["full_name"]),
        # "I confirm I have read the above" — an acknowledgement of something
        # shown on the page. Scoped to read/understand/acknowledge wording so it
        # can never blanket-yes a substantive declaration.
        (r"i (confirm|acknowledge|agree|certify|understand)\b.{0,60}"
         r"(read|understood|above|terms|privacy|policy|notice|statement)", YES),
        (r"\be-?mail", p["email"]),
        (r"\bphone|\bmobile|\btelephone|\bcontact number", p["phone"]),
        (r"pronoun", p["pronouns"]),

        # --- address (boundary-anchored; "United States" must not match these) ---
        (r"address\s*line\s*2|\bapt\b|\bsuite\b|\bunit\b", ""),
        (r"street|address\s*line\s*1|^address$|mailing address", loc["address_line_1"]),
        (r"\bcity\b|\btown\b", loc["city"]),
        (r"\bstate\b|\bprovince\b|\bregion\b(?!.*prefer)", loc["state"]),
        (r"\bzip\b|postal", loc["postal_code"]),
        (r"\bcountry\b(?!.*citizen)", loc["country"]),
        (r"current location|where are you (based|located)|location$",
         f"{loc['city']}, {loc['country']}"),

        # --- links ---
        (r"linkedin", links["linkedin"]),
        (r"github", links["github"]),
        (r"portfolio|personal (web)?site|\bwebsite\b|other url", links["portfolio"]),

        # --- availability / pay ---
        (r"notice period", avail["notice_period"]),
        (r"(pick|select).{0,12}date|start date|date.{0,12}available",
         avail.get("earliest_start_iso", avail["earliest_start_date"])),
        (r"when can you start|availability|notice",
         avail["earliest_start_date"]),
        (r"where are you (currently )?(located|based)|current(ly)? locat",
         f"{answers['location']['city']}, {answers['location']['country']}"),
        (r"able to work from a local office|work from the office|onsite \d|"
         r"hybrid|days per week", YES),
        (r"(salary|compensation|remuneration|package).*(expect|require|desired)"
         r"|expected (salary|compensation|ctc)|desired (salary|compensation)"
         r"|salary expectation", pay["expected_text"]),
        (r"current (salary|compensation|ctc)", comp["current_ctc"]),

        # --- sourcing ---
        (r"how did you (hear|find)|referral source|source",
         answers["questions"]["how_did_you_hear"]),
        (r"referred by|referrer", answers["questions"]["referred_by"]),

        # --- EEO ---
        (r"\bgender\b", eeo["gender"]),
        (r"race|ethnic", eeo["race_ethnicity"]),
        (r"hispanic|latino", eeo["hispanic_latino"]),
        (r"veteran", eeo["veteran_status"]),
        (r"disab", eeo["disability_status"]),

        # --- always left to a human ---
        (r"cover letter|why (do you want|are you interested)|tell us", SKIP),
    ]

    compiled = [(re.compile(pat), val) for pat, val in RULES]

    def resolve(label):
        L = norm(label)
        if not L:
            return SKIP

        # Anything Sunil has already answered wins over every heuristic below,
        # including the compliance hard-stops — those exist to avoid GUESSING,
        # and a stored answer is not a guess.
        stored = match_learned(label, learned)
        if stored is not None:
            return stored

        # "How many years of experience with X?" -> the per-skill table.
        if re.search(r"years?.*(experience|exp)|experience.*years?", L):
            for skill_key, yrs in skill.items():
                if skill_key == "default":
                    continue
                token = skill_key.replace("_", " ")
                if token in L or skill_key in L:
                    return yrs
            if re.search(r"total|overall|professional|relevant|industry", L):
                return exp["total_years"]
            return skill["default"]

        for rx, val in compiled:
            if rx.search(L):
                return val
        # Nothing structured matched — check what Sunil has already answered before.
        return match_learned(label, learned) or SKIP

    return resolve, pay


# --------------------------------------------------------------------------
# self-test — verifies matching without opening a browser
# --------------------------------------------------------------------------

SELFTEST_LABELS = [
    "First Name *", "Last Name *", "Email *", "Phone", "Location (City)",
    "LinkedIn Profile", "GitHub URL", "Website",
    "Are you legally authorized to work in the United States?",
    "Will you now or in the future require sponsorship for employment visa status?",
    "What is your notice period?", "Earliest start date",
    "Desired salary", "Current CTC",
    "How many years of experience do you have with Python?",
    "How many years of experience do you have with Generative AI?",
    "Years of experience with PyTorch", "Total years of professional experience",
    "How did you hear about this job?",
    "Have you previously been employed by Amazon?",
    "Gender", "Race / Ethnicity", "Veteran Status", "Disability Status",
    "Are you over 18 years of age?", "Are you willing to relocate?",
    "Street Address", "City", "State", "Zip Code", "Country",
    "Cover Letter", "Why do you want to work here?",
]


def selftest():
    answers = load("answers.yaml")
    print("=== market detection ===")
    for loc in ["Dubai, UAE", "Amsterdam, Netherlands", "San Francisco, CA",
                "Sydney, Australia", "Riyadh, Saudi Arabia", "Remote", "Bangalore, India"]:
        print(f"  {loc:<28} -> {detect_market(loc)}")

    for market, company in [("usa", "Stripe"), ("uae", "G42"), ("usa", "Amazon")]:
        ctx = {"market": market, "company": company}
        resolve, pay = build_resolver(answers, ctx)
        print(f"\n=== {company} ({market}) — salary block: {pay['expected_text']} ===")
        unmatched = []
        for lab in SELFTEST_LABELS:
            v = resolve(lab)
            if v is SKIP:
                unmatched.append(lab)
            else:
                shown = {YES: "YES", NO: "NO"}.get(v, v)
                shown = str(shown)
                print(f"  {lab[:52]:<54} -> {shown[:58]}")
        if unmatched:
            print("  UNMATCHED (left for a human):")
            for u in unmatched:
                print(f"    - {u}")
        if company == "Stripe":
            break   # full dump once is enough; others just show the deltas
    print("\n=== per-company override check ===")
    for co in ["Amazon", "Amazon Web Services", "Stripe"]:
        r, _ = build_resolver(answers, {"market": "usa", "company": co})
        v = r("Have you previously been employed by this company?")
        print(f"  previously employed at {co:<22} -> {'YES' if v == YES else 'NO'}")


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--fill", metavar="COMPANY")
    ap.add_argument("--url", help="override the apply URL")
    ap.add_argument("--submit", metavar="QUEUE_ID")
    ap.add_argument("--submit-approved", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return

    answers = load("answers.yaml")
    preflight(answers)

    if args.fill:
        from jobpilot.fill.browser import fill_application       # imported late: needs playwright
        meta, jd_text = read_jd(args.fill)
        portal = (meta.get("Platform / How applying") or "").split("—")[0].strip()
        if portal not in SUPPORTED:
            sys.exit(f"Portal '{portal}' is not supported for autofill "
                     f"(supported: {', '.join(sorted(SUPPORTED))}).\n"
                     f"Workday-class portals are handled on the desktop by hand — "
                     f"see automation-plan.md decision D.")
        url = args.url or meta.get("Apply URL") or meta.get("Link")
        if portal == "ashby" and url and not url.rstrip("/").endswith("/application"):
            url = url.rstrip("/") + "/application"
        market = detect_market(meta.get("Location", ""), jd_text)
        ctx = {"market": market, "company": meta.get("Company", args.fill),
               "role": meta.get("Role / Title", ""), "portal": portal,
               "location": meta.get("Location", ""), "url": url,
               "company_slug": args.fill}
        resolve, pay = build_resolver(answers, ctx)
        fill_application(ctx, answers, resolve, pay, submit=False)
        return

    if args.submit or args.submit_approved:
        from jobpilot.fill.browser import submit_approved
        submit_approved(answers, one=args.submit)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
