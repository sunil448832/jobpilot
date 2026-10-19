"""
platforms/successfactors.py — SAP SuccessFactors careers sites (careers.sap.com and the
career<N>.successfactors.<com|eu> portals that any jobs2web careers site hands off to):
link recognition and the account gate. Everything on the application page itself is
filled by the form agent, like any form.

    at_gate(page)           the "Career Opportunities: Sign In" page is showing
    start(page, ctx, log, press_start)
                            sign in with the stored credentials (a TrustArc consent overlay
                            swallows clicks on these pages: it is removed first). The code
                            never creates an account: a refused sign-in is put to him on the
                            questions page (fix the account or reset its password to the one
                            stored), then one more try — never a password asked.

Credentials: .env SUCCESSFACTORS_EMAIL / SUCCESSFACTORS_PASSWORD, else the Workday pair
(his call, 2026-10-14: the SAP careers account uses the same email and password). Never
printed and never shown to Claude.
"""
import re

ID = "successfactors"
HOSTS = ("successfactors.", "careers.sap.com", "jobs.sap.com")
ROUTE = "mobile"
STANDARD = False
GATE_HEADING = re.compile(r"career opportunities:\s*sign in", re.I)
REFUSED = re.compile(r"(email|user ?name|password).{0,60}(incorrect|invalid|not (valid|recognised|recognized|found)|"
                     r"does not (match|exist))|incorrect (email|password)|locked|too many attempts|"
                     r"not a registered user|no account", re.I)
# TrustArc's consent banner: a full-page overlay that intercepts every click until answered
CONSENT_IDS = ("trustarc-banner-overlay", "consent_blackbar", "truste-consent-track")


def creds():
    from jobpilot.core.daily import env
    e = env()
    return (e.get("SUCCESSFACTORS_EMAIL") or e.get("WORKDAY_EMAIL", ""),
            e.get("SUCCESSFACTORS_PASSWORD") or e.get("WORKDAY_PASSWORD", ""))


def company_key(url):
    """careers.sap.com -> sap; career5.successfactors.eu/careers?company=SAP -> sap."""
    import urllib.parse
    p = urllib.parse.urlparse(url or "")
    q = urllib.parse.parse_qs(p.query)
    if q.get("company"):
        return q["company"][0].lower()
    m = re.match(r"^(?:careers|jobs)\.([^.]+)\.", p.netloc.lower())
    return m.group(1) if m else None


# ---------------------------------------------------------------- the page, like a person

def _box(frame, pattern):
    for loc in frame.get_by_role("textbox").all():
        try:
            n = loc.get_attribute("aria-label") or loc.evaluate(
                "el => (el.labels && el.labels[0] && el.labels[0].innerText) || ''")
            if re.search(pattern, n or "", re.I) and loc.is_visible():
                return loc
        except Exception:
            continue
    return None


def _clear_consent(page):
    """Remove the consent banner so clicks reach the page. Removed, not answered: pressing its
    "Accept All" reloads the posting and swallows the Apply click that follows (2026-10-14)."""
    try:
        page.evaluate("ids => ids.forEach(id => { const e = document.getElementById(id); if (e) e.remove(); })",
                      list(CONSENT_IDS))
    except Exception:
        pass


def _quiet(page):
    from jobpilot.apply.explore_agentic.browser import wait_quiet
    wait_quiet(page.main_frame, max_s=10, quiet=4)


def _said(frame):
    from jobpilot.apply.explore_agentic import see
    return " ".join(see.errors(frame))


def _heading(page):
    try:
        return (page.main_frame.get_by_role("heading", level=1).first.inner_text(timeout=1500) or "").strip()
    except Exception:
        return ""


def at_gate(page):
    try:
        f = page.main_frame
        if GATE_HEADING.search(_heading(page)):
            return True
        return bool(_box(f, r"^email address") and _box(f, r"^password"))
    except Exception:
        return False


def _sign_in(page, email, pw, log):
    """Email and password into the gate, sent. True once the gate is gone: the portal shows
    "Loading..." for a good while (30s+) before it draws the application page."""
    f = page.main_frame
    _clear_consent(page)
    _box(f, r"^email address").fill(email)
    _box(f, r"^password").fill(pw)
    btn = f.get_by_role("button", name="Sign In", exact=True)
    if btn.count():
        btn.first.click(force=True, timeout=5000)
    else:
        _box(f, r"^password").press("Enter")
    page.wait_for_timeout(3000)
    if at_gate(page) and not _said(f) and _box(f, r"^password") and (_box(f, r"^password").input_value() or ""):
        _box(f, r"^password").press("Enter")              # the press did not register: send the form the other way
    for _ in range(30):
        page.wait_for_timeout(2500)
        if not at_gate(page):
            try:
                body = (f.inner_text("body", timeout=3000) or "").lower()
            except Exception:
                body = ""
            if "loading" not in body[:400]:
                break
        elif _said(f):
            break
    _quiet(page)
    _clear_consent(page)
    return not at_gate(page)


def _ask_fix(page, ctx, log, what):
    from jobpilot.review.ask import ask
    ans = ask(f"sf-signin-{ctx.get('company_slug', 'x')}",
              f"The SAP careers sign-in did not work: {what}. Sign in yourself at {ctx.get('url', '')} — unlock the "
              "account, or reset its password to the one jobpilot has stored (the Workday one) — then type done here. "
              "Type skip to leave this application for now.", hint="the password is never asked for here",
              timeout=1800, about=f"{ctx.get('company', 'SAP')} (careers sign-in)", slug=ctx.get("company_slug"))
    ok = (ans or "").strip().lower() == "done"
    log(f"    [successfactors] sign-in fix: {'done — trying once more' if ok else (ans or 'no answer') + ' — stopped'}")
    return ok


def start(page, ctx, log=print, press_start=None):
    """Through the sign-in gate to the application page. The button that opens the
    form is the map's (press_start)."""
    email, pw = creds()
    for i in range(6):
        _clear_consent(page)
        if at_gate(page):
            if not (email and pw):
                log("    [successfactors] no credentials in .env (SUCCESSFACTORS_* or WORKDAY_*)")
                return False
            if _sign_in(page, email, pw, log):
                log("    [successfactors] signed in")
                return True
            said = _said(page.main_frame)
            log(f"    [successfactors] sign-in refused: {said[:120] or 'no message'}")
            if not said and i == 0:
                continue                                  # no message: the press was lost — once more, quietly
            if i >= 2 or not _ask_fix(page, ctx, log, f"“{said[:140] or 'refused without a message'}”"):
                return False
            continue
        if page.main_frame.get_by_role("link", name="Sign Out").count():
            return True                                   # signed in: on the application
        if not (press_start and press_start(page)):
            page.wait_for_timeout(2000)
    log("    [successfactors] the application page did not open")
    return False
