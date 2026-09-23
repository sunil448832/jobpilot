"""
workday.py — drive a Workday candidate wizard (My Information → My Experience →
Application Questions → Voluntary Disclosures → Self Identify → Review → Submit).

Called by browser.fill_application (stage "prep": stops at Review, screenshots)
and browser.submit_approved (stage "submit": walks to Review again, then Submit).
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

from jobpilot.core.config import cfg

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


def by_label(page, pattern, want="input,textarea,button"):
    """A selector for the control under the label matching `pattern` (regex, i),
    or '' — the fallback when a tenant's data-automation-id is not the usual one."""
    _TAGGED[0] += 1
    n = _TAGGED[0]
    try:
        found = page.evaluate("""([pat, want, n]) => {
          const re = new RegExp(pat, 'i');
          const vis = el => !!el && (el.offsetParent !== null || el.getClientRects().length > 0);
          for (const l of document.querySelectorAll('label, legend')) {
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
        }""", [pattern, want, n])
    except Exception:
        found = False
    return f'[data-jobbot-wd="{n}"]' if found else ""


def click_radio(page, question_pattern, want):
    """Tick the radio whose own label is `want` inside the group whose question
    matches `question_pattern`. Works on the label text, not on ids."""
    try:
        return bool(page.evaluate("""([qpat, want]) => {
          const qre = new RegExp(qpat, 'i'), w = want.trim().toLowerCase();
          const labelOf = r => {
            let l = r.id ? document.querySelector(`label[for="${CSS.escape(r.id)}"]`) : null;
            if (!l) l = r.closest('label');
            if (!l) { const ref = r.getAttribute('aria-labelledby'); if (ref) l = document.getElementById(ref); }
            return l;
          };
          for (const r of document.querySelectorAll('input[type="radio"], [role="radio"]')) {
            // walk up until the question text is inside the box (legend, sibling label…)
            let box = r.parentElement, ok = false;
            for (let hops = 0; box && hops < 8; hops++) {
              if (qre.test(box.innerText || '')) { ok = true; break; }
              box = box.parentElement;
            }
            if (!ok || (box.innerText || '').length > 1500) continue;
            const l = labelOf(r);
            const t = ((l && l.innerText) || r.getAttribute('aria-label') || r.innerText || '').trim().toLowerCase();
            if (t !== w && !t.startsWith(w + ' ') && !t.startsWith(w + ',')) continue;
            if (l) l.click(); else r.click();
            if (r.tagName === 'INPUT' && !r.checked) { r.click(); }
            if (r.tagName === 'INPUT' && !r.checked) {
              const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked').set;
              set.call(r, true); r.dispatchEvent(new Event('change', {bubbles: true}));
            }
            return true;
          }
          return false;
        }""", [question_pattern, want]))
    except Exception:
        return False


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


def type_prompt(page, sel, wants, warnings, label=""):
    """A Workday multiselect/typeahead ("How did you hear", "Field of study",
    country phone code): type, wait for suggestions, click the match."""
    if not _vis(page, sel, 500):
        return ""

    def pills():
        try:
            return (page.locator(sel).first.evaluate(
                "el => { const c = el.closest('[data-automation-id^=\"formField-\"]') || el.parentElement.parentElement;"
                " return [...c.querySelectorAll('[data-automation-id=\"selectedItem\"], [data-automation-id=\"pill\"], [data-automation-id=\"selectedItemList\"] li')]"
                ".map(p => p.innerText.trim()).filter(Boolean).join(' | '); }") or "").strip()
        except Exception:
            return ""

    # A wrong pill from an earlier attempt must go first: Workday keeps it.
    if pills() and not any(w.lower() in pills().lower() for w in wants if w):
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
            loc.type(want[:40], delay=40)
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
        hit = next(iter(_rank(opts, want)), None)
        if hit is None and len(opts) > 25:
            hit = _scroll_find(page, want)
            hit = hit if hit[0] is not None else None
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
        cat = next((o for o, ot in opts if any(k in ot for k in ("social", "job board", "online", "internet"))), None)
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
    for _ in range(max_s * 2):
        if (_vis(page, '[data-automation-id="email"]', 200) or _vis(page, NEXT_BTN, 200)
                or _vis(page, '[data-automation-id="applyManually"]', 200)
                or _vis(page, '[data-automation-id="applyButton"]', 200)
                or _vis(page, 'input[data-automation-id="legalNameSection_firstName"]', 200)):
            return True
        page.wait_for_timeout(500)
    return False


# ------------------------------------------------------------- account

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
        page.wait_for_timeout(1200)
    _fill(page, '[data-automation-id="email"]', email, warnings, "email")
    _fill(page, '[data-automation-id="password"]', pw, warnings, "password")
    _click(page, 'button[data-automation-id="signInSubmitButton"], div[data-automation-id="click_filter"] button')
    page.wait_for_timeout(3500)
    if not on_gate():
        warnings.append("Workday: signed in")
        return True
    err = ""
    try:
        err = (page.locator('[data-automation-id="errorMessage"]').first.inner_text() or "").strip()
    except Exception:
        pass
    # Unknown address -> create the account with the same credentials.
    if _click(page, 'button[data-automation-id="createAccountLink"], a[data-automation-id="createAccountLink"]'):
        page.wait_for_timeout(1500)
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
        _click(page, 'button[data-automation-id="createAccountSubmitButton"], div[data-automation-id="click_filter"] button')
        page.wait_for_timeout(4000)
        body = _body(page)
        if "verif" in body and "email" in body:
            _verify_email(page, ctx, warnings)
        if not on_gate():
            warnings.append("Workday: account created for this tenant")
            return True
        try:
            err = (page.locator('[data-automation-id="errorMessage"]').first.inner_text() or "").strip()
        except Exception:
            pass
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
            page.wait_for_timeout(3000)
        except Exception as e:
            warnings.append(f"Workday: code entry failed: {str(e)[:60]}")
        return
    ans = ask(f"wd-link-{ctx.get('company_slug', 'x')}",
              f"{ctx.get('company', 'Workday')}: Workday sent a verification EMAIL for the new account. "
              f"Open it, click the link, then type 'done' here.", timeout=900)
    if ans:
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
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
            page.wait_for_timeout(4000)
            continue
        step = current_step(page)
        if step and not step.startswith("create account") and _vis(page, NEXT_BTN, 800):
            return True
        if _vis(page, '[data-automation-id="email"]', 600) or step.startswith("create account"):
            if not login_or_create(page, ctx, warnings):
                return False
            page.wait_for_timeout(2000)
            continue
        if _vis(page, '[data-automation-id="applyManually"]', 600):
            _click(page, '[data-automation-id="applyManually"]')
            page.wait_for_timeout(3000)
            continue
        if _vis(page, '[data-automation-id="applyButton"]', 600):
            _click(page, '[data-automation-id="applyButton"]')
            page.wait_for_timeout(3000)
            continue
        if _vis(page, 'a[data-automation-id="adventureButton"]', 600):   # "Apply" on some tenants
            _click(page, 'a[data-automation-id="adventureButton"]')
            page.wait_for_timeout(3000)
            continue
        page.wait_for_timeout(2000)
    step = current_step(page)
    ok = _vis(page, NEXT_BTN, 800) and bool(step) and not step.startswith("create account")
    if not ok:
        warnings.append("Workday: application wizard did not open (no Apply button / step page found)")
    return ok


def fill_my_information(page, answers, ctx, resolve, warnings, filled):
    from jobpilot.fill.browser import fill_fields, YES
    p, loc, links = answers["personal"], answers["location"], answers["links"]

    def first_visible(*sels):
        return next((s for s in sels if s and _vis(page, s, 300)), "")

    # Country FIRST: changing it re-renders the whole address/phone block and
    # wipes anything typed before it.
    c = first_visible('button[data-automation-id="countryDropdown"]', by_label(page, r"^country\s*\*?$", "button"))
    if c:
        try:
            cur = (page.locator(c).first.inner_text() or "").strip().lower()
        except Exception:
            cur = ""
        if loc["country"].lower() != cur.strip():
            got = pick_listbox(page, c, [loc["country"]], warnings, "Country")
            if got:
                filled["Country"] = got
                page.wait_for_timeout(2000)
        else:
            filled["Country"] = cur

    heard = answers.get("questions", {}).get("how_did_you_hear") or "LinkedIn"
    src = first_visible('[data-automation-id="source"] input', 'input[data-automation-id="source"]',
                        '[data-automation-id="sourceSection"] input', by_label(page, r"how did you hear", "input"))
    if src:
        got = type_prompt(page, src, [heard, "LinkedIn", "Job Board", "Job Boards"], warnings, "How did you hear about us")
        if got:
            filled["How did you hear about us?"] = got
    else:
        src = first_visible('button[data-automation-id="sourceDropdown"]', by_label(page, r"how did you hear", "button"))
        if src:
            got = pick_listbox(page, src, [heard, "LinkedIn", "Job Board"], warnings, "source")
            if got:
                filled["How did you hear about us?"] = got

    # Previous worker: the per-company rule in answers.yaml (Amazon = Yes).
    prev = resolve(f"Have you previously been employed by {ctx.get('company', '')}?")
    want = "Yes" if prev is YES else "No"
    if click_radio(page, r"worked for .* as an employee|previously (been )?employed|contingent worker|former (employee|worker)", want):
        filled["Previously worked here?"] = want
    else:
        warnings.append("Workday: 'have you worked here before' radio not found")

    def put(sel_id, pattern, value, label):
        s = first_visible(sel_id, by_label(page, pattern, "input"))
        if s and _fill(page, s, value, warnings, label):
            filled[label] = value

    put('input[data-automation-id="legalNameSection_firstName"]', r"^(given name|first name|legal first)", p["first_name"], "Given name")
    put('input[data-automation-id="legalNameSection_lastName"]', r"^(family name|last name|surname|legal last)", p["last_name"], "Family name")
    put('input[data-automation-id="addressSection_addressLine1"]', r"^address line 1", loc.get("address_line_1") or loc["city"], "Address line 1")
    put('input[data-automation-id="addressSection_city"]', r"^city", loc["city"], "City")
    put('input[data-automation-id="addressSection_postalCode"]', r"^(postal|zip)", loc.get("postal_code"), "Postal code")
    s = first_visible('button[data-automation-id="addressSection_countryRegion"]', by_label(page, r"^(state|province|region|county)", "button"))
    if s:
        pick_listbox(page, s, [loc.get("state") or loc["city"]], warnings, "State / Region")
    # Generic pass here — before the phone block, which it gets wrong on Workday
    # (full +91 number into a national-number box, and into the extension).
    f, w, _m = fill_fields(page, resolve, answers, ctx)
    for k, v in f.items():
        filled.setdefault(k, v)
    warnings.extend(x for x in w if "NOT FILLED (REQUIRED)" in x and "phone" not in x.lower())

    s = first_visible('button[data-automation-id="phone-device-type"]', by_label(page, r"phone device type|^device type", "button"))
    if s:
        got = pick_listbox(page, s, ["Mobile", "Home", "Telephone"], warnings, "Phone device type")
        if got:
            filled["Phone device type"] = got
    code = f"{loc['country']} (+{p['phone_country_code'].lstrip('+')})"
    s = first_visible('input[data-automation-id="countryPhoneCode"]', '[data-automation-id="country-phone-code"] input',
                      '[data-automation-id="phone-country-code"] input', by_label(page, r"country phone code|phone code", "input"))
    if s:
        already = ""
        try:
            already = page.locator(s).first.evaluate(
                "el => (el.closest('[data-automation-id^=\"formField-\"]') || el.parentElement.parentElement).innerText") or ""
        except Exception:
            pass
        if f"+{p['phone_country_code'].lstrip('+')}" not in already:
            type_prompt(page, s, [code, loc["country"]], warnings, "country phone code")
    else:
        s = first_visible('button[data-automation-id="country-phone-code"]', by_label(page, r"country phone code|phone code", "button"))
        if s:
            pick_listbox(page, s, [code, loc["country"]], warnings, "country phone code")
    s = first_visible('input[data-automation-id="phone-number"]', by_label(page, r"^phone number", "input"))
    if s:
        try:
            page.locator(s).first.fill(p["phone_national"], timeout=4000)
            filled["Phone number"] = p["phone_national"]
        except Exception as e:
            warnings.append(f"Workday: phone number: {str(e)[:60]}")
    s = first_visible('input[data-automation-id="phone-extension"]', by_label(page, r"^phone extension|^extension", "input"))
    if s:
        try:
            page.locator(s).first.fill("", timeout=3000)          # never an extension
        except Exception:
            pass


def fill_my_experience(page, answers, ctx, resolve, warnings, filled):
    from jobpilot.fill.browser import resume_path, fill_fields
    emp, edu = answers.get("employment") or [], answers.get("education") or []

    # Work experience blocks. If the account already carries them (a previous
    # application on this tenant), leave them — they are ours from last time.
    sec = '[data-automation-id="workExperienceSection"]'
    if _vis(page, sec, 500):
        have = page.locator(f'{sec} [data-automation-id^="workExperience-"]').count()
        if have:
            warnings.append(f"Workday: {have} work-experience block(s) already on the account; left as they are")
        else:
            for i, e in enumerate(emp):
                add = f'{sec} button[data-automation-id="Add"]' if i == 0 else f'{sec} button[data-automation-id="Add Another"]'
                if not _click(page, add):
                    warnings.append(f"Workday: could not add work-experience block {i + 1}")
                    break
                page.wait_for_timeout(1200)
                blk = f'{sec} [data-automation-id="workExperience-{i + 1}"]'
                _fill(page, f'{blk} input[data-automation-id="jobTitle"]', e["title"], warnings, "job title")
                _fill(page, f'{blk} input[data-automation-id="company"]', e["employer"], warnings, "company")
                _fill(page, f'{blk} input[data-automation-id="location"]', e.get("location", ""), warnings, "location")
                if e.get("current"):
                    try:
                        page.locator(f'{blk} input[data-automation-id="currentlyWorkHere"]').first.check(timeout=3000, force=True)
                    except Exception:
                        _click(page, f'{blk} [data-automation-id="currentlyWorkHere"]')
                set_date(page, f'{blk} [data-automation-id="formField-startDate"]', e.get("start_date"), warnings, "start date")
                if not e.get("current") and e.get("end_date"):
                    set_date(page, f'{blk} [data-automation-id="formField-endDate"]', e["end_date"], warnings, "end date")
                _fill(page, f'{blk} textarea[data-automation-id="description"]', e.get("summary", ""), warnings, "description")
                filled[f"Experience {i + 1}"] = f"{e['title']} @ {e['employer']}"

    sec = '[data-automation-id="educationSection"]'
    if _vis(page, sec, 500):
        have = page.locator(f'{sec} [data-automation-id^="education-"]').count()
        if have:
            warnings.append(f"Workday: {have} education block(s) already on the account; left as they are")
        else:
            for i, e in enumerate(edu):
                add = f'{sec} button[data-automation-id="Add"]' if i == 0 else f'{sec} button[data-automation-id="Add Another"]'
                if not _click(page, add):
                    warnings.append(f"Workday: could not add education block {i + 1}")
                    break
                page.wait_for_timeout(1200)
                blk = f'{sec} [data-automation-id="education-{i + 1}"]'
                school = f'{blk} input[data-automation-id="school"], {blk} [data-automation-id="schoolItem"] input'
                if _vis(page, school, 400):
                    got = type_prompt(page, school, [e["institution"], e["institution"].split(",")[0]], warnings, "school")
                    if not got:
                        _fill(page, school, e["institution"], warnings, "school")
                deg = e.get("degree", "")
                short = re.search(r"\(([^)]+)\)", deg)
                pick_listbox(page, f'{blk} button[data-automation-id="degree"]',
                             [deg, short.group(1) if short else "", deg.split(" of ")[0]], warnings, "degree")
                fos = f'{blk} [data-automation-id="field-of-study"] input, {blk} input[data-automation-id="field-of-study"], {blk} [data-automation-id="fieldOfStudy"] input'
                if _vis(page, fos, 300):
                    type_prompt(page, fos, [e.get("field_of_study", ""), e.get("field_of_study", "").split(" and ")[0]], warnings, "field of study")
                if e.get("gpa") and str(e["gpa"]).upper() != "TODO":
                    _fill(page, f'{blk} input[data-automation-id="gpa"]', e["gpa"], warnings, "gpa")
                set_date(page, f'{blk} [data-automation-id="formField-startDate"]', e.get("start_date"), warnings, "edu start")
                set_date(page, f'{blk} [data-automation-id="formField-endDate"]', e.get("end_date"), warnings, "edu end")
                filled[f"Education {i + 1}"] = f"{deg} — {e['institution']}"

    # Resume (.docx parses best), LinkedIn, websites.
    rp = resume_path(answers, ctx["company_slug"], ctx["portal"])
    up = 'input[data-automation-id="file-upload-input-ref"]'
    if rp and page.locator(up).count():
        try:
            page.locator(up).first.set_input_files(rp)
            page.wait_for_timeout(3500)
            filled["Resume"] = os.path.basename(rp)
        except Exception as e:
            warnings.append(f"Workday: resume upload failed: {str(e)[:60]}")
    _fill(page, 'input[data-automation-id="linkedinQuestion"]', answers["links"].get("linkedin"), warnings, "linkedin") and filled.update({"LinkedIn": answers["links"].get("linkedin")})
    # Anything else labelled on this page (languages, skills stay empty on purpose).
    f, w, _m = fill_fields(page, resolve, answers, ctx)
    for k, v in f.items():
        filled.setdefault(k, v)


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


DIALOG = '[role="dialog"], [data-automation-id="popUpDialog"], [data-automation-id="sidePanel"], [data-automation-id="panel"]'


def handle_side_panel(page, answers, warnings):
    """Workday sometimes slides in a 'Personal Information' panel (own Save /
    Cancel) over the wizard — seen after Save and Continue on Mastercard, with
    the phone-code menu already open. Close the menu, put the national phone
    number in, Save the panel, and let the wizard carry on."""
    if not _vis(page, DIALOG, 500):
        return False
    p = answers["personal"]
    dlg = page.locator(DIALOG).last
    try:
        title = (dlg.locator("h2, h3, [data-automation-id='panelTitle']").first.inner_text() or "").strip()
    except Exception:
        title = "panel"
    warnings.append(f"Workday: side panel '{title[:40]}' handled")
    # An open prompt menu inside the panel swallows clicks: close it on the title.
    try:
        dlg.locator("h2, h3").first.click(timeout=2000)
        page.wait_for_timeout(300)
    except Exception:
        pass
    ph = by_label(page, r"^phone number", "input")
    if ph and _vis(page, ph, 300):
        try:
            page.locator(ph).first.fill(p["phone_national"], timeout=3000)
        except Exception:
            pass
    ext = by_label(page, r"^phone extension|^extension", "input")
    if ext and _vis(page, ext, 300):
        try:
            page.locator(ext).first.fill("", timeout=2000)
        except Exception:
            pass
    save = dlg.locator('button:has-text("Save"), button[data-automation-id*="Save" i]').last
    try:
        save.click(timeout=4000)
    except Exception:
        try:
            save.click(timeout=3000, force=True)
        except Exception as e:
            warnings.append(f"Workday: side panel Save failed: {str(e)[:60]}")
            return False
    page.wait_for_timeout(2500)
    return True


def next_step(page, before, answers=None):
    """Click Save and Continue; report whether the wizard moved on."""
    from jobpilot.fill.browser import wait_dom_stable
    if not _click(page, NEXT_BTN):
        return False, [{"label": "Save and Continue", "kind": "text", "text": "button not found"}]
    page.wait_for_timeout(2500)
    wait_dom_stable(page, max_s=12, quiet=2)
    after = current_step(page)
    if after and after != before:
        return True, []
    if answers is not None and handle_side_panel(page, answers, []):
        wait_dom_stable(page, max_s=10, quiet=2)
        after = current_step(page)
        if after and after != before:
            return True, []
        if _click(page, NEXT_BTN):                   # the panel ate the first click
            page.wait_for_timeout(2500)
            wait_dom_stable(page, max_s=12, quiet=2)
            after = current_step(page)
            if after and after != before:
                return True, []
    errs = collect_errors(page)
    if not errs and after == before:
        errs = [{"label": before, "kind": "text", "text": "step did not advance and showed no field error"}]
    return False, errs


def run(page, ctx, answers, resolve, stage="prep"):
    """Drive the wizard. stage='prep' stops at Review; 'submit' presses Submit."""
    from jobpilot.fill.browser import harvest_questions, fill_fields
    warnings, filled, missing, questions = [], {}, [], []
    res = {"filled": filled, "warnings": warnings, "missing": missing, "questions": questions,
           "reached_review": False, "submit_ok": False, "steps": []}

    if not start_application(page, ctx, warnings):
        return res
    last = ""
    for _ in range(10):
        wait_step_content(page)
        if "something went wrong" in _body(page)[:3000]:
            warnings.append("Workday: 'something went wrong' mid-wizard — refreshed")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            wait_step_content(page)
        step = current_step(page)
        res["steps"].append(step or "?")
        if step.startswith("review"):
            res["reached_review"] = True
            break
        if step.startswith("create account"):
            if not login_or_create(page, ctx, warnings):
                break
            page.wait_for_timeout(2500)
            continue
        if step == last:
            break                                  # did not advance last time; errors already recorded
        last = step
        try:
            if step.startswith("my information"):
                fill_my_information(page, answers, ctx, resolve, warnings, filled)
            elif step.startswith("my experience"):
                fill_my_experience(page, answers, ctx, resolve, warnings, filled)
            else:
                if step.startswith("self identify"):
                    fill_self_identify(page, answers, warnings, filled)
                f, w, m = fill_fields(page, resolve, answers, ctx)
                filled.update(f)
                warnings.extend(x for x in w if "REQUIRED" in x or "COULD NOT" in x)
                missing.extend(m)
                if stage == "prep":
                    questions.extend(q for q in harvest_questions(page, resolve, warnings)
                                     if q.get("required") or len(q.get("label", "")) > 60)
        except Exception as e:
            warnings.append(f"Workday: step '{step}' raised {type(e).__name__}: {str(e)[:80]}")
        ok, errs = next_step(page, step, answers)
        if not ok:
            for e in errs:
                missing.append({"label": e.get("label", ""), "required": True, "kind": e.get("kind", "text"),
                                "options": [], "reason": "Workday: " + (e.get("text") or "field rejected on Save and Continue")})
            warnings.append(f"Workday: '{step}' would not save — {[e.get('label') for e in errs][:5]}")
            break

    if stage == "prep" or not res["reached_review"]:
        return res
    # Review -> Submit. Success is Workday's confirmation page.
    if not _click(page, SUBMIT_BTN):
        warnings.append("Workday: Submit button not found on Review")
        return res
    page.wait_for_timeout(4000)
    for _ in range(10):
        body = _body(page)
        if any(w in body for w in ("congratulations", "application submitted", "thank you for applying",
                                   "successfully submitted", "your application has been")):
            res["submit_ok"] = True
            break
        if _vis(page, '[data-automation-id="applicationSubmitted"], [data-automation-id="applyFlowSubmittedTitle"]', 300):
            res["submit_ok"] = True
            break
        page.wait_for_timeout(1000)
    if not res["submit_ok"]:
        errs = collect_errors(page)
        for e in errs:
            missing.append({"label": e.get("label", ""), "required": True, "kind": e.get("kind", "text"),
                            "options": [], "reason": "Workday: " + (e.get("text") or "rejected on Submit")})
        warnings.append("Workday: no confirmation page after Submit")
    return res
