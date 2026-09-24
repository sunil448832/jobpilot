"""
platforms/workday.py — Workday: link recognition, JD via the tenant's JSON API, and
a driver for the candidate wizard (My Information → My Experience → Application
Questions → Voluntary Disclosures → Self Identify → Review → Submit).

The generic walker cannot do Workday: an account per tenant, sign-in or create
with an emailed code, and pages that repaint themselves. So this module offers
explore() / submit(), which browser.fill_application and browser.submit_approved
call instead of the walker (stage "prep": stops at Review, screenshots; stage
"submit": walks to Review again, then Submit).
Every control on Workday carries a stable data-automation-id, which is what this
module leans on; anything it does not know by id is handed to the generic
label-based filler (browser.fill_fields), and anything still empty becomes a
review-page question exactly as on the other portals. No LLM here.

Account: one Workday account per company tenant, email + password from
~/.config/jobbot/env (WORKDAY_EMAIL / WORKDAY_PASSWORD). Sign in first; if the
tenant does not know the address, create the account. An emailed verification
code is asked for on Telegram (review.ask), like Greenhouse's.
"""
import datetime as dt
import os
import re
import urllib.parse

from jobpilot.core.config import cfg
from jobpilot.fill.platforms._http import get, html_to_text

ID = "workday"
HOSTS = ("myworkdayjobs.com", "myworkdaysite.com")   # the same wizard on either domain
ROUTE = "mobile"                       # this driver runs the wizard end to end


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


def explore(page, ctx, answers, resolve, rep):
    """Pass 1: the wizard to its Review page, never Submit. walk-shaped result."""
    return WorkdayFlow(page, ctx, answers, resolve, rep, "prep").run()


def walker(page, ctx, answers, resolve, rep):
    """The page-by-page stepper a live session drives (session.py)."""
    return WorkdayFlow(page, ctx, answers, resolve, rep, "prep")


def submit(page, ctx, answers, resolve, rep, it):
    """Pass 2: resume the saved draft — pages it already holds are saved as they
    are, pages he answered on are filled — then press Submit on Review."""
    flow = WorkdayFlow(page, ctx, answers, resolve, rep, "submit")
    res = flow.run()
    clicked = ok = False
    if res["reached_end"] or flow.already:
        clicked, ok = flow.press_submit()
    res.update({"clicked": clicked, "submit_ok": ok, "errs": len(flow.missing), "warnings": flow.warnings})
    return res


STEP_NAMES = ("my information", "my experience", "application questions",
              "voluntary disclosures", "self identify", "review")
OPTION_SEL = ('[role="option"], li[role="option"], [data-automation-id="promptOption"], '
              '[data-automation-id="menuItem"], [role="menuitem"]')
NEXT_BTN = ('button[data-automation-id="bottom-navigation-next-button"], '
            'button[data-automation-id="pageFooterNextButton"], '
            'button:has-text("Save and Continue"), button:has-text("Next")')
SUBMIT_BTN = ('button[data-automation-id="bottom-navigation-next-button"], '
              'button[data-automation-id="pageFooterNextButton"], button:has-text("Submit")')
ERR_SEL = '[data-automation-id="errorMessage"], [data-automation-id="fieldError"], [aria-invalid="true"]'


def creds():
    from jobpilot.core.daily import env
    e = env()
    return e.get("WORKDAY_EMAIL", ""), e.get("WORKDAY_PASSWORD", "")


def _wait(page, ms):
    """A one-off pause, scaled by browser.pace (polling loops are not scaled:
    they already stop as soon as the page is ready)."""
    page.wait_for_timeout(max(150, int(ms * float(cfg("browser.pace", 0.5)))))


# ------------------------------------------------------------------ helpers

def _vis(page, sel, timeout=800):
    try:
        loc = page.locator(sel).first
        return loc.count() > 0 and loc.is_visible(timeout=timeout)
    except Exception:
        return False


def _click(page, sel, timeout=4000):
    from jobpilot.fill.browser import click_field
    if not _vis(page, sel, 600):
        return False
    try:
        click_field(page, sel)
        return True
    except Exception:
        try:
            page.locator(sel).first.click(timeout=timeout, force=True)
            return True
        except Exception:
            return False


def _fill(page, sel, text, warnings, label=""):
    """Fill a plain input by selector; say so when it is not on the page."""
    if text is None or str(text) == "":
        return False
    if not _vis(page, sel, 500):
        return False
    try:
        loc = page.locator(sel).first
        loc.fill(str(text), timeout=4000)
        return True
    except Exception as e:
        warnings.append(f"Workday: could not fill {label or sel}: {str(e)[:60]}")
        return False


_TAGGED = [0]


def by_label(page, pattern, want="input,textarea,button", within=None):
    """A selector for the control under the label matching `pattern` (regex, i),
    or '' — the fallback when a tenant's data-automation-id is not the usual one.
    `within` scopes the search to one block (a work-experience entry)."""
    _TAGGED[0] += 1
    n = _TAGGED[0]
    try:
        found = page.evaluate("""([pat, want, n, within]) => {
          const re = new RegExp(pat, 'i');
          const vis = el => !!el && (el.offsetParent !== null || el.getClientRects().length > 0);
          const root = within ? document.querySelector(within) : document;
          if (!root) return false;
          for (const l of root.querySelectorAll('label, legend')) {
            const t = (l.innerText || '').trim();
            if (!t || !re.test(t)) continue;
            let el = null;
            const f = l.getAttribute('for');
            if (f) { const c = document.getElementById(f); if (c && c.matches(want) && vis(c)) el = c; }
            if (!el) {
              // the label's own field box, else walk up a little until a control appears
              let box = l.closest('[data-automation-id^="formField-"], fieldset') || l.parentElement;
              for (let hops = 0; box && hops < 4 && !el; hops++) {
                el = [...box.querySelectorAll(want)].find(vis) || null;
                if (!el) box = box.parentElement;
              }
            }
            if (!el) continue;
            el.setAttribute('data-jobbot-wd', String(n));
            return true;
          }
          return false;
        }""", [pattern, want, n, within])
    except Exception:
        found = False
    return f'[data-jobbot-wd="{n}"]' if found else ""


def _body(page):
    try:
        return (page.inner_text("body") or "").lower()
    except Exception:
        return ""


def _rank(opts, want):
    w = want.strip().lower()
    return ([o for o in opts if o[1] == w] + [o for o in opts if o[1].startswith(w) and o[1] != w]
            + [o for o in opts if w in o[1] and not o[1].startswith(w)])


def _options(page):
    """Every visible option in the open menu as (locator, text) — read in ONE
    JS pass so a 250-entry country list is ranked whole. Reading the first 60
    one by one once made 'British Indian Ocean Territory' beat 'India'."""
    out = []
    try:
        rows = page.evaluate("""(sel) => [...document.querySelectorAll(sel)].map((o, i) => {
            const r = o.getBoundingClientRect(), cs = getComputedStyle(o);
            const shown = r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
            const noise = o.closest('header, nav, [data-automation-id="selectedItemList"], [data-automation-id="selectedItem"], [data-automation-id^="utility"]');
            return [i, shown && !noise ? (o.innerText || '').trim() : null];
          }).filter(x => x[1])""", OPTION_SEL) or []
        loc = page.locator(OPTION_SEL)
        out = [(loc.nth(i), t.lower()) for i, t in rows]
    except Exception:
        pass
    return out


def _scroll_find(page, want, max_pages=25):
    """A long menu is virtualised: only the visible window is in the DOM.
    Page through it until an option matching `want` renders, or give up."""
    for _ in range(max_pages):
        for o, ot in _rank(_options(page), want):
            return o, ot
        moved = False
        try:
            moved = page.evaluate("""(sel) => {
              const first = document.querySelector(sel);
              if (!first) return false;
              let box = first.parentElement;
              while (box && box !== document.body) {
                const cs = getComputedStyle(box);
                if (/(auto|scroll)/.test(cs.overflowY) && box.scrollHeight > box.clientHeight + 4) break;
                box = box.parentElement;
              }
              if (!box || box === document.body) return false;
              const before = box.scrollTop;
              box.scrollTop = before + Math.max(box.clientHeight - 40, 120);
              return box.scrollTop !== before;
            }""", OPTION_SEL)
        except Exception:
            moved = False
        if not moved:
            break
        page.wait_for_timeout(350)
    return None, ""


def pick_listbox(page, sel, wants, warnings, label=""):
    """Open a <button aria-haspopup=listbox> (or any button) and pick the first
    of `wants` that matches an option; never picks an unmatched option."""
    if not _click(page, sel):
        return ""
    page.wait_for_timeout(600)
    opts = _options(page)
    for want in [w for w in wants if w]:
        hit = next(iter(_rank(opts, want)), None)
        if hit is None and len(opts) > 25:
            o, ot = _scroll_find(page, want)
            hit = (o, ot) if o is not None else None
        if hit is not None:
            o, ot = hit
            try:
                o.scroll_into_view_if_needed(timeout=2000)
                o.click(timeout=3000)
                page.wait_for_timeout(400)
                return ot
            except Exception:
                continue
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    if opts:
        warnings.append(f"Workday: no option for {label or sel} matched {wants[0]!r}; "
                        f"menu offers: {[t for _, t in opts][:10]}")
    return ""


def type_prompt(page, sel, wants, warnings, label="", exact=False):
    """A Workday multiselect/typeahead ("How did you hear", "Field of study",
    country phone code): type, wait for suggestions, click the match."""
    if not _vis(page, sel, 500):
        return ""
    opts = []          # the report below reads it even when no attempt got as far as a menu

    def pills():
        try:
            return (page.locator(sel).first.evaluate(
                "el => { const c = el.closest('[data-automation-id^=\"formField-\"]') || el.parentElement.parentElement;"
                " return [...c.querySelectorAll('[data-automation-id=\"selectedItem\"], [data-automation-id=\"pill\"], [data-automation-id=\"selectedItemList\"] li')]"
                ".map(p => p.innerText.trim()).filter(Boolean).join(' | '); }") or "").strip()
        except Exception:
            return ""

    # A wrong pill from an earlier attempt must go first: Workday keeps it.
    cur = pills().lower()
    keep_pill = any((w.lower() == cur) if exact else (w.lower() in cur) for w in wants if w)
    if cur and not keep_pill:
        try:
            page.locator(sel).first.evaluate(
                "el => { const c = el.closest('[data-automation-id^=\"formField-\"]') || el.parentElement.parentElement;"
                " for (const b of c.querySelectorAll('[data-automation-id=\"DELETE_charm\"], button[aria-label*=\"emove\"], [data-automation-id=\"selectedItem\"] button')) b.click(); }")
            page.wait_for_timeout(600)
        except Exception:
            pass

    for want in [w for w in wants if w]:
        try:
            loc = page.locator(sel).first
            loc.click(timeout=3000)
            loc.fill("")
            loc.type(want[:40], delay=int(cfg("browser.type_delay_ms", 25)))
        except Exception:
            continue
        # Workday prompts SEARCH on Enter; without it they just list everything.
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass
        opts = []
        for _ in range(6):
            page.wait_for_timeout(500)
            opts = _options(page)
            if opts:
                break
        ranked = _rank(opts, want)
        if exact:
            ranked = [x for x in ranked if x[1] == want.strip().lower()]
        hit = next(iter(ranked), None)
        if hit is None and len(opts) > 25:
            hit = _scroll_find(page, want)
            hit = hit if hit[0] is not None and (not exact or hit[1] == want.strip().lower()) else None
        if hit is not None:
            o, ot = hit
            try:
                o.scroll_into_view_if_needed(timeout=2000)
                o.click(timeout=3000)
                page.wait_for_timeout(500)
                return pills() or ot
            except Exception:
                pass
        # Category menus ("Social Media" > "LinkedIn"): open the first category
        # that sounds right and look again.
        cat = None if exact else next((o for o, ot in opts if any(k in ot for k in ("social", "job board", "online", "internet"))), None)
        if cat is not None:
            try:
                cat.click(timeout=3000)
                page.wait_for_timeout(800)
                for o, ot in _rank(_options(page), want):
                    o.click(timeout=3000)
                    page.wait_for_timeout(500)
                    return pills() or ot
            except Exception:
                pass
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        if pills() and want.lower() in pills().lower():
            return pills()
    warnings.append(f"Workday: typeahead {label or sel} took none of {wants[:2]}"
                    + (f"; offered {[t for _, t in opts][:8]}" if opts else ""))
    return ""


def set_date(page, container_sel, ym, warnings, label="", day=None):
    """Month / (day) / year boxes inside a Workday date field. ym = 'YYYY-MM'."""
    if not ym or not _vis(page, container_sel, 500):
        return False
    y, m = ym.split("-")[0], ym.split("-")[1]
    ok = True
    for part, val in (("Month", m), ("Day", day), ("Year", y)):
        if val is None:
            continue
        sel = f'{container_sel} input[data-automation-id="dateSection{part}-input"]'
        if not _vis(page, sel, 400):
            if part != "Day":
                ok = False
            continue
        try:
            loc = page.locator(sel).first
            loc.click(timeout=3000)
            page.keyboard.press("Control+A")
            page.keyboard.type(str(val).lstrip("0") if part == "Day" else str(val), delay=60)
        except Exception as e:
            warnings.append(f"Workday: {label} {part}: {str(e)[:50]}")
            ok = False
    return ok


ALL_STEPS = STEP_NAMES + ("create account", "sign in")


def current_step(page):
    """Which wizard page is showing: the ACTIVE progress-bar step (the bold
    one), else the step heading. Never the page text — the progress bar lists
    every step's name, which once read as 'My Information' on the sign-in page."""
    try:
        t = page.evaluate("""() => {
          const bar = document.querySelector('[data-automation-id="progressBar"]');
          if (!bar) return '';
          const cur = bar.querySelector('[aria-current="step"], [aria-current="true"], [data-automation-id="progressBarActiveStep"]');
          if (cur) return cur.innerText;
          let best = '', bw = 0;
          for (const el of bar.querySelectorAll('li, div, span, p')) {
            const txt = (el.innerText || '').trim();
            if (!txt || txt.length > 60 || el.children.length > 3) continue;
            const w = parseInt(getComputedStyle(el).fontWeight, 10) || 400;
            if (w > bw) { bw = w; best = txt; }
          }
          return bw >= 600 ? best : '';
        }""") or ""
        t = re.sub(r"\s+", " ", t.strip().lower().replace("/", " "))
        t = re.sub(r"^current step \d+ of \d+\s*", "", t)      # screen-reader prefix on the active step
        for s in ALL_STEPS:
            if s in t:
                return "create account" if s == "sign in" else t   # full text: "application questions 1 of 2"
    except Exception:
        pass
    try:
        heads = page.locator("h2, h3, [data-automation-id='pageHeaderTitle']")
        for i in range(min(heads.count(), 12)):
            t = re.sub(r"\s+", " ", (heads.nth(i).inner_text() or "").strip().lower())
            for s in STEP_NAMES:
                if t.startswith(s):
                    return t
    except Exception:
        pass
    return ""


def wait_step_content(page, max_s=20):
    """Workday paints the progress bar first and the step's form a few seconds
    later (a grey skeleton in between). Wait for something actionable."""
    def inputs():
        try:
            return page.evaluate("() => [...document.querySelectorAll('input:not([type=hidden]),textarea,button[aria-haspopup=\"listbox\"]')]"
                                 ".filter(e => e.offsetParent !== null).length")
        except Exception:
            return 0
    for _ in range(max_s * 2):
        if (_vis(page, '[data-automation-id="email"]', 200)
                or _vis(page, '[data-automation-id="applyManually"]', 200)
                or _vis(page, '[data-automation-id="applyButton"]', 200)
                or _vis(page, 'button:has-text("with email")', 200)):
            return True
        # The footer button paints before the form does; a step page is ready
        # only once its controls are there and stop changing.
        if _vis(page, NEXT_BTN, 200):
            n1 = inputs()
            if n1 >= 3:
                page.wait_for_timeout(700)
                if inputs() == n1:
                    return True
        page.wait_for_timeout(500)
    return False


# ------------------------------------------------------------- account

def sign_in(page, email, pw, warnings):
    """Fill the email sign-in form and submit it. Workday lays a transparent
    click_filter overlay over the submit button; that overlay is the real
    target (a click on the button itself can land on its edge and time out).
    Enter in the password box is the fallback."""
    _fill(page, '[data-automation-id="email"]', email, warnings, "email")
    _fill(page, '[data-automation-id="password"]', pw, warnings, "password")
    clicked = (_click(page, 'div[data-automation-id="click_filter"]')
               or _click(page, 'button[data-automation-id="signInSubmitButton"]'))
    if not clicked:
        try:
            page.locator('[data-automation-id="password"]').first.press("Enter")
        except Exception:
            pass
    _wait(page, 4000)


def login_or_create(page, ctx, warnings):
    """We are on a sign-in / create-account page. Sign in; if the tenant does
    not know us, create the account. Returns True once past the gate."""
    email, pw = creds()
    if not (email and pw):
        warnings.append("Workday: WORKDAY_EMAIL / WORKDAY_PASSWORD missing in ~/.config/jobbot/env")
        return False

    def on_gate():
        return _vis(page, '[data-automation-id="email"]', 600) and _vis(page, '[data-automation-id="password"]', 600)

    if not on_gate():
        return True
    # Prefer the sign-in form if the create form is what opened.
    if _vis(page, '[data-automation-id="verifyPassword"]', 500):
        _click(page, 'button[data-automation-id="signInLink"], a[data-automation-id="signInLink"]')
        _wait(page, 1200)
    sign_in(page, email, pw, warnings)

    def err_text():
        try:
            loc = page.locator('[data-automation-id="errorMessage"], [data-automation-id="alertMessage"], [role="alert"]')
            return " | ".join((loc.nth(i).inner_text() or "").strip() for i in range(min(loc.count(), 3))).strip(" |")
        except Exception:
            return ""

    def on_chooser():
        return _vis(page, 'button:has-text("Sign in with email"), button:has-text("with email")', 400)

    if not on_gate() and not on_chooser():
        warnings.append("Workday: signed in")
        return True
    if on_chooser():
        # Bounced to the social chooser: take the email route and try once more
        # before concluding anything about the account.
        _click(page, 'button:has-text("Sign in with email"), button:has-text("with email")')
        _wait(page, 2000)
        if on_gate():
            sign_in(page, email, pw, warnings)
            if not on_gate() and not on_chooser():
                warnings.append("Workday: signed in (second attempt)")
                return True
    err = err_text()
    if err:
        warnings.append(f"Workday: sign-in said: {err[:140]}")
    if re.search(r"wrong (email address or )?password|incorrect password|account (might|may) be locked|account is locked", err, re.I):
        # The account exists and the stored password does not open it (NVIDIA).
        # Creating an account is wrong here, and every further try moves the
        # account toward a lock. Stop and leave it to him.
        warnings.append("Workday: STOPPED — the stored password does not open this tenant's account; "
                        "reset it on the employer's Workday (or update WORKDAY_PASSWORD) before retrying")
        return False
    # Unknown address -> create the account with the same credentials.
    if _click(page, 'button[data-automation-id="createAccountLink"], a[data-automation-id="createAccountLink"]'):
        _wait(page, 1500)
        _fill(page, '[data-automation-id="email"]', email, warnings, "email")
        _fill(page, '[data-automation-id="password"]', pw, warnings, "password")
        _fill(page, '[data-automation-id="verifyPassword"]', pw, warnings, "verify password")
        for cb in ('input[data-automation-id="createAccountCheckbox"]',
                   '[data-automation-id="createAccountCheckbox"] input', 'input[type="checkbox"]'):
            if _vis(page, cb, 400):
                try:
                    page.locator(cb).first.check(timeout=3000, force=True)
                except Exception:
                    pass
                break
        _click(page, 'div[data-automation-id="click_filter"]') or _click(page, 'button[data-automation-id="createAccountSubmitButton"]')
        _wait(page, 4000)
        body = _body(page)
        cerr = err_text()
        if cerr:
            warnings.append(f"Workday: create-account said: {cerr[:140]}")
        if "verif" in body and "email" in body:
            _verify_email(page, ctx, warnings)
        if not on_gate() and not on_chooser():
            warnings.append("Workday: account created for this tenant")
            return True
        if on_chooser() and not cerr:
            # Bounced to the chooser after "create". That alone is NOT proof the
            # address needs verifying: on Berkadia it meant the account already
            # existed and the sign-in click had missed, and the prompt sent him
            # looking for an email that was never sent. Sign in first.
            _click(page, 'button:has-text("Sign in with email"), button:has-text("with email")')
            _wait(page, 2000)
            if on_gate():
                sign_in(page, email, pw, warnings)
                if not on_gate() and not on_chooser():
                    warnings.append("Workday: signed in (account already existed)")
                    return True
            said = (err_text() + " " + _body(page)[:3000]).lower()
            if not re.search(r"verif|activat|confirm your (email|account)", said):
                warnings.append(f"Workday: could not sign in — {err_text()[:140] or 'no message from Workday'}")
                return False
            from jobpilot.review.ask import ask
            warnings.append("Workday: the tenant says the email must be verified before sign-in")
            ans = ask(f"wd-verify-{ctx.get('company_slug', 'x')}",
                      f"{ctx.get('company', 'Workday')}: the new Workday account needs your email verified. "
                      f"Open the mail from them, click the link, then type 'done' here.", timeout=900)
            if ans:
                page.goto(ctx["url"], wait_until="domcontentloaded", timeout=60000)
                _wait(page, 3000)
                return login_or_create(page, ctx, warnings)
            return False
        err = cerr or err
    warnings.append(f"Workday: could not sign in or create an account ({err[:120] or 'no message'})")
    return False


def _verify_email(page, ctx, warnings):
    """Some tenants email a code (or a link) before the account works."""
    from jobpilot.review.ask import ask
    code_inputs = page.locator('input[autocomplete="one-time-code"], input[maxlength="1"], input[name*="code" i], '
                               'input[id*="code" i], input[data-automation-id*="code" i]')
    if code_inputs.count():
        code = ask(f"wd-code-{ctx.get('company_slug', 'x')}",
                   f"{ctx.get('company', 'Workday')}: the Workday account for this employer emailed a "
                   f"verification code to your address. Paste it here.", timeout=900)
        code = re.sub(r"\s+", "", code or "")
        if not code:
            warnings.append("Workday: verification code not received in time")
            return
        try:
            code_inputs.first.click(timeout=3000, force=True)
            page.keyboard.type(code, delay=90)
            page.wait_for_timeout(800)
            if code_inputs.count() >= len(code) and not (code_inputs.nth(1).input_value() or "").strip():
                for i, ch in enumerate(code[:code_inputs.count()]):
                    code_inputs.nth(i).fill(ch)
            _click(page, 'button[type="submit"], button[data-automation-id*="Submit"], button[data-automation-id*="verify" i]')
            _wait(page, 3000)
        except Exception as e:
            warnings.append(f"Workday: code entry failed: {str(e)[:60]}")
        return
    ans = ask(f"wd-link-{ctx.get('company_slug', 'x')}",
              f"{ctx.get('company', 'Workday')}: Workday sent a verification EMAIL for the new account. "
              f"Open it, click the link, then type 'done' here.", timeout=900)
    if ans:
        page.reload(wait_until="domcontentloaded")
        _wait(page, 3000)
    else:
        warnings.append("Workday: email verification not confirmed in time")


# -------------------------------------------------------------- wizard

def start_application(page, ctx, warnings):
    """From the posting page to the first wizard step. Handles the sign-in gate
    appearing before or after the Apply click, and the 'how to apply' modal."""
    glitches = 0
    for attempt in range(6):
        wait_step_content(page)
        if "something went wrong" in _body(page)[:3000]:
            # Workday's own transient error page. Refresh once as it asks; if it
            # persists, leave the broken apply URL and re-enter from the posting
            # (the saved draft is resumed from there).
            glitches += 1
            if glitches == 1:
                warnings.append("Workday: 'something went wrong' page — refreshed")
                page.reload(wait_until="domcontentloaded")
            else:
                warnings.append("Workday: error page persisted — re-entering from the job posting")
                page.goto(ctx["url"], wait_until="domcontentloaded", timeout=60000)
            _wait(page, 4000)
            continue
        step = current_step(page)
        if step and not step.startswith("create account") and _vis(page, NEXT_BTN, 800):
            return True
        # Some tenants (Palo Alto Networks) offer Apple / Google / LinkedIn sign-in
        # first; the email form is behind "Sign in with email".
        if not _vis(page, '[data-automation-id="email"]', 400):
            for sel in ('button:has-text("Sign in with email")', 'button:has-text("Sign In with email")',
                        'button:has-text("Continue with email")', '[data-automation-id="signInWithEmail"]',
                        'button:has-text("with email")'):
                if _click(page, sel):
                    _wait(page, 2000)
                    break
        if _vis(page, '[data-automation-id="email"]', 600) or step.startswith("create account"):
            if not login_or_create(page, ctx, warnings):
                return False
            _wait(page, 2000)
            continue
        if _vis(page, '[data-automation-id="applyManually"]', 600):
            _click(page, '[data-automation-id="applyManually"]')
            _wait(page, 3000)
            continue
        if _vis(page, '[data-automation-id="applyButton"]', 600):
            _click(page, '[data-automation-id="applyButton"]')
            _wait(page, 3000)
            continue
        if _vis(page, 'a[data-automation-id="adventureButton"]', 600):   # "Apply" on some tenants
            _click(page, 'a[data-automation-id="adventureButton"]')
            _wait(page, 3000)
            continue
        # A saved draft turns the posting's Apply into "Continue Application" —
        # which every submit after an exploration meets (Berkadia). Text last,
        # for tenants whose buttons carry none of the ids above.
        resumed = False
        for sel in ('button:has-text("Continue Application")', 'a:has-text("Continue Application")',
                    'button:has-text("Continue Applying")', 'a:has-text("Continue Applying")',
                    'a[role="button"]:text-is("Apply")', 'button:text-is("Apply")'):
            if _vis(page, sel, 500):
                _click(page, sel)
                _wait(page, 3000)
                resumed = True
                break
        if resumed:
            continue
        _wait(page, 2000)
    step = current_step(page)
    ok = _vis(page, NEXT_BTN, 800) and bool(step) and not step.startswith("create account")
    if not ok:
        warnings.append("Workday: application wizard did not open (no Apply button / step page found)")
    return ok


_SEC = [0]


def container_by_heading(page, heading_re, min_controls=2, max_text=6000):
    """Selector for the smallest block under a heading ("Work Experience 1")
    that holds at least `min_controls` form controls — the tenant-independent
    way to find a repeating entry when the data-automation-ids differ."""
    _SEC[0] += 1
    n = _SEC[0]
    try:
        ok = page.evaluate("""([re_, minC, maxT, n]) => {
          const re = new RegExp(re_, 'i');
          const vis = el => el.offsetParent !== null;
          const heads = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"],legend,strong,b,div,span,p')]
            .filter(h => vis(h) && h.children.length <= 2 && re.test((h.innerText || '').trim()) && (h.innerText || '').trim().length < 60);
          for (const h of heads) {
            let box = h.parentElement;
            for (let hops = 0; box && hops < 8; hops++) {
              const ctrls = [...box.querySelectorAll('input:not([type=hidden]),textarea,select,button[aria-haspopup="listbox"]')].filter(vis);
              const txt = (box.innerText || '');
              if (ctrls.length >= minC && !/save and continue/i.test(txt)) {
                if (txt.length > maxT) break;
                box.setAttribute('data-jobbot-sec', String(n));
                return true;
              }
              box = box.parentElement;
            }
          }
          return false;
        }""", [heading_re, min_controls, max_text, n])
    except Exception:
        ok = False
    return f'[data-jobbot-sec="{n}"]' if ok else ""


def _val(page, sel):
    try:
        return (page.locator(sel).first.input_value() or "").strip()
    except Exception:
        return ""


def set_date_any(page, blk, label_pattern, ym, warnings, label=""):
    """A Workday date field is either a month box + year box (Mastercard) or one
    MM/YYYY (or YYYY) box (Palo Alto). Find it by its label inside the block."""
    if not ym:
        return False
    y, m = ym.split("-")[0], ym.split("-")[1]
    box = by_label(page, label_pattern, "input", within=blk)
    if not box:
        return False
    try:
        info = page.locator(box).first.evaluate(
            "el => { const ff = el.closest('[data-automation-id^=\"formField-\"]') || el.parentElement.parentElement;"
            " const ins = [...ff.querySelectorAll('input')].filter(i => i.offsetParent !== null);"
            " return {n: ins.length, ph: ins.map(i => i.placeholder || i.getAttribute('aria-label') || ''),"
            "         ids: ins.map(i => i.getAttribute('data-automation-id') || '')}; }")
    except Exception:
        info = {"n": 1, "ph": [""], "ids": [""]}
    try:
        has_month = any("dateSectionMonth" in i for i in info["ids"])
        has_year = any("dateSectionYear" in i for i in info["ids"])
        if has_month or has_year:
            ff = page.locator(box).first
            cont = ff.locator("xpath=ancestor::*[starts-with(@data-automation-id,'formField-')][1]")
            # a year-only box (education) takes just the year; typing MM/YYYY into it gave "7201"
            for part, val in ((("Month", m),) if has_month else ()) + (("Year", y),):
                inp = cont.locator(f'input[data-automation-id="dateSection{part}-input"]').first
                try:
                    inp.click(timeout=1500)
                except Exception:
                    try:
                        inp.click(timeout=1500, force=True)
                    except Exception:
                        inp.evaluate("el => el.focus()")
                page.keyboard.press("Control+A")
                page.keyboard.type(val, delay=80)
                page.wait_for_timeout(200)
                if (inp.input_value() or "").strip().lstrip("0") != val.lstrip("0"):
                    inp.evaluate("(el, v) => { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;"
                                 " set.call(el, v); el.dispatchEvent(new Event('input', {bubbles: true}));"
                                 " el.dispatchEvent(new Event('change', {bubbles: true})); }", val)
            page.keyboard.press("Tab")
            return True
        ph = (info["ph"][0] or "").upper()
        text = y if ("YYYY" in ph and "MM" not in ph) else f"{m}/{y}"
        loc = page.locator(box).first
        try:
            loc.click(timeout=2500)
        except Exception:
            try:
                loc.click(timeout=2500, force=True)
            except Exception:
                loc.evaluate("el => el.focus()")
        page.keyboard.press("Control+A")
        page.keyboard.type(text, delay=70)
        page.keyboard.press("Tab")
        page.wait_for_timeout(300)
        if not (loc.input_value() or "").strip():
            # a masked widget that ignores typing: set it and fire the events
            loc.evaluate("(el, v) => { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;"
                         " set.call(el, v); el.dispatchEvent(new Event('input', {bubbles: true}));"
                         " el.dispatchEvent(new Event('change', {bubbles: true})); el.blur(); }", text)
        return True
    except Exception as e:
        warnings.append(f"Workday: {label or label_pattern} date: {str(e)[:60]}")
        return False


# ------------------------------------------------ repeating sections (Workday-wide)
# Work Experience and Education are lists in answers.yaml, shown as numbered
# blocks. One filler for both: find the section, make one block per entry, map
# each labelled control in a block to a field of the entry, and fill it with the
# Workday widget its kind needs. Anything a tenant does differently belongs in
# that application's hooks.py (fill_step / after_fill), not here.

SECTIONS = (
    {"what": "work-experience", "list": "employment", "ids": ("workExperience",), "label": "Work-Experience",
     "heading": "work experience",
     "fields": ((r"^job title|^position|^title", "title"), (r"^company|^employer", "employer"),
                (r"^location", "location"), (r"currently work here|i currently work", "current"),
                (r"^from|^start", "start_date"), (r"^to\b|^end", "end_date"),
                (r"description|responsibilit", "summary"))},
    {"what": "education", "list": "education", "ids": ("education",), "label": "Education",
     "heading": "education",
     "fields": ((r"school|university|institution", "institution"), (r"^degree", "degree"),
                (r"field of study|^major", "field_of_study"), (r"gpa|overall result|^grade", "gpa"),
                (r"^from|^start", "start_date"), (r"^to\b|^end", "end_date"))},
)

BLOCK_CONTROLS_JS = r"""(blk) => {
  const root = document.querySelector(blk); if (!root) return [];
  const out = []; let i = 0;
  for (const ff of root.querySelectorAll('[data-automation-id^="formField-"], fieldset, [role="group"]')) {
    if (ff.querySelector('[data-automation-id^="formField-"]') && ff.matches('[role="group"]')) continue;
    const lab = ff.querySelector('label, legend'); if (!lab) continue;
    const text = (lab.innerText || '').split('\n')[0].replace(/\*\s*$/, '').trim(); if (!text) continue;
    const tag = 'jb' + (i++); ff.setAttribute('data-jobbot-blk', tag);
    const sel = `[data-jobbot-blk="${tag}"]`;
    const kind = ff.querySelector('input[type=checkbox]') ? 'checkbox'
      : ff.querySelector('[data-automation-id^="dateSection"], input[placeholder*="YYYY" i], [data-automation-id*="date" i] input') ? 'date'
      : ff.querySelector('button[aria-haspopup="listbox"]') ? 'listbox'
      : ff.querySelector('[data-automation-id*="multiSelect" i], [data-automation-id*="searchBox" i], [data-automation-id*="prompt" i], [data-uxi-widget-type="selectinput"]') ? 'prompt'
      : ff.querySelector('textarea') ? 'textarea' : 'text';
    const inp = ff.querySelector('input:not([type=hidden]), textarea');
    out.push({label: text, kind, sel, value: inp ? (inp.value || '') : ((ff.querySelector('button') || {}).innerText || '').trim()});
  }
  return out;
}"""


def _candidates(field, value, answers):
    """The true value, in the forms a Workday list may hold it — and, for a school
    or degree, the tenant's own fallback, never a nearest guess."""
    v = str(value)
    if field == "institution":
        country = (answers.get("location") or {}).get("country", "India")
        return [v, v.replace(",", ""), re.sub(r"\s*\(.*?\)", "", v).replace(",", "").strip(), f"Other - {country}", "Other"]
    if field == "degree":
        short = re.search(r"\(([^)]+)\)", v)
        return [v, short.group(1) if short else "", v.split(" of ")[0], "Master" if "master" in v.lower() else "Bachelor"]
    return [v]


def fill_sections(page, answers, warnings, filled):
    """Every repeating section on the page, from answers.yaml. Returns missing records."""
    missing = []
    for sp in SECTIONS:
        entries = answers.get(sp["list"]) or []

        def panel(n, sp=sp):
            for sel in [f'[data-automation-id="{i}-{n}"]' for i in sp["ids"]] + \
                       [f'[role="group"][aria-labelledby="{sp["label"]}-{n}-panel"]']:
                if _vis(page, sel, 250):
                    return sel
            return container_by_heading(page, rf"^{sp['heading']}\s*{n}$", 2, 4000)

        sec = next((x for x in [f'[data-automation-id="{i}Section"]' for i in sp["ids"]] +
                    [f'[role="group"][aria-labelledby="{sp["label"]}-section"]'] if _vis(page, x, 300)), None) \
            or container_by_heading(page, rf"^{sp['heading']}$", 1, 12000)
        if not sec or not entries:
            continue
        for n, e in enumerate(entries, 1):
            blk = panel(n)
            if not blk:
                for add in (f'{sec} button[data-automation-id="add-button"]', f'{sec} button:has-text("Add Another")',
                            f'{sec} button:has-text("Add")'):
                    if _click(page, add):
                        _wait(page, 1200)
                        blk = panel(n)
                        if blk:
                            break
            if not blk:
                warnings.append(f"Workday: could not add {sp['what']} entry {n}")
                break
            for c in page.evaluate(BLOCK_CONTROLS_JS, blk) or []:
                field = next((f for rx, f in sp["fields"] if re.search(rx, c["label"], re.I)), None)
                if not field:
                    continue
                val = e.get(field)
                if field == "end_date" and e.get("current"):
                    continue
                if val in (None, "") or str(val).upper() == "TODO":
                    continue
                if c["kind"] == "checkbox":
                    if field == "current" and val:
                        try:
                            page.locator(f'{c["sel"]} input[type=checkbox]').first.check(timeout=3000, force=True)
                        except Exception:
                            pass
                    continue
                if c["value"].strip() and c["kind"] != "date":
                    continue                                  # the draft or the resume parse already holds it
                label = f"{c['label']} {n}"
                if c["kind"] == "date":
                    set_date_any(page, blk, "^" + re.escape(c["label"][:12]), val, warnings, label)
                elif c["kind"] == "listbox":
                    got = pick_listbox(page, f'{c["sel"]} button[aria-haspopup="listbox"]',
                                       _candidates(field, val, answers), warnings, label)
                    if got:
                        filled[label] = got
                elif c["kind"] == "prompt":
                    got = type_prompt(page, f'{c["sel"]} input', _candidates(field, val, answers), warnings, label,
                                      exact=field == "institution")
                    if got:
                        filled[label] = got
                    elif field == "institution":
                        missing.append({"label": c["label"], "required": True, "kind": "text", "options": [],
                                        "reason": f"school {str(val)[:40]!r} is not in this employer's list and it offers no 'Other'"})
                else:
                    _fill(page, f'{c["sel"]} {"textarea" if c["kind"] == "textarea" else "input"}', str(val), warnings, label)
                    filled[label] = str(val)[:60]
        # entries past answers.yaml are the resume parse's: nothing true goes in them
        for _ in range(10):
            extra = panel(len(entries) + 1)
            if not extra or not _click(page, f'{extra} button:has-text("Delete")'):
                break
            _wait(page, 1200)
            warnings.append(f"Workday: removed an extra {sp['what']} entry added by the resume parse")
    return missing


def upload_resume(page, answers, ctx, warnings, filled):
    """Resume first on its step: Workday parses it and repaints the sections."""
    from jobpilot.fill.browser import resume_path, wait_dom_stable
    rp = resume_path(answers, ctx["company_slug"], ctx["portal"])
    up = 'input[data-automation-id="file-upload-input-ref"], input[type="file"]'
    if not rp or not page.locator(up).count():
        return
    if "successfully uploaded" in _body(page) or _vis(page, '[data-automation-id="file-upload-successful"]', 300):
        return
    try:
        page.locator(up).first.set_input_files(rp)
        _wait(page, 3500)
        wait_dom_stable(page, max_s=15, quiet=2)
        filled["Resume"] = os.path.basename(rp)
    except Exception as e:
        warnings.append(f"Workday: resume upload failed: {str(e)[:60]}")


def workday_resolve(resolve, answers):
    """The generic resolver, plus the phone fields every Workday tenant splits:
    a country-code picker, a national number, an extension left empty."""
    p, loc = answers["personal"], answers["location"]
    code = f"{loc['country']} (+{str(p['phone_country_code']).lstrip('+')})"

    def r(label):
        L = (label or "").lower()
        if re.search(r"country phone code|phone code", L):
            return code
        if re.search(r"^phone number|^mobile number", L):
            return p["phone_national"]
        if re.search(r"phone extension|^extension", L):
            return None
        return resolve(label)
    return r


def fill_self_identify(page, answers, warnings, filled):
    """The disability form wants a typed name and today's date next to the choice."""
    today = dt.date.today()
    _fill(page, 'input[data-automation-id="name"]', answers["personal"]["full_name"], warnings, "name")
    if _vis(page, '[data-automation-id="dateSignedOn"]', 400):
        set_date(page, '[data-automation-id="dateSignedOn"]', f"{today:%Y-%m}", warnings, "date signed", day=today.day)
    elif _vis(page, '[data-automation-id="formField-dateSignedOn"]', 400):
        set_date(page, '[data-automation-id="formField-dateSignedOn"]', f"{today:%Y-%m}", warnings, "date signed", day=today.day)
    filled["Self-identify signed"] = f"{today:%Y-%m-%d}"


def collect_errors(page):
    """Field-level validation after a 'Save and Continue' that did not advance."""
    out = []
    try:
        out = page.evaluate("""() => {
          const seen = new Set(), res = [];
          const labelOf = el => {
            const ff = el.closest('[data-automation-id^="formField-"], fieldset, .formField') || el.parentElement;
            const l = ff && (ff.querySelector('label, legend') || null);
            let t = l ? l.innerText : (el.getAttribute('aria-label') || '');
            if (!t && el.id) { const x = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (x) t = x.innerText; }
            return (t || '').split('\\n')[0].trim();
          };
          for (const el of document.querySelectorAll('[aria-invalid="true"]')) {
            const lab = labelOf(el); if (!lab || seen.has(lab)) continue; seen.add(lab);
            const isList = el.tagName === 'BUTTON' || el.getAttribute('aria-haspopup') === 'listbox';
            res.push({label: lab, kind: isList ? 'dropdown' : (el.type === 'radio' || el.type === 'checkbox' ? 'choice' : 'text')});
          }
          for (const e of document.querySelectorAll('[data-automation-id="errorMessage"], [data-automation-id="fieldError"]')) {
            const t = (e.innerText || '').trim(); if (!t) continue;
            const ff = e.closest('[data-automation-id^="formField-"], fieldset');
            const lab = ff ? labelOf(ff.querySelector('input,button,textarea,select') || ff) : '';
            const key = lab || t; if (seen.has(key)) continue; seen.add(key);
            res.push({label: lab || t.replace(/^error:?\\s*/i, ''), kind: 'text', text: t});
          }
          return res;
        }""") or []
    except Exception:
        pass
    return out


def next_step(page, before, answers=None):
    """Click Save and Continue; report whether the wizard moved on."""
    from jobpilot.fill.browser import wait_dom_stable
    if not _click(page, NEXT_BTN):
        return False, [{"label": "Save and Continue", "kind": "text", "text": "button not found"}]
    _wait(page, 2500)
    wait_dom_stable(page, max_s=12, quiet=2)
    after = current_step(page)
    if after and after != before:
        return True, []
    errs = collect_errors(page)
    if not errs and after == before:
        errs = [{"label": before, "kind": "text", "text": "step did not advance and showed no field error"}]
    return False, errs


CONFIRMED = ("congratulations", "application submitted", "thank you for applying", "successfully submitted",
             "your application has been", "application received")


class WorkdayFlow:
    """The wizard page by page — the same interface as walk.Walk (start, fill,
    next, status, finalize, run), so a live session (and a Claude run driving
    it) can stop on a page, fix it, fill that page again and go on, instead of
    signing in and re-walking every page after each fix.

    stage "prep" (exploration) fills every page. stage "submit" fast-forwards a
    page the saved draft already holds — nothing required empty, nothing he
    answered on it — with a plain Save and Continue."""

    def __init__(self, page, ctx, answers, resolve, rep, stage="prep"):
        from jobpilot.fill import hooks
        self.page, self.ctx, self.answers, self.resolve, self.rep, self.stage = page, ctx, answers, resolve, rep, stage
        self.warnings, self.filled, self.missing, self.questions = [], {}, [], []
        self.steps, self.step, self.n = [], "", 0
        self.done = self.stuck = self.already = False
        self.page_missing, self.page_filled = {}, {}
        self.know = hooks.knowledge("workday", ctx.get("company_slug"))

    # ---- moving -----------------------------------------------------------
    def reload_knowledge(self):
        from jobpilot.fill import platforms, hooks
        platforms.reload()
        self.know = hooks.knowledge("workday", self.ctx.get("company_slug"))
        return self.know.describe()

    def _arrive(self):
        """Settle on whatever step is showing: recover Workday's error page,
        pass the account gate, and note the step."""
        page = self.page
        for _ in range(4):
            wait_step_content(page)
            if "something went wrong" in _body(page)[:3000]:
                self.warnings.append("Workday: 'something went wrong' mid-wizard — refreshed")
                page.reload(wait_until="domcontentloaded")
                _wait(page, 3000)
                continue
            step = current_step(page)
            if step.startswith("create account"):
                if not login_or_create(page, self.ctx, self.warnings):
                    self.stuck = True
                    return
                _wait(page, 1500)
                continue
            break
        self.step = current_step(page)
        self.n += 1
        self.steps.append(self.step or "?")
        self.rep.step = self.step
        self.rep.pages = [p for p in self.rep.pages if p.get("n") != self.n] + [{"n": self.n, "heading": self.step}]
        if self.step.startswith("review"):
            self.done = True
            self.rep.reached_end = True

    def start(self):
        body = _body(self.page)[:4000]
        if "already applied for this job" in body or "you've already applied" in body:
            # Workday remembers. On the submit leg this is the confirmation we need;
            # on prep it means a duplicate that must not be queued again.
            self.warnings.append("Workday: 'You've already applied for this job'")
            self.already = self.done = True
            return self
        if not start_application(self.page, self.ctx, self.warnings):
            self.stuck = True
            return self
        self._arrive()
        return self

    def _needs_fill(self):
        """Submit leg only: does this saved page need anything typed?"""
        if self.stage != "submit":
            return True
        mine = [r for r in (self.rep.recipes or []) if (r.get("step") or "") == self.step
                and r.get("answered_by") == "applicant"]
        if mine:
            return True
        from jobpilot.fill.browser import EXTRACT_JS
        try:
            fields = self.page.evaluate(EXTRACT_JS)
            # A checkbox's value is always "on": ask the box itself. Workday does
            # not keep consent boxes in a saved draft (Capital One's terms box).
            unticked = self.page.evaluate("""() => {
              const need = [...document.querySelectorAll('input[type=checkbox], input[type=radio]')]
                .filter(e => e.getClientRects().length && (e.required || e.getAttribute('aria-required') === 'true'
                        || !!(e.closest('[data-automation-id^="formField-"], fieldset') || {querySelector: () => null}).querySelector('abbr')));
              const groups = {};
              for (const e of need) { const k = e.name || e.id; (groups[k] = groups[k] || []).push(e); }
              return Object.values(groups).some(g => !g.some(e => e.checked));
            }""")
        except Exception:
            return True
        if unticked:
            return True
        return any(f.get("required") and not (f.get("value") or "").strip()
                   and f["type"] not in ("file", "checkbox", "radio", "buttongroup") for f in fields)

    def fill(self, force=False):
        """Fill the current step (again, if asked again). A company hook may
        take over a step: fill_step(page, step, flow) -> True when it did.
        force: fill even if the saved draft seems to hold the page."""
        from jobpilot.fill.browser import harvest_questions, fill_fields
        if self.done or self.stuck:
            return self.status()
        page, step = self.page, self.step
        before_m = len(self.missing)
        if not force and not self._needs_fill():
            self.page_filled[self.n], self.page_missing[self.n] = [], []
            return self.status()
        hook = self.know.custom("fill_step")
        resolve = workday_resolve(self.resolve, self.answers)
        try:
            if hook and hook(page, step, self):
                pass                                          # the application's hooks took this step
            else:
                if step.startswith("my experience"):
                    upload_resume(page, self.answers, self.ctx, self.warnings, self.filled)
                    self.missing.extend(fill_sections(page, self.answers, self.warnings, self.filled))
                elif step.startswith("self identify"):
                    fill_self_identify(page, self.answers, self.warnings, self.filled)
                # the step's other labelled controls; blocks above were filled from answers.yaml
                self.ctx["trust_prefilled"] = step.startswith("my experience")
                try:
                    f, w, m = fill_fields(page, resolve, self.answers, self.ctx)
                finally:
                    self.ctx.pop("trust_prefilled", None)
                self.filled.update(f)
                self.page_filled[self.n] = sorted(f)
                self.warnings.extend(x for x in w if "REQUIRED" in x or "COULD NOT" in x or "placeholder" in x)
                self.missing.extend(m)
            after = self.know.custom("after_fill")
            if after:
                after(page, step, self)                        # the application's hooks: extra touches
            if self.stage == "prep":
                self.questions.extend(q for q in harvest_questions(page, resolve, self.warnings)
                                      if q.get("required") or len(q.get("label", "")) > 60)
        except Exception as e:
            self.warnings.append(f"Workday: step '{step}' raised {type(e).__name__}: {str(e)[:80]}")
        self.page_missing[self.n] = self.missing[before_m:]
        return self.status()

    def next(self):
        if self.done:
            return False, "already on the last page (Review)"
        if self.stuck:
            return False, "stuck — fix the page, retry, then next"
        ok, errs = next_step(self.page, self.step, self.answers)
        if not ok and errs and not getattr(self, "_refilled", None) == self.n:
            # Fields that render late (Capital One's Prefix appears only after
            # Country repaints the page) are missed by the first fill. Fill the
            # page once more and save again before calling it stuck.
            self._refilled = self.n
            self.fill(force=True)            # the page said no: fill it for real, no fast-forward
            ok, errs = next_step(self.page, self.step, self.answers)
        if ok:
            self._arrive()
            return True, f"on '{self.step}'"
        for e in errs:
            m = {"label": e.get("label", ""), "required": True, "kind": e.get("kind", "text"), "options": [],
                 "reason": "Workday: " + (e.get("text") or "field rejected on Save and Continue"), "page": self.n}
            self.missing.append(m)
            self.page_missing.setdefault(self.n, []).append(m)
        self.warnings.append(f"Workday: '{self.step}' would not save — {[e.get('label') for e in errs][:5]}")
        self.stuck = True
        return False, self.warnings[-1]

    def retry(self):
        """Fill the current page again (after a fix) and clear the stuck flag."""
        self.stuck = False
        return self.fill()

    # ---- reporting --------------------------------------------------------
    def status(self):
        seen, rem = set(), []
        for m in self.page_missing.get(self.n, []):
            k = (m.get("label") or "")[:90].lower()
            if k and k not in seen:
                seen.add(k)
                rem.append({k2: m[k2] for k2 in ("label", "kind", "options", "reason") if k2 in m})
        return {"page": self.n, "heading": self.step, "controls": None,
                "filled_on_page": self.page_filled.get(self.n, []), "filled_total": len(self.filled),
                "placeholders": dict(self.rep.placeholders), "remaining": rem,
                "next": None if self.done else {"text": "Save and Continue"}, "submit": {"text": "Submit"},
                "stuck": self.stuck, "reached_end": self.done, "knowledge": self.know.describe(),
                "pages": [{"n": i + 1, "heading": h} for i, h in enumerate(self.steps)]}

    def finalize(self):
        if not self.done and not self.already:
            self.warnings.append("Workday: did not reach the Review step — see the screenshot "
                                 f"(steps seen: {', '.join(self.steps) or 'none'})")
        for i, q in enumerate(self.questions):
            q["qid"] = i
        return {"filled": self.filled, "warnings": self.warnings, "missing": self.missing,
                "questions": self.questions, "pages_done": len(self.steps), "steps": self.steps,
                "reached_end": self.done}

    def run(self):
        self.start()
        while not self.done and not self.stuck:
            self.fill()
            if self.stuck or self.done:
                break
            ok, _ = self.next()
            if not ok:
                break
        return self.finalize()

    def press_submit(self):
        """Review -> Submit. Success is Workday's confirmation (it can take 20s+)."""
        page = self.page
        if self.already:
            return True, True
        if not _click(page, SUBMIT_BTN):
            self.warnings.append("Workday: Submit button not found on Review")
            return False, False
        _wait(page, 3000)
        for _ in range(int(cfg("browser.submit_poll_s", 30))):
            if any(w in _body(page) for w in CONFIRMED) or _vis(
                    page, '[data-automation-id="applicationSubmitted"], [data-automation-id="applyFlowSubmittedTitle"]', 300):
                return True, True
            page.wait_for_timeout(1000)
        for e in collect_errors(page):
            self.missing.append({"label": e.get("label", ""), "required": True, "kind": e.get("kind", "text"),
                                 "options": [], "reason": "Workday: " + (e.get("text") or "rejected on Submit")})
        self.warnings.append("Workday: no confirmation page after Submit")
        return True, False
