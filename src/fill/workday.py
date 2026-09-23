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
NEXT_BTN = 'button[data-automation-id="bottom-navigation-next-button"]'
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
    out = []
    try:
        loc = page.locator(OPTION_SEL)
        for i in range(min(loc.count(), 60)):
            o = loc.nth(i)
            if o.is_visible(timeout=150):
                out.append((o, (o.inner_text() or "").strip().lower()))
    except Exception:
        pass
    return out


def pick_listbox(page, sel, wants, warnings, label=""):
    """Open a <button aria-haspopup=listbox> (or any button) and pick the first
    of `wants` that matches an option; never picks an unmatched option."""
    if not _click(page, sel):
        return ""
    page.wait_for_timeout(600)
    opts = _options(page)
    for want in [w for w in wants if w]:
        for o, ot in _rank(opts, want):
            try:
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
    for want in [w for w in wants if w]:
        try:
            loc = page.locator(sel).first
            loc.click(timeout=3000)
            loc.fill("")
            loc.type(want[:40], delay=40)
        except Exception:
            continue
        for _ in range(8):
            page.wait_for_timeout(500)
            opts = _options(page)
            if opts:
                break
        for o, ot in _rank(_options(page), want):
            try:
                o.click(timeout=3000)
                page.wait_for_timeout(400)
                return ot
            except Exception:
                continue
        try:
            page.keyboard.press("Enter")          # single-suggestion prompts accept Enter
            page.wait_for_timeout(400)
        except Exception:
            pass
        chosen = ""
        try:
            chosen = (page.locator(sel).first.evaluate(
                "el => { const c = el.closest('[data-automation-id^=\"formField-\"]') || el.parentElement;"
                " const p = c && c.querySelector('[data-automation-id=\"selectedItem\"], [data-automation-id=\"pill\"]');"
                " return p ? p.innerText : ''; }") or "").strip()
        except Exception:
            pass
        if chosen:
            return chosen
    warnings.append(f"Workday: typeahead {label or sel} took none of {wants[:2]}")
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


def current_step(page):
    """Which wizard page is showing, by its heading."""
    try:
        heads = page.locator("h2, h3, [data-automation-id='pageHeaderTitle']")
        for i in range(min(heads.count(), 12)):
            t = (heads.nth(i).inner_text() or "").strip().lower()
            for s in STEP_NAMES:
                if t.startswith(s):
                    return s
    except Exception:
        pass
    body = _body(page)
    for s in STEP_NAMES:
        if re.search(rf"\b{s}\b", body[:4000]):
            return s
    return ""


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
    for attempt in range(3):
        if _vis(page, NEXT_BTN, 800) or current_step(page):
            return True
        if _vis(page, '[data-automation-id="email"]', 600):
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
    ok = _vis(page, NEXT_BTN, 800) or bool(current_step(page))
    if not ok:
        warnings.append("Workday: application wizard did not open (no Apply button / step page found)")
    return ok


def fill_my_information(page, answers, ctx, resolve, warnings, filled):
    from jobpilot.fill.browser import fill_fields, YES
    p, loc, links = answers["personal"], answers["location"], answers["links"]
    # Generic first (labelled inputs, listboxes it recognises), specific after,
    # so a known Workday id always wins over a guessed label match.
    f, w, _m = fill_fields(page, resolve, answers, ctx)
    filled.update(f)
    warnings.extend(x for x in w if "REQUIRED" in x or "NOT FILLED (REQUIRED)" in x)

    heard = answers.get("questions", {}).get("how_did_you_hear") or "LinkedIn"
    if _vis(page, '[data-automation-id="source"] input, input[data-automation-id="source"], [data-automation-id="sourceSection"] input', 500):
        got = type_prompt(page, '[data-automation-id="source"] input, input[data-automation-id="source"], [data-automation-id="sourceSection"] input',
                          [heard, "LinkedIn", "Job Board"], warnings, "How did you hear about us")
        if got:
            filled["How did you hear about us?"] = got
    elif _vis(page, 'button[data-automation-id="sourceDropdown"]', 400):
        got = pick_listbox(page, 'button[data-automation-id="sourceDropdown"]', [heard, "LinkedIn"], warnings, "source")
        if got:
            filled["How did you hear about us?"] = got

    # Previous worker: the per-company rule in answers.yaml (Amazon = Yes).
    prev = resolve(f"Have you previously been employed by {ctx.get('company', '')}?")
    want = "Yes" if prev is YES else "No"
    for sel in (f'[data-automation-id="previousWorker"] label:has-text("{want}")',
                f'[data-automation-id="previousWorker"] [role="radio"]:has-text("{want}")'):
        if _click(page, sel):
            filled["Previously worked here?"] = want
            break

    if _vis(page, 'button[data-automation-id="countryDropdown"]', 400):
        got = pick_listbox(page, 'button[data-automation-id="countryDropdown"]', [loc["country"]], warnings, "Country")
        if got:
            filled["Country"] = got
            page.wait_for_timeout(1500)                # the address block re-renders per country

    _fill(page, 'input[data-automation-id="legalNameSection_firstName"]', p["first_name"], warnings, "first name") and filled.update({"First name": p["first_name"]})
    _fill(page, 'input[data-automation-id="legalNameSection_lastName"]', p["last_name"], warnings, "last name") and filled.update({"Last name": p["last_name"]})
    _fill(page, 'input[data-automation-id="addressSection_addressLine1"]', loc.get("address_line_1") or loc["city"], warnings, "address")
    _fill(page, 'input[data-automation-id="addressSection_city"]', loc["city"], warnings, "city")
    _fill(page, 'input[data-automation-id="addressSection_postalCode"]', loc.get("postal_code"), warnings, "postal code")
    if _vis(page, 'button[data-automation-id="addressSection_countryRegion"]', 400):
        pick_listbox(page, 'button[data-automation-id="addressSection_countryRegion"]', [loc.get("state") or loc["city"]], warnings, "State / Region")
    if _vis(page, 'button[data-automation-id="phone-device-type"]', 400):
        pick_listbox(page, 'button[data-automation-id="phone-device-type"]', ["Mobile", "Home", "Telephone"], warnings, "Phone device type")
    for sel in ('input[data-automation-id="countryPhoneCode"]', '[data-automation-id="country-phone-code"] input',
                '[data-automation-id="phone-country-code"] input'):
        if _vis(page, sel, 300):
            type_prompt(page, sel, [f"{loc['country']} (+{p['phone_country_code'].lstrip('+')})", loc["country"]],
                        warnings, "country phone code")
            break
    else:
        if _vis(page, 'button[data-automation-id="country-phone-code"]', 300):
            pick_listbox(page, 'button[data-automation-id="country-phone-code"]',
                         [f"{loc['country']} (+{p['phone_country_code'].lstrip('+')})", loc["country"]], warnings, "country phone code")
    _fill(page, 'input[data-automation-id="phone-number"]', p["phone_national"], warnings, "phone") and filled.update({"Phone": p["phone_national"]})


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


def next_step(page, before):
    """Click Save and Continue; report whether the wizard moved on."""
    from jobpilot.fill.browser import wait_dom_stable
    if not _click(page, NEXT_BTN):
        return False, [{"label": "Save and Continue", "kind": "text", "text": "button not found"}]
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
        step = current_step(page)
        res["steps"].append(step or "?")
        if step == "review":
            res["reached_review"] = True
            break
        if step == last:
            break                                  # did not advance last time; errors already recorded
        last = step
        try:
            if step == "my information":
                fill_my_information(page, answers, ctx, resolve, warnings, filled)
            elif step == "my experience":
                fill_my_experience(page, answers, ctx, resolve, warnings, filled)
            else:
                if step == "self identify":
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
        ok, errs = next_step(page, step)
        if not ok:
            for e in errs:
                missing.append({"label": e.get("label", ""), "required": True, "kind": e.get("kind", "text"),
                                "options": [], "reason": "Workday: " + (e.get("text") or "field rejected on Save and Continue")})
            warnings.append(f"Workday: '{step}' would not save — {[e.get('label') for e in errs][:5]}")
            break

    if stage == "prep" or not res["reached_review"]:
        return res
    # Review -> Submit. Success is Workday's confirmation page.
    if not _click(page, NEXT_BTN):
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
