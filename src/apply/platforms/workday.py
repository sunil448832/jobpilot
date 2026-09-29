"""
platforms/workday.py — Workday: link recognition, the JD from the tenant's JSON API,
and what only Workday needs BETWEEN its pages. Everything on a page is filled by the
form agent (explore_agentic), like any form.

    start(page, ctx, log, press_start)
                            the posting -> the first wizard page, through the account
                            gate (sign in; create the account when the tenant does not
                            know the address; an email verification asked on the questions
                            page). Done by code — the agent never sees the credentials.
                            What it cannot get past (a refused password, a locked account,
                            no account form) is asked on the questions page: he fixes the
                            account himself and answers done, then one more try; never an
                            account created over one that exists, never a password asked.
    step(page)              which page is showing: the progress bar's current step
    next_page(page, step, name)  press the map's `next` button; (moved on, the page's errors)
    is_last(step)           Review: exploration stops there, submit presses Submit

Every button's name comes from the page map (Claude reads it off the page), so
nothing here knows how a tenant words "Next" or "Apply". The gate's forms are sent
the way a person sends them — the form's own submit button, else Enter in the
password box — because a tenant may hide that button from assistive technology
(aria-hidden) while the page header carries a visible "Sign In" of the same name.

Credentials: ~/.config/jobbot/env (WORKDAY_EMAIL / WORKDAY_PASSWORD), never
printed and never shown to Claude.
"""
import re
import urllib.parse

from jobpilot.apply.platforms._http import get, html_to_text

ID = "workday"
HOSTS = ("myworkdayjobs.com", "myworkdaysite.com")   # the same wizard on either domain
ROUTE = "mobile"
STANDARD = False                      # each tenant's own page maps
WRONG_PASSWORD = re.compile(r"wrong (email address or )?password|incorrect password|account (might|may) be locked|"
                            r"account is locked", re.I)


def company_key(url):
    """The tenant: <tenant>.wd5.myworkdayjobs.com, or wd3.myworkdaysite.com/recruiting/<tenant>/."""
    p = urllib.parse.urlparse(url or "")
    m = re.match(r"^([^.]+)\.wd\d+\.myworkdayjobs\.com$", p.netloc.lower())
    segs = [s for s in p.path.split("/") if s]
    if m:
        return m.group(1)
    if p.netloc.lower().endswith("myworkdaysite.com") and len(segs) > 1 and segs[0] == "recruiting":
        return segs[1].lower()
    return None


def fetch_jd(url):
    """Workday exposes the posting as JSON under /wday/cxs/<tenant>/<site>/job/<path>."""
    p = urllib.parse.urlparse(url)
    segs = [s for s in p.path.split("/") if s]
    m = re.match(r"^([^.]+)\.wd\d+\.myworkdayjobs\.com$", p.netloc.lower())
    if m:
        tenant = m.group(1)
    elif p.netloc.lower().endswith("myworkdaysite.com") and len(segs) > 2 and segs[0] == "recruiting":
        tenant = segs[1]                     # wd3.myworkdaysite.com/recruiting/<tenant>/<site>/job/...
        segs = segs[2:]
    else:
        return None
    # A link copied from LinkedIn ends in ".../apply?source=LinkedIn" and may
    # carry a locale segment; the API wants <site>/job/<location>/<posting>.
    while segs and segs[-1].lower() in ("apply", "applymanually", "autofillwithresume"):
        segs.pop()
    if "job" not in segs:
        return None
    i = segs.index("job")
    site = segs[i - 1] if i >= 1 else None
    if not site or re.match(r"^[a-z]{2}-[A-Z]{2}$", site):
        return None
    api = f"https://{p.netloc}/wday/cxs/{tenant}/{site}/job/" + "/".join(segs[i + 1:])
    d = get(api, as_json=True)
    info = d.get("jobPostingInfo") or {}
    return {
        "title": info.get("title", ""),
        "company": tenant.replace("-", " ").title(),
        "location": info.get("location", ""),
        "text": html_to_text(info.get("jobDescription", "")),
        "apply_url": info.get("externalUrl", url),
    }


def company_key(url):
    """The tenant: adobe.wd5.myworkdayjobs.com -> adobe; wd3.myworkdaysite.com/recruiting/takeaway/... -> takeaway."""
    p = urllib.parse.urlparse(url or "")
    m = re.match(r"^([^.]+)\.wd\d+\.myworkdayjobs\.com$", p.netloc.lower())
    if m:
        return m.group(1)
    segs = [s for s in p.path.split("/") if s]
    if len(segs) > 1 and segs[0] == "recruiting":
        return segs[1]
    return None


def creds():
    from jobpilot.core.daily import env
    e = env()
    return e.get("WORKDAY_EMAIL", ""), e.get("WORKDAY_PASSWORD", "")


# ---------------------------------------------------------------- by name, like a person

def _find(frame, name, roles=("button", "link")):
    for role in roles:
        loc = frame.get_by_role(role, name=name, exact=True)
        if loc.count() and loc.first.is_visible():
            return loc.first
    return None


def _click(frame, name):
    loc = _find(frame, name)
    if loc is None:
        return False
    loc.click(timeout=5000)
    frame.wait_for_timeout(400)
    return True


def _box(frame, pattern):
    """The visible text box whose name matches (Email Address*, Password*)."""
    for loc in frame.get_by_role("textbox").all():
        try:
            n = loc.get_attribute("aria-label") or loc.evaluate(
                "el => (el.labels && el.labels[0] && el.labels[0].innerText) || ''")
        except Exception:
            continue
        if re.search(pattern, n or "", re.I) and loc.is_visible():
            return loc
    return None


def _quiet(page):
    from jobpilot.apply.explore_agentic.browser import wait_quiet
    wait_quiet(page.main_frame, max_s=10, quiet=4)


def _send(frame, box):
    """Send the form `box` sits in: its own submit button (even one hidden from assistive
    technology), else Enter in the box. Never a button found by name elsewhere on the page."""
    try:
        form = box.locator("xpath=ancestor::form[1]")
        if form.count():
            btn = form.locator('button[type="submit"], input[type="submit"]')
            if btn.count():
                btn.first.click(force=True, timeout=5000)
                frame.wait_for_timeout(1500)
                return
    except Exception:
        pass
    box.press("Enter")
    frame.wait_for_timeout(1500)


def _said(frame):
    from jobpilot.apply.explore_agentic import see
    return " ".join(see.errors(frame))


# ---------------------------------------------------------------- the interface

def step(page):
    """The progress bar's current step, lower case ('my information'); '' when none.
    Read as it is: the callers wait for the page where a wait is due."""
    try:
        t = page.evaluate("""() => {
          const cur = document.querySelector('[aria-current="step"], [data-automation-id="progressBarActiveStep"]');
          return cur ? cur.innerText : '';
        }""") or ""
    except Exception:
        t = ""
    t = re.sub(r"^current step \d+ of \d+\s*", "", re.sub(r"\s+", " ", t.strip().lower()))
    return t


def at_gate(page):
    """Is the account gate showing — the tenant's Sign In / Create Account step, its sign-in
    chooser (Apple, Google, email), or the email and password boxes? Code passes it (start);
    an agent never gets the credentials."""
    f = page.main_frame
    try:
        if re.search(r"sign in|create account", step(page)):
            return True
        if _find(f, "Sign in with email") is not None:
            return True
        return bool(_box(f, r"^email") and _box(f, r"^password"))
    except Exception:
        return False


def is_last(step_name):
    return (step_name or "").startswith("review")


def next_page(page, step_name, name):
    """Press the page's `next` button (its name from the map). (moved on, [the page's errors])."""
    from jobpilot.apply.explore_agentic import see
    if not _click(page.main_frame, name):
        return False, [f"no {name!r} button"]
    for _ in range(6):                                    # the page saves, then moves on: give it time — an
        _quiet(page)                                      # alert (a status like "file uploaded") is no reason to stop waiting
        if step(page) != step_name:
            return True, []
    return False, see.errors(page.main_frame) or ["the page did not move on and showed no error"]


def start(page, ctx, log=print, press_start=None):
    """From the posting to the first wizard page, through the account gate. The
    button that opens the form is the map's (press_start)."""
    f = page.main_frame
    for _ in range(8):
        _quiet(page)
        body = (f.inner_text("body") or "").lower()[:3000]
        if "something went wrong" in body:
            log("    [workday] 'something went wrong' — reloading")
            page.reload(wait_until="domcontentloaded")
            continue
        # the account gate first, told by its boxes — its step name varies by tenant
        # ("Sign In", "Create Account/Sign In")
        if _click(f, "Sign in with email"):               # the social sign-in chooser
            continue
        if _box(f, r"^email") and _box(f, r"^password"):
            if not _gate(page, ctx, log):
                return False
            continue
        if step(page):
            if re.search(r"sign in|create account", step(page)):
                page.wait_for_timeout(1500)               # the gate's own step: its boxes are still loading
                continue
            return True                                   # on a wizard page
        if not (press_start and press_start(page)):
            page.wait_for_timeout(2000)
    log("    [workday] the application wizard did not open")
    return False


def _ask_fix(page, ctx, log, what):
    """A sign-in the code cannot get past: ask him on the questions page to fix the account
    on the employer's Workday himself — never for the password itself. True when he says
    done (the caller tries once more), False on skip or no answer."""
    from jobpilot.review.ask import ask
    host = re.sub(r"^https?://([^/]+).*$", r"\1", ctx.get("url") or "")
    ans = ask(f"wd-signin-{ctx.get('company_slug', 'x')}",
              f"The Workday sign-in did not work: {what}. Sign in yourself at {host} — unlock the account, or "
              "reset its password to the one jobpilot has stored — then type done here. Type skip to leave "
              "this application for now.", hint="the password is never asked for here",
              timeout=1800, about=f"{ctx.get('company', 'Workday')} (Workday sign-in)", slug=ctx.get("company_slug"))
    ok = (ans or "").strip().lower() == "done"
    log(f"    [workday] sign-in fix: {'done — trying once more' if ok else (ans or 'no answer') + ' — stopped'}")
    return ok


def _sign_in(page, email, pw):
    """Email and password into the sign-in form, sent. True when the form went away."""
    f = page.main_frame
    _box(f, r"^email").fill(email)
    pw_box = _box(f, r"^password")
    pw_box.fill(pw)
    _send(f, pw_box)
    _quiet(page)
    return not _box(f, r"^password")


def _gate(page, ctx, log, asked=False):
    """Sign in; when the tenant does not know the address, create the account. What the code
    cannot get past (a refused password, a locked account, no account form) is put to him on
    the questions page once; after his done, one more try."""
    f = page.main_frame
    email, pw = creds()
    if not (email and pw):
        log("    [workday] WORKDAY_EMAIL / WORKDAY_PASSWORD missing in ~/.config/jobbot/env")
        return False
    creating = _box(f, r"verify.*password") is not None
    if not creating:
        if _sign_in(page, email, pw):
            log("    [workday] signed in")
            return True
        said = _said(f)
        if WRONG_PASSWORD.search(said):
            log(f"    [workday] the stored password does not open this tenant's account ({said[:100]})")
            if asked or not _ask_fix(page, ctx, log, f"“{said[:140]}”"):
                return False
            if _sign_in(page, email, pw):
                log("    [workday] signed in after the fix")
                return True
            log(f"    [workday] STOPPED — still refused after the fix: {_said(f)[:120]}")
            return False
        log(f"    [workday] sign-in refused: {said[:120] or 'no message'} — creating the account")
        if not _click(f, "Create Account") or not _box(f, r"verify.*password"):
            log(f"    [workday] could not sign in and found no account form: {said[:120] or 'no message'}")
            if asked or not _ask_fix(page, ctx, log, f"no account form after “{said[:120] or 'a refused sign-in'}”"):
                return False
            return _box(f, r"^password") is not None and _sign_in(page, email, pw)
    _box(f, r"^email").fill(email)
    _box(f, r"^password").fill(pw)
    verify_box = _box(f, r"verify.*password")
    verify_box.fill(pw)
    for cb in f.get_by_role("checkbox").all():            # the terms box
        if cb.is_visible() and not cb.is_checked():
            cb.check(force=True)
    _send(f, verify_box)
    _quiet(page)
    if _box(f, r"verify.*password"):                     # still on the account form: it was refused
        said = _said(f)
        log(f"    [workday] account not created: {said[:160] or 'no message'}")
        if re.search(r"already (exists|in use|registered|have an account)|account exists", said, re.I):
            if _click(f, "Sign In") or _click(f, "Back to Sign In"):
                return True                               # the loop signs in on its next pass
        if asked or not _ask_fix(page, ctx, log, f"the account was not created: “{said[:140] or 'no message'}”"):
            return False
        return (_click(f, "Sign In") or _click(f, "Back to Sign In")) and _sign_in(page, email, pw)
    body = (f.inner_text("body") or "").lower()
    if "verif" in body and "email" in body:
        from jobpilot.review.ask import ask
        ans = ask(f"wd-verify-{ctx.get('company_slug', 'x')}",
                  "The new Workday account needs your email verified. Open the mail from them, click the "
                  "link, then type 'done' here.", timeout=900, about=f"{ctx.get('company', 'Workday')} (Workday)",
                  slug=ctx.get("company_slug"))
        if not ans:
            log("    [workday] email verification not confirmed in time")
            return False
        page.goto(ctx["url"], wait_until="domcontentloaded", timeout=60000)
        return True
    log("    [workday] account created")
    return True
