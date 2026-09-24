#!/usr/bin/env python3
"""
browser.py — the Playwright half of Phase 2. Imported by autofill.py.

Runs HEADED real Chrome against a persistent profile (~/.config/jobbot/chrome-profile)
so sessions, cookies and human-looking fingerprints persist. Never headless: portals
treat headless traffic differently, and you want to be able to watch it work.

fill_application()  — pass 1. EXPLORES the whole form: fills what it can, enters a
                      PLACEHOLDER into any required control nobody has an answer
                      for so the form can be walked to its last page, records a
                      recipe per control in applications/<slug>/replay.json,
                      screenshots, queues as PENDING. Cannot submit: there is no
                      submit call on this path at all.
submit_approved()   — pass 2. Only touches queue items the phone marked APPROVED.
                      Refuses to open the browser while any explored control still
                      has no approved answer; then REPLAYS the recipes with the true
                      values and falls back to discovery for anything new.
"""
import datetime as dt
import json
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)
QUEUE_DIR = os.path.join(DATA, "queue")
from jobpilot.core.config import cfg  # noqa: E402
from jobpilot.fill import replay as R  # noqa: E402   recipes: explore on pass 1, replay on pass 2
from jobpilot.fill import platforms, hooks, walk  # noqa: E402   platform modules; per-application hooks; the walker
PROFILE_DIR = os.path.expanduser("~/.config/jobbot/chrome-profile")

YES, NO = "__YES__", "__NO__"


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[*\u2217]", "", s or "")).strip().lower()


# Tag every control and read its label the way a human would: explicit <label>,
# then ARIA, then the nearest preceding text, then placeholder/name.
EXTRACT_JS = r"""
() => {
  const out = [];
  // Tags are this pass's numbering. Old ones from an earlier extraction made the
  // third pass skip every dropdown button ("already tagged") on a re-extract —
  // after a resume upload, all Workday menus silently vanished.
  document.querySelectorAll('[data-jobbot-idx]').forEach(e => e.removeAttribute('data-jobbot-idx'));
  const ctrls = document.querySelectorAll('input, textarea, select');
  let i = 0;
  for (const el of ctrls) {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) continue;
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') continue;
    // A control on a page the wizard is not showing has no box. (A file input is
    // often boxless by design behind a styled button, so it is exempt.)
    if (type !== 'file' && el.getClientRects().length === 0) continue;
    if (el.closest('header, nav, [data-automation-id^="utility"]')) continue;   // page chrome, never the form

    let label = '';
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) label = l.innerText;
    }
    if (!label) { const l = el.closest('label'); if (l) label = l.innerText; }
    if (!label) label = el.getAttribute('aria-label') || '';
    if (!label) {
      const ref = el.getAttribute('aria-labelledby');
      if (ref) { const n = document.getElementById(ref); if (n) label = n.innerText; }
    }
    if (!label) {
      let n = el.parentElement, hops = 0;
      while (n && hops < 3 && !label) {
        const t = (n.innerText || '').trim();
        if (t && t.length < 200) label = t;
        n = n.parentElement; hops++;
      }
    }
    // A custom select's visible text is often just "Select..." — that is a
    // placeholder, not the question. Treat it as empty and keep looking.
    // "Select One" too: Workday's hidden text box inside a dropdown otherwise
    // took the dropdown's own placeholder text as its question.
    const GENERIC = /^(select( one)?\.{0,3}|choose( one)?\.{0,3}|please select|--+|none|n\/a)$/i;
    if (GENERIC.test((label || '').trim())) label = '';
    if (!label) {
      let n = el.parentElement, hops = 0;
      while (n && hops < 5 && !label) {
        const cand = Array.from(n.childNodes)
          .filter(c => c.nodeType === 3)
          .map(c => c.textContent.trim())
          .filter(t => t.length > 6 && !GENERIC.test(t))[0];
        if (cand) label = cand;
        else {
          const lbl = n.querySelector && n.querySelector('label');
          if (lbl && !GENERIC.test(lbl.innerText.trim())) label = lbl.innerText;
        }
        n = n.parentElement; hops++;
      }
    }
    if (!label) label = el.placeholder || el.name || '';
    // A validation message sits where a label is looked for ("Error: The field
    // If yes, … is required"). The field's own label is the question.
    if (/^error\b/i.test((label || '').trim())) {
      const ff = el.closest('[data-automation-id^="formField-"], fieldset, [class*="field" i]');
      const l2 = ff && ff.querySelector('label, legend');
      label = (l2 && !/^error\b/i.test(l2.innerText.trim()) && l2.innerText) || el.getAttribute('aria-label') ||
              label.replace(/^error:?\s*(the field\s*)?/i, '').replace(/\s*is required and must have a value\.?$/i, '');
    }

    el.setAttribute('data-jobbot-idx', String(i));
    const rec = {
      idx: i, type, name: el.name || '', id: el.id || '',
      label: (label || '').split('\n')[0].trim().slice(0, 160),
      required: el.required || el.getAttribute('aria-required') === 'true',
      value: el.value || '',
      placeholder: el.placeholder || '',
      combobox: (el.getAttribute('role') === 'combobox'
                 || el.getAttribute('aria-autocomplete') === 'list'
                 || el.getAttribute('aria-haspopup') === 'listbox'
                 || /start typing|search|select\.\.\./i.test(el.placeholder || '')),
      // Greenhouse's 2025 job board renders every dropdown as react-select:
      // the <input> is only the search box, the chosen value lives in a sibling
      // .select__single-value, and el.value is EMPTY after a pick. Flag it so
      // the fill reads back the right node.
      rs: !!el.closest('.select__control'),
      tag: el.tagName.toLowerCase(),
    };
    if (el.tagName.toLowerCase() === 'select') {
      rec.options = Array.from(el.options).map(o => o.text.trim());
    }
    // A radio's own label is the OPTION ("Yes"), not the question. The question
    // lives in the fieldset legend or a group container, so capture it too.
    if (type === 'radio' || type === 'checkbox') {
      let g = '';
      const fs = el.closest('fieldset');
      const lg = fs && fs.querySelector('legend');
      if (lg) g = lg.innerText;
      if (!g) {
        const grp = el.closest('[role="radiogroup"],[role="group"]');
        if (grp) g = (grp.getAttribute('aria-label') || '').trim();
      }
      // A checkbox whose own label is a statement ("By checking this box, I
      // consent to ...") is its own question. Climbing from it once reached the
      // form's first line and asked "First Name" with the consent as its option.
      if (!g && type === 'checkbox' && (label || '').trim().length > 40) g = label;
      if (!g) {
        let n = el.parentElement, hops = 0;
        while (n && hops < 4 && !g) {
          const first = (n.innerText || '').split('\n')[0].trim();
          // the first line of an option list is an OPTION ("C2/Native"), not the question
          if (first && first.length > 8 && first.length < 200 &&
              !/^(yes|no)$/i.test(first) && first !== (label || '').trim()) g = first;
          n = n.parentElement; hops++;
        }
      }
      rec.group_label = (g || '').split('\n')[0].trim().slice(0, 160);
    }
    out.push(rec);
    i++;
  }

  // --- second pass: Yes/No (and similar) rendered as <button> groups ---
  const CHOICE = /^(yes|no|n\/a|true|false|prefer not to say|decline.*)$/i;
  const groups = new Map();
  for (const b of document.querySelectorAll('button, [role="radio"], [role="option"]')) {
    const txt = (b.innerText || '').trim();
    if (!txt || txt.length > 24 || !CHOICE.test(txt)) continue;
    // An open dropdown's own "Yes"/"No" rows are menu options, not answer
    // buttons: they became a phantom question named "Select One".
    if (b.closest('[role="listbox"], [role="menu"], [data-automation-id*="promptOption"], [data-automation-id="activeListContainer"]')) continue;
    const st = window.getComputedStyle(b);
    if (st.display === 'none' || st.visibility === 'hidden') continue;
    if (b.closest('header, nav, [data-automation-id^="utility"]')) continue;
    const box = b.closest('fieldset,[role="group"],[role="radiogroup"]') || b.parentElement;
    if (!box) continue;
    if (!groups.has(box)) groups.set(box, []);
    groups.get(box).push(b);
  }
  for (const [box, btns] of groups) {
    if (btns.length < 2) continue;
    if (btns.some(b => b.getAttribute('role') === 'option')) {
      const q0 = (box.innerText || '').split('\n').map(x => x.trim()).find(x => x && !CHOICE.test(x)) || '';
      if (!q0 || /^(select|choose)( one)?\.{0,3}$/i.test(q0)) continue;
    }
    // The question is the nearest preceding text above the button row.
    let q = '', n = box, hops = 0;
    while (n && hops < 4 && !q) {
      const t = (n.innerText || '').trim();
      if (t) {
        const first = t.split('\n').map(x => x.trim())
                       .filter(x => x && !CHOICE.test(x))[0];
        if (first && first.length > 8) q = first;
      }
      n = n.parentElement; hops++;
    }
    for (const b of btns) {
      b.setAttribute('data-jobbot-idx', String(i));
      out.push({idx: i, type: 'buttongroup', name: '', id: '',
                label: (b.innerText || '').trim().slice(0, 60),
                group_label: (q || '').slice(0, 200),
                required: true, value: (b.innerText || '').trim(),
                pressed: b.getAttribute('aria-checked') === 'true' ||
                         b.getAttribute('aria-pressed') === 'true'});
      i++;
    }
  }

  // --- third pass: custom dropdowns rendered as <button aria-haspopup="listbox">
  // (Workday, MUI). The button's text is the current value; options render in
  // a popup on click, so the fill opens it and reads them then.
  for (const b of document.querySelectorAll('button[aria-haspopup="listbox"], [role="combobox"]:not(input):not(select)')) {
    if (b.hasAttribute('data-jobbot-idx')) continue;
    const st = window.getComputedStyle(b);
    if (st.display === 'none' || st.visibility === 'hidden') continue;
    // The header's settings gear is a listbox button too; a re-find once landed on it.
    if (b.closest('header, nav, [data-automation-id^="utility"]')) continue;
    // Workday points aria-labelledby at the button's OWN text ("Select One" +
    // "Required"), so the field box's <label> comes first, and any candidate
    // that is just the placeholder is discarded.
    const PLACEHOLDER = /^(select one|select\.{0,3}|choose|required|select one required)$/i;
    const clean = s => { s = (s || '').split('\n')[0].trim(); return PLACEHOLDER.test(s) ? '' : s; };
    const ff = b.closest('[data-automation-id^="formField-"], .formField, fieldset');
    let label = '';
    if (ff) { const l = ff.querySelector('label, legend'); if (l) label = clean(l.innerText); }
    if (!label && b.id) { const l = document.querySelector(`label[for="${CSS.escape(b.id)}"]`); if (l) label = clean(l.innerText); }
    if (!label) {
      const ref = b.getAttribute('aria-labelledby');
      if (ref) label = clean(ref.split(/\s+/).map(id => ((document.getElementById(id) || {}).innerText || ''))
                              .filter(t => !PLACEHOLDER.test(t.trim())).join(' '));
    }
    if (!label) label = clean(b.getAttribute('aria-label') || '');
    if (!label) {
      // nearest preceding text block above the button
      let n = b.parentElement, hops = 0;
      while (n && hops < 4 && !label) {
        const t = clean((n.innerText || '').split('\n').map(x => x.trim()).filter(x => x && !PLACEHOLDER.test(x))[0] || '');
        if (t && t.length > 3) label = t;
        n = n.parentElement; hops++;
      }
    }
    const fullLabel = label || '';
    label = fullLabel.slice(0, 160);
    const cur = (b.innerText || '').trim();
    b.setAttribute('data-jobbot-idx', String(i));
    out.push({idx: i, type: 'listbox', name: b.getAttribute('name') || '', id: b.id || '',
              label, tag: 'button', combobox: false, rs: false, placeholder: '',
              // on the FULL label: a long one cut at 160 lost its "*" (NVIDIA's veteran question)
              required: b.getAttribute('aria-required') === 'true' || /\*\s*$/.test(fullLabel) ||
                        !!(ff && ff.querySelector('abbr, [aria-required="true"]')),
              value: /^(select one|select\.{0,3}|choose|please select|--)/i.test(cur) ? '' : cur});
    i++;
  }
  return out;
}
"""


# Companies that host an ATS on their own domain embed the real application
# form in an iframe. Querying only the top-level document finds the site nav
# and no form at all, so locate the form frame first. Which iframe hosts look
# like a form is the platform module's knowledge (FRAME_PATTERNS).


# Rendered form controls only: what a person can see and act on right now.
VISIBLE_CONTROLS_JS = ("() => [...document.querySelectorAll('input:not([type=hidden]),select,textarea,"
                       "button[aria-haspopup=\"listbox\"]')].filter(e => e.type === 'file' || e.getClientRects().length > 0).length")


def count_controls(frame):
    try:
        return frame.evaluate(VISIBLE_CONTROLS_JS)
    except Exception:
        return 0


def pick_form_frame(page, timeout_ms=None, poll_ms=500, hint=None, patterns=()):
    """Return the frame holding the application form, and how it was chosen.

    Embedded ATS iframes attach late and at variable speed, so POLL for one
    rather than sleeping a fixed amount — a fixed wait silently falls back to the
    site nav and fills nothing. `hint` is the frame URL a previous pass recorded;
    `patterns` are what the application's hooks or its platform module name.
    """
    timeout_ms = timeout_ms or cfg("browser.form_frame_timeout_ms", 20000)
    pats = tuple(patterns) + platforms.frame_patterns()
    hinted = (hint or "").split("?")[0] or None
    waited = 0
    while waited < timeout_ms:
        for f in page.frames:
            u = f.url or ""
            if (hinted and u.split("?")[0] == hinted) or any(pat in u for pat in pats):
                n = count_controls(f)
                if n >= 3:
                    return f, f"{'recorded' if hinted and u.split('?')[0] == hinted else 'ATS'} iframe ({n} controls) after {waited}ms"
        # A page with no iframe at all has nothing to wait for (an embed script
        # that injects one has had the post-load wait plus 2s more); the form,
        # if any, is in the page itself.
        if len(page.frames) == 1 and waited >= 2000:
            break
        page.wait_for_timeout(poll_ms)
        waited += poll_ms

    best, best_n = page.main_frame, count_controls(page.main_frame)
    for f in page.frames:
        if f is page.main_frame:
            continue
        n = count_controls(f)
        if n > best_n:
            best, best_n = f, n
    where = "main frame" if best is page.main_frame else f"iframe {best.url[:60]}"
    return best, f"{where} ({best_n} controls) — no ATS iframe found in {timeout_ms}ms"


# The same answer in another portal's words. Used only after an exact /
# prefix / contains match failed, and only for these two shapes:
#   declining   "Decline to self-identify" = "Decline to state" = "I don't wish to answer"
#   negation    "I am not a protected veteran" on a Yes/No menu = "No"
DECLINE_RX = re.compile(r"decline|prefer not|not (wish|want) to|don.?t wish|do not wish|choose not|"
                        r"rather not|not to (say|disclose|answer|state|self)", re.I)
NEGATION_RX = re.compile(r"^(no\b|i am not\b|i.?m not\b|not a\b|i do not\b|i don.?t\b)", re.I)


def equivalent_choice(want, texts):
    """Index of the option that says `want` in the menu's own words, or None."""
    w = str(want or "").strip()
    low = [(t or "").strip().lower() for t in texts]
    if DECLINE_RX.search(w):
        return next((i for i, t in enumerate(low) if DECLINE_RX.search(t)), None)
    if re.match(r"^linkedin", w, re.I):                 # a source: the menu's nearest online channel
        for rx in (r"linkedin", r"social", r"job board|job site|online", r"internet|website|web"):
            i = next((i for i, t in enumerate(low) if re.search(rx, t)), None)
            if i is not None:
                return i
        return None
    if re.match(r"^(mobile|cell)", w, re.I):             # a mobile number: the menu's word for it
        return next((i for i, t in enumerate(low) if re.search(r"cell|mobile", t)), None)
    if NEGATION_RX.match(w):
        return next((i for i, t in enumerate(low) if t == "no" or t.startswith(("no,", "no ", "no-"))), None)
    return None


def pick_option(options, desired):
    """Best matching <option> text for a desired value (or YES/NO)."""
    opts = [o for o in options if o.strip()]
    if not opts:
        return None
    low = [o.lower() for o in opts]

    if desired is YES or desired == YES:
        for i, o in enumerate(low):
            if o.strip() in ("yes", "y", "true"):
                return opts[i]
        for i, o in enumerate(low):
            if o.startswith("yes"):
                return opts[i]
        return None
    if desired is NO or desired == NO:
        for i, o in enumerate(low):
            if o.strip() in ("no", "n", "false"):
                return opts[i]
        for i, o in enumerate(low):
            if o.startswith("no") and "not" not in o[:6]:
                return opts[i]
        return None

    d = str(desired).strip().lower()
    if not d:
        return None
    for i, o in enumerate(low):                       # exact
        if o == d:
            return opts[i]
    for i, o in enumerate(low):                       # containment either way
        if d in o or o in d:
            return opts[i]
    eq = equivalent_choice(d, opts)                   # the same answer, in the menu's words
    if eq is not None:
        return opts[eq]
    # No word-overlap guess: it picked "I identify as a veteran, just not a
    # protected veteran" for "I am not a protected veteran" — most words shared,
    # the opposite meaning. No match means a question, not a guess.
    return None


def resume_path(answers, company_slug, portal):
    """ATS portals parse .docx far better than a LaTeX PDF."""
    files = answers["files"]
    key = ("resume_docx_pattern" if files["upload_for_ats_portal"] == "docx"
           else "resume_pdf_pattern")
    # Tailored files live with the tool (applications/), the base PDF with the
    # resume repo (resume/). Patterns in answers.yaml are relative to those roots.
    p = os.path.join(TOOL, files[key].format(company=company_slug))
    if os.path.isfile(p):
        return p
    alt = os.path.join(TOOL, files["resume_pdf_pattern"].format(company=company_slug))
    if os.path.isfile(alt):
        return alt
    fallback = os.path.join(RESUME, files["default_fallback_pdf"])
    return fallback if os.path.isfile(fallback) else None


def wait_dom_stable(page, max_s=30, quiet=3):
    """Block until the form has stopped re-rendering for `quiet` seconds.

    Both Ashby and Greenhouse rebuild the form after a resume upload, a few
    seconds after their "parsing" text disappears. Every control tagged before
    that rebuild is gone afterwards, which is how one run filled 16 fields and
    then could not find a single one of the rest."""
    sig = None
    still = 0
    for _ in range(max_s):
        page.wait_for_timeout(1000)
        try:
            cur = page.evaluate("() => document.querySelectorAll('[data-jobbot-idx]').length"
                                " + '/' + document.querySelectorAll('input,textarea,select,button').length"
                                " + '/' + (document.body.innerText.length)")
        except Exception:
            return
        if cur == sig:
            still += 1
            if still >= quiet:
                return
        else:
            sig, still = cur, 0


def _alive(page, idx):
    try:
        return page.locator(f'[data-jobbot-idx="{idx}"]').count() > 0
    except Exception:
        return False


def _key(f):
    return (f["type"], norm(f.get("label") or ""), norm(f.get("group_label") or ""),
            (f.get("value") or "") if f["type"] in ("radio", "checkbox", "buttongroup") else "")


def _refind(page, f, everyone=None):
    """Re-tag the (rebuilt) form and return the control that matches `f`.

    Re-tagging renumbers EVERY control, so when `everyone` (the list the caller
    is still walking) is given, all of them get their new index at once — a
    single re-found field next to a list of stale ones is how a LinkedIn box
    once received "Yes"."""
    try:
        fresh = page.evaluate(EXTRACT_JS)
    except Exception:
        return None
    by_key = {}
    by_id = {}
    for g in fresh:
        by_key.setdefault(_key(g), g)
        if g.get("id"):
            by_id.setdefault(g["id"], g)

    def match(x):
        if not norm(x.get("label") or "") and not norm(x.get("group_label") or ""):
            return by_id.get(x["id"]) if x.get("id") else None     # never match two nameless controls
        return by_key.get(_key(x)) or (by_id.get(x["id"]) if x.get("id") else None)

    for x in (everyone or []):
        g = match(x)
        if g:
            x["idx"] = g["idx"]
    return match(f)


def fill_fields(page, resolve, answers, ctx):
    """Fill everything mappable. `page` may be a Page or a Frame.

    ctx["replay"] is the pass's replay.Replay, set by fill_application (explore)
    or submit_approved (replay). Exploring: a required control with no answer
    gets a PLACEHOLDER so the form can be walked to its last page; it is
    recorded as such, kept out of `filled`, and surfaces as a question.
    Replaying: the recorded strategy is tried first, and a placeholder box
    ticked on pass 1 is unticked when it is not the answer (Workday keeps the
    draft between passes)."""
    rep = ctx.get("replay") or ctx.setdefault("replay", R.Replay())
    explore = rep.explore

    fields = page.evaluate(EXTRACT_JS)
    filled, warnings = {}, []
    radio_groups = {}
    # Every field that stayed empty, as data: the label IS the question a
    # human would be asked, and for a menu the options are what it offered.
    # A failed submit turns the required ones into review-page questions.
    missing = []

    def miss(label, required, kind, options=None, reason=""):
        missing.append({"label": (label or "").strip(), "required": bool(required),
                        "kind": kind, "options": [o for o in (options or []) if o][:20],
                        "reason": reason[:160]})

    def offered(how):
        # fill_react_select reports "menu offers: ['a', 'b']" when nothing matched
        m = re.search(r"menu offers: (\[.*\])", how or "")
        if not m:
            return []
        try:
            import ast
            return [str(x) for x in ast.literal_eval(m.group(1))]
        except Exception:
            return []

    recipe = rep.recipe

    # PHASE 1 — upload the resume FIRST, then let the portal's own parser finish.
    # Ashby (and others) autofill from the uploaded CV and REWRITE the form when
    # parsing completes. Filling before that finishes gets silently undone: the
    # first two submit attempts failed with five required fields blank because of
    # exactly this race.
    for f in fields:
        if f["type"] != "file":
            continue
        rp = resume_path(answers, ctx["company_slug"], ctx["portal"])
        if not rp:
            warnings.append(f"no resume file built for {ctx['company_slug']}")
            continue
        # Only a resume field gets the resume. One DeepJudge form had three file
        # inputs and all three received sunil_resume.docx — including
        # "Transcripts of Records". A cover-letter slot gets it only when the
        # portal insists (and says so in the warnings); anything else stays empty.
        lab = norm(f["label"])
        ident = norm(f.get("id") or f.get("name") or "")
        # Greenhouse labels every file input "Attach"; the id says which is which.
        if re.search(r"cover", ident) and not re.search(r"cover", lab):
            lab = "cover letter"
        if lab and not re.search(r"resume|\bcv\b|curriculum|upload file|attach", lab):
            if re.search(r"cover", lab):
                if not f["required"]:
                    continue
                warnings.append(f"'{f['label'][:40]}' is required — no cover letter exists, "
                                f"resume uploaded in its place")
            else:
                if f["required"]:
                    warnings.append(f"REQUIRED file '{f['label'][:50]}' left empty — "
                                    f"not a resume/cover-letter field")
                    miss(f["label"], True, "file", reason="needs a file that is not the resume")
                continue
        try:
            page.set_input_files(f'[data-jobbot-idx="{f["idx"]}"]', rp)
            filled[f["label"] or "Resume"] = os.path.basename(rp)
            recipe(f, "set_input_files", rp)
        except Exception as e:
            warnings.append(f"resume upload failed on '{f['label']}': {e}")

    if any(f["type"] == "file" for f in fields):
        for _ in range(30):                     # up to ~30s
            try:
                body = (page.inner_text("body") or "").lower()
            except Exception:
                break
            if "parsing your resume" not in body and "autofilling" not in body:
                break
            page.wait_for_timeout(1000)
        # Ashby announces the rewrite ("Autofill completed!") a few seconds
        # AFTER the parsing text goes away; Greenhouse re-renders silently.
        # Wait for the tagged controls to stop changing before touching any.
        wait_dom_stable(page)
        # The DOM was rewritten, so the old indices are stale — re-extract.
        fields = page.evaluate(EXTRACT_JS)

    # PHASE 2 — everything else, now that the portal has finished rewriting.
    picked = set()                              # labels a react-select already took
    for f in fields:
        sel = f'[data-jobbot-idx="{f["idx"]}"]'
        label = f["label"] or f["name"]
        ftype = f["type"]

        if ftype == "file":
            continue                            # handled in phase 1
        if f.get("rs"):
            picked.add(norm(label))
        elif not f.get("id") and not f.get("name") and norm(label) in picked:
            continue                            # the picked value's own search box
        if ftype == "search" and not f["required"]:
            continue                            # a widget's own filter box (phone country picker)

        if ftype in R.GROUP_TYPES:
            radio_groups.setdefault(f["name"] or f.get("group_label") or label,
                                    []).append(f)
            continue

        # A re-render between two fields drops every tag; find this one again
        # by what it IS rather than giving up on the rest of the form.
        if not _alive(page, f["idx"]):
            g = _refind(page, f, everyone=fields)
            if not g:
                warnings.append(f"'{label[:60]}' vanished after a re-render — not filled")
                if f["required"]:
                    miss(label, True, "text", reason="field vanished after a re-render")
                continue
            sel = f'[data-jobbot-idx="{f["idx"]}"]'

        val = resolve(label)
        hint = rep.hint(f)
        if val is None and explore and not ctx.get("trust_prefilled") and f["required"] and (f.get("value") or "").strip() \
                and label not in rep.placeholders and ftype in ("listbox", "select-one", "select", "text", "textarea"):
            # A value nobody here put in: an earlier run's placeholder saved in a
            # Workday draft ("Miss" as a Prefix), or the portal's resume parse.
            # Keep it on the page so the walk goes on, but it is a placeholder
            # until he confirms it — a value nobody can account for is never sent.
            cur = f["value"].strip()
            opts = f.get("options") or (listbox_options(page, sel) if ftype == "listbox" else [])
            opts = [o for o in opts if o and not re.match(r"^(select|choose|please select|--)", o.strip(), re.I)]
            rep.placeholder(f, "prefilled", cur, options=opts)
            rep.record[-1]["mismatch"] = True              # only his pick settles it
            warnings.append(f"  '{label[:50]}' already held {cur[:30]!r} (not from his answers) — asked")
            miss(label, True, "dropdown" if opts else "text", opts,
                 f"the form already held {cur[:30]!r}, which is not one of his answers — confirm or change")
            continue
        if val is None:
            ours = rep.placeholders.get(label)
            mine = bool(f["value"]) and ours is not None and norm(str(f["value"])) == norm(str(ours))
            if f["required"] and (not f["value"] or mine):
                opts = f.get("options") or []
                if ftype == "listbox" and not opts:
                    opts = listbox_options(page, sel)          # so the phone shows the real choices
                if mine:
                    # A page filled again (a session retry): our own placeholder is
                    # still there, and still not an answer.
                    miss(label, True, "dropdown" if opts else "text", opts,
                         f"explored with placeholder {str(ours)[:30]!r} — needs your real answer")
                    continue
                warnings.append(f"REQUIRED and unmapped: '{label}'")
                if explore:
                    ok, how, used, seen = explore_fill(page, f, sel, answers, opts)
                    opts = opts or seen
                    if ok:
                        rep.placeholder(f, how, used, options=opts)
                        warnings.append(f"  placeholder {str(used)[:30]!r} entered in '{label[:50]}' to reach the end of the form")
                        miss(label, True, "dropdown" if opts else "text", opts,
                             f"explored with placeholder {str(used)[:30]!r} — needs your real answer")
                        continue
                    warnings.append(f"  no placeholder possible for '{label[:50]}': {how}")
                miss(label, True, "dropdown" if opts else "text", opts, "no stored answer")
            continue

        try:
            if ftype == "listbox":
                text = {YES: "Yes", NO: "No"}.get(val, str(val))
                if f.get("value") and norm(f["value"]) == norm(text):
                    filled[label] = f["value"]              # already showing it
                    recipe(f, "listbox", f["value"], chosen=f["value"])
                else:
                    ok, how = fill_listbox(page, sel, text)
                    if ok:
                        filled[label] = how
                        recipe(f, "listbox", text, chosen=how)
                    elif f["required"] and explore and mismatch_placeholder(
                            page, f, sel, label, text, how, answers, rep, warnings, miss,
                            offered(how) or listbox_options(page, sel)):
                        pass
                    else:
                        warnings.append(f"NOT FILLED{' (REQUIRED)' if f['required'] else ''}: "
                                        f"'{label[:60]}' — {how}. Needs a manual entry.")
                        if f["required"]:
                            miss(label, True, "dropdown", offered(how), how)
            elif ftype == "select-one" or ftype == "select":
                choice = pick_option(f.get("options", []), val)
                if choice:
                    page.select_option(sel, label=choice)
                    filled[label] = choice
                    recipe(f, "select", choice, chosen=choice)
                elif f["required"] and explore and mismatch_placeholder(
                        page, f, sel, label, val, "", answers, rep, warnings, miss, f.get("options") or []):
                    pass
                else:
                    warnings.append(f"no option matched on '{label}' "
                                    f"(wanted {val!r}, options: {f.get('options', [])[:6]})")
                    if f["required"]:
                        miss(label, True, "dropdown", f.get("options"),
                             f"stored answer {str(val)[:30]!r} is not one of the options")
            else:
                text = {YES: "Yes", NO: "No"}.get(val, val)
                # A date control cannot take "2 months from offer acceptance".
                is_date = (ftype == "date"
                           or "date" in (f.get("name") or "").lower()
                           or re.search(r"pick date|dd/mm|mm/dd|yyyy|select date",
                                        (f.get("placeholder") or "") + label, re.I))
                if is_date:
                    iso = (answers.get("availability", {}) or {}).get("earliest_start_iso")
                    if iso:
                        text = iso
                if ftype == "number":
                    text = numeric_for(text, label, answers, ctx.get("market"))
                if str(text).strip():
                    # Recipe says a plain fill did not stick here: go straight to typing.
                    ok, how = fill_verified(page, f["idx"], text, is_date,
                                            combobox=bool(f.get("combobox")) or hint.get("strategy") == "typed",
                                            rs=bool(f.get("rs")),
                                            textarea=f.get("tag") == "textarea",
                                            stable_id=f.get("id") or "")
                    if ok:
                        filled[label] = str(text)
                        recipe(f, how, text)
                    elif f["required"] and explore and f.get("rs") and mismatch_placeholder(
                            page, f, sel, label, text, how, answers, rep, warnings, miss, offered(how)):
                        pass
                    else:
                        warnings.append(
                            f"NOT FILLED{' (REQUIRED)' if f['required'] else ''}: "
                            f"'{label[:60]}' — {how}. Needs a manual entry.")
                        if f["required"]:
                            opts = offered(how)
                            miss(label, True,
                                 "dropdown" if (opts or f.get("rs") or "Yes/No dropdown" in how) else "text",
                                 opts or (["Yes", "No"] if "Yes/No dropdown" in how else []), how)
                    time.sleep(0.08)
        except Exception as e:
            warnings.append(f"could not fill '{label}': {e}")

    # Radios: decide once per group, then click the option whose own label matches.
    settled = []          # (idx, question) buttongroup picks to re-verify once the page has settled
    for gname, opts in radio_groups.items():
        # Prefer the group question (legend) over an individual option's label.
        question = next((o.get("group_label") for o in opts if o.get("group_label")), "")
        if not question:
            question = next((o["label"] for o in opts if o["label"]), gname)
        # The identifying words are often in the OPTIONS, not the heading.
        ask = question + " " + " ".join(o["label"] for o in opts)
        val = resolve(question)
        if val is None:
            val = resolve(ask)
        g = {"label": question, "name": gname, "type": opts[0]["type"],
             "required": any(o["required"] for o in opts), "options": [o["label"] for o in opts]}
        hint = rep.hint_group(g)
        if val is None:
            # A radio group left blank is visible on the form, so always warn —
            # required or not. Silent skips are how a half-filled form gets sent.
            req = " REQUIRED" if g["required"] else ""
            warnings.append(f"radio group unmapped{req}: '{question}'")
            if req:
                if explore:
                    # "No" first: "Yes" opens follow-up required fields ("If yes,
                    # provide the business's name…"), each needing its own
                    # placeholder — Capital One never saved. Still a placeholder.
                    o = next((x for x in opts if re.match(r"^no\b", (x.get("label") or "").strip(), re.I)), opts[0])
                    ok, how = (click_choice if o["type"] == "buttongroup" else robust_check)(page, o["idx"])
                    if ok:
                        rep.placeholder(g, how, o["label"], chosen=o["label"], ask=ask)
                        warnings.append(f"  placeholder choice {o['label'][:30]!r} ticked on '{question[:50]}' to reach the end of the form")
                        miss(question, True, "choice", g["options"],
                             f"explored with placeholder {o['label'][:30]!r} — needs your real answer")
                        continue
                    warnings.append(f"  no placeholder possible on '{question[:50]}': {how}")
                miss(question, True, "choice", g["options"], "no stored answer")
            continue
        # A single-option checkbox group is an acknowledgement: YES means tick
        # the one box, whose label is the statement rather than the word "yes".
        if (val is YES or val == YES) and len(opts) == 1:
            ok, how = robust_check(page, opts[0]["idx"], first=hint.get("strategy"))
            if ok:
                filled[question] = opts[0]["label"] or "confirmed"
                recipe(g, how, "confirmed", chosen=opts[0]["label"], ask=ask)
            else:
                warnings.append(f"COULD NOT TICK '{question[:60]}' ({how})")
            continue
        want = {YES: "yes", NO: "no"}.get(val, str(val).lower())
        # A stored answer is the option's own text (possibly with a trailing
        # note like "— this one only"), so match on the leading phrase.
        want_core = re.split(r"\s+[—-]{1,2}\s+", want)[0].strip()
        # A placeholder box ticked on pass 1 is still ticked in a Workday draft;
        # if it is not the answer, it comes off before the answer goes on.
        ph = hint.get("placeholder") or (hint.get("chosen") if hint.get("dummy") else None)
        if ph and g["type"] == "checkbox":
            for o in opts:
                if norm(o["label"]) == norm(ph) and norm(o["label"]) != norm(want_core):
                    if not robust_uncheck(page, o["idx"]):
                        warnings.append(f"placeholder '{o['label'][:40]}' could not be unticked on '{question[:40]}'")
        for o in opts:
            ol = (o["label"] or "").lower()
            if (want in ol or ol in want or want_core in ol
                    or (want_core and ol.startswith(want_core[:40]))
                    or want == (o["value"] or "").lower()):
                if not _alive(page, o["idx"]):
                    g2 = _refind(page, o, everyone=[x for grp in radio_groups.values() for x in grp])
                    if not g2:
                        warnings.append(f"'{question[:60]}' vanished after a re-render — not ticked")
                        break
                if o["type"] == "buttongroup":
                    ok, how = click_choice(page, o["idx"])
                else:
                    ok, how = robust_check(page, o["idx"], first=hint.get("strategy"))
                if ok:
                    filled[question] = o["label"] or want
                    recipe(g, how, want, chosen=o["label"], ask=ask)
                    if o["type"] == "buttongroup":
                        settled.append((o["idx"], question))
                else:
                    warnings.append(
                        f"COULD NOT TICK '{o['label'][:60]}' for "
                        f"'{question[:60]}' ({how}) — needs a manual click")
                    if g["required"]:
                        miss(question, True, "choice", g["options"], how)
                break
        else:
            warnings.append(f"no radio matched '{question}' (wanted {want!r})")
            if g["required"]:
                miss(question, True, "choice", g["options"],
                     f"stored answer {want[:30]!r} is not one of the choices")

    if settled:
        page.wait_for_timeout(700)          # outlast a debounced form-state sync
        for idx, question in settled:
            if not _alive(page, idx):
                continue
            try:
                st = page.eval_on_selector(f'[data-jobbot-idx="{idx}"]', CHOICE_STATE)
            except Exception:
                continue
            if st == "off":
                ok, how = click_choice(page, idx)
                warnings.append(f"'{question[:50]}' had reverted after settling — re-clicked ({how if ok else 'still not taking'})")

    return filled, warnings, missing


def mismatch_placeholder(page, f, sel, label, stored, how, answers, rep, warnings, miss, offered_opts):
    """Exploration only: a required menu whose options do not include the stored
    answer. Put ANY option in as a placeholder so the walk goes on, and ask him
    with the real choices; his pick is backfilled into replay.json on review.
    Returns True when the placeholder went in."""
    real = [o for o in (offered_opts or []) if o and not re.match(r"^(select|choose|please select|--)", o.strip(), re.I)]
    ok, how2, used, seen = explore_fill(page, f, sel, answers, real)
    opts = real or [o for o in (seen or []) if o and not re.match(r"^(select|choose)", o.strip(), re.I)]
    if not ok:
        return False
    rep.placeholder(f, how2, used, options=opts)
    rep.record[-1]["mismatch"] = True              # a stored answer exists but does not fit: he must pick
    warnings.append(f"  placeholder {str(used)[:30]!r} in '{label[:50]}' — the stored answer "
                    f"{str(stored)[:30]!r} is not one of its choices; asked")
    miss(label, True, "dropdown", opts,
         f"stored answer {str(stored)[:30]!r} is not one of the choices — explored with placeholder {str(used)[:30]!r}")
    return True


def explore_fill(page, f, sel, answers, opts):
    """Pass 1 only: put a placeholder into a required control that has no answer,
    so the walk to the last page is not blocked. Menus take their first real
    option, text takes a type-appropriate stand-in. Returns
    (ok, how, value_used, options_seen)."""
    ftype = f["type"]
    def first_choice(options):
        return next((o for o in options if re.match(r"^no\b", o.strip(), re.I)), options[0])

    if ftype == "listbox":
        opts = [o for o in (opts or listbox_options(page, sel))
                if o and not re.match(r"^(select|choose|please select|--)", o.strip(), re.I)]
        if not opts:
            return False, "listbox offered nothing", None, []
        pick = first_choice(opts)
        ok, how = fill_listbox(page, sel, pick)
        return ok, "listbox" if ok else how, pick, opts
    if ftype in ("select-one", "select"):
        real = [o for o in (f.get("options") or []) if o.strip()
                and not re.match(r"^(select|choose|please select|--+|none)\b", o.strip(), re.I)]
        if not real:
            return False, "select has no real options", None, []
        try:
            pick = first_choice(real)
            page.select_option(sel, label=pick)
            return True, "select", pick, real
        except Exception as e:
            return False, str(e)[:60], None, real
    if f.get("rs"):
        s = f'[id="{f["id"]}"]' if f.get("id") else sel
        menu = rs_options(page, s)
        if not menu:
            return False, "react-select offered nothing", None, []
        pick = first_choice(menu)
        ok, how = fill_react_select(page, s, pick)
        return ok, "react-select" if ok else how, pick, menu
    val = R.dummy_value(f, answers)
    is_date = (ftype == "date" or "date" in (f.get("name") or "").lower()
               or bool(re.search(r"pick date|dd/mm|mm/dd|yyyy|select date",
                                 (f.get("placeholder") or "") + (f.get("label") or ""), re.I)))
    ok, how = fill_verified(page, f["idx"], val, is_date, combobox=bool(f.get("combobox")),
                            textarea=f.get("tag") == "textarea", stable_id=f.get("id") or "")
    return ok, how, val, []


# A fill attempt that got the value wrong for the form — the answer itself needs
# a look, so it stays a question even though an answer is on file.
MISMATCH_RX = re.compile(r"not one of the|no option matched|not a yes/no|menu offers", re.I)


def questions_from_missing(missing, existing, resolve=None, warnings=None):
    """Turn the fill's `missing` records into review-page questions.

    Pure: no browser, no LLM. The portal's label is the question and its menu
    is the option list; a free-text field gets no options (the form offers
    "write my own answer"). A question already on the item is re-opened rather
    than duplicated — its old answer was evidently not what the form takes."""
    import uuid
    out = list(existing or [])
    by_label = {norm(q.get("label", ""))[:90]: q for q in out}   # harvest truncates labels; match on the head
    added = 0
    for m in missing:
        if not m.get("required") or not m.get("label"):
            continue
        # Already answered in answers.yaml / learned.yaml / on the review page:
        # the portal not taking it is the tool's problem, not a question for him.
        # Asking "Given Name?" because a page crashed before filling it is noise.
        if resolve is not None and not MISMATCH_RX.search(m.get("reason") or ""):
            try:
                known = resolve(m["label"]) is not None
            except Exception:
                known = False
            if known:
                if warnings is not None:
                    warnings.append(f"not asked (answer on file, the form did not take it): '{m['label'][:60]}'")
                continue
        L = norm(m["label"])[:90]
        opts = list(m.get("options") or [])
        if not opts and (m.get("kind") == "choice"
                         or re.match(r"^(are|do|did|have|has|were|will|would|can|is)\b|, (do|are|have) you\b",
                                     norm(m["label"]))):
            opts = ["Yes", "No"]                    # a yes/no question shown as a menu we could not read
        q = by_label.get(L)
        if q:
            if q.get("status") == "answered" and q.get("selected") in opts:
                continue                     # answered correctly; the failure was elsewhere
            q["status"] = "open"
            q["previous"] = q.get("selected")
            q["selected"] = None
            # Real choices beat a Yes/No guess: Workday reports one field twice —
            # the filler with its menu, the "would not save" error with nothing —
            # and the guess used to overwrite the menu.
            q["options"] = list(m.get("options") or []) or q.get("options") or opts or []
            q["note"] = m.get("reason", "")
            added += 1
            continue
        out.append({"qid": "q-" + uuid.uuid4().hex[:8], "label": m["label"].rstrip("*").strip(),
                    "options": opts, "kind": "select" if opts else "text",
                    "required": True, "status": "open", "selected": None,
                    "answered_via": None, "feedback": None, "form_group": None,
                    "form_options": opts, "note": m.get("reason", "")})
        by_label[L] = out[-1]
        added += 1
    return out, added


# ARIA live-region and combobox helper text gets read as a label when a widget
# has no real one. It is instruction text for screen readers, not a question.
A11Y_NOISE = re.compile(
    r"type to refine|press down to open|selected\.|is focused|use (up|down) arrow|"
    r"results are available|combobox|listbox|screen reader|no results found|"
    r"loading\.\.\.|clear all|remove item", re.I)


def harvest_questions(target, resolve, warnings, max_questions=8):
    """Every field we could not answer becomes a question for the phone.

    Checkboxes and radios are grouped: a "select all that apply" block is ONE
    question whose options are its member labels, not eleven separate questions.
    Options for free-text questions are left empty on purpose — drafting 2-3 good
    answers is judgment work, so Claude writes them in-session (plan decision A).
    """
    qs = []
    try:
        fields = target.evaluate(EXTRACT_JS)
    except Exception:
        return qs

    groups, singles = {}, []
    for f in fields:
        label = (f["label"] or f["name"] or "").strip()
        if not label or f["type"] == "file":
            continue
        if f["type"] in ("checkbox", "radio", "buttongroup"):
            key = f["name"] or f.get("group_label") or label
            groups.setdefault(key, {"question": f.get("group_label") or "",
                                    "options": [], "required": False,
                                    "type": f["type"]})
            groups[key]["options"].append(label)
            groups[key]["required"] |= bool(f["required"])
        else:
            singles.append((f, label))

    for key, g in groups.items():
        question = g["question"] or key
        if A11Y_NOISE.search(question):
            continue
        combined = question + " " + " ".join(g["options"])
        if resolve(question) is not None or resolve(combined) is not None:
            continue
        qs.append({
            "qid": len(qs), "label": question[:400],
            "kind": "multi" if g["type"] == "checkbox" else "choice",
            "required": g["required"], "form_group": key,
            "form_options": g["options"],
            "options": g["options"][:6],   # the real choices ARE the options
            "selected": None, "feedback": None, "status": "open",
        })

    seen_labels = {norm(g["question"]) for g in
                   [{"question": q["label"]} for q in qs]}
    for f, label in singles:
        if resolve(label) is not None:
            continue
        if not (f["required"] or len(label) > 60):
            continue
        if A11Y_NOISE.search(label):
            continue
        # The same question often appears twice: a hidden input plus its visible
        # widget. Asking Sunil the same thing twice is a bug, not thoroughness.
        key = norm(label)[:90]
        if key in seen_labels:
            continue
        seen_labels.add(key)
        opts = f.get("options") or []
        if f["type"] == "listbox":
            # The menu is the option list; open it once so the phone shows real choices.
            opts = listbox_options(target, f'[data-jobbot-idx="{f["idx"]}"]')
            if not opts and re.match(r"^(are|do|did|have|has|were|will|would|can|is)\b|, (do|are|have) you\b", norm(label)):
                opts = ["Yes", "No"]
        qs.append({
            "qid": len(qs), "label": label[:400],
            "kind": "choice" if (f["type"].startswith("select") or f["type"] == "listbox") else "text",
            "required": bool(f["required"]), "form_group": f["name"],
            "form_options": opts,
            "options": opts[:8],
            "selected": None, "feedback": None, "status": "open",
        })

    qs.sort(key=lambda q: (not q["required"],))
    if len(qs) > max_questions:
        warnings.append(f"{len(qs)} questions found; asking the {max_questions} required ones first")
        qs = qs[:max_questions]
    for i, q in enumerate(qs):
        q["qid"] = i
    if qs:
        warnings.append(f"{len(qs)} question(s) need your input before submitting")
    return qs


def numeric_for(text, label, answers, market):
    """What a <input type=number> can take. A salary question gets the market's
    annual figure from answers.yaml; anything else the first number in the text."""
    if re.search(r"salary|compensation|pay\b|ctc|remuneration", norm(label)):
        comp = answers.get("compensation") or {}
        pay = (comp.get("by_market") or {}).get(market) or comp.get("default") or {}
        for k in ("expected_annual", "expected_monthly"):
            if pay.get(k):
                return str(pay[k])
    m = re.search(r"\d[\d,]*(\.\d+)?", str(text))
    return m.group(0).replace(",", "") if m else str(text)


def _date_forms(iso):
    """The same date, in the forms pickers accept: ISO, then unambiguous ones."""
    try:
        d = dt.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return [str(iso)]
    return [d.isoformat(), d.strftime("%B %-d, %Y"), d.strftime("%-d %B %Y"), d.strftime("%m/%d/%Y")]


def _date_echoed(shown, iso):
    """Does the picker's own text show the day, month and year we meant?"""
    try:
        d = dt.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return bool(shown)
    low = (shown or "").lower()
    nums = {int(x) for x in re.findall(r"\d+", low)}
    month_ok = d.month in nums or d.strftime("%b").lower() in low
    return d.year in nums and d.day in nums and month_ok


def click_field(frame, sel):
    """Click a control even when a sticky banner sits over it.

    Ashby keeps its "Autofill from resume" panel sticky at the top; after
    Playwright scrolls a field into view that panel covers it, the click is
    "intercepted" and times out — which is how every Yes/No on Airwallex and
    Taktile stayed blank. Escalate: normal click, then centre it and force,
    then a plain JS focus."""
    try:
        frame.click(sel, timeout=4000)
        return "click"
    except Exception:
        pass
    try:
        frame.eval_on_selector(sel, "el => el.scrollIntoView({block: 'center'})")
        frame.wait_for_timeout(300)
        frame.click(sel, timeout=3000, force=True)
        return "force-click"
    except Exception:
        pass
    frame.eval_on_selector(sel, "el => { el.focus(); el.click && el.click(); }")
    return "js-focus"


def rs_value(frame, sel):
    """The text react-select is actually showing for this input, or ''."""
    try:
        return (frame.eval_on_selector(sel, """el => {
            const c = el.closest('.select__control') || el.parentElement;
            const v = c && c.querySelector('.select__single-value, [class*="single-value"]');
            return v ? v.innerText : '';
        }""") or "").strip()
    except Exception:
        return ""


def fill_verified(frame, idx, text, is_date=False, combobox=False, rs=False, textarea=False,
                  stable_id=""):
    """Type a value and READ IT BACK. A fill that reports success on a hidden
    input while the visible combobox stays empty is how required fields ended up
    blank on a form the code called complete. Escalate until the value sticks."""
    sel = f'[data-jobbot-idx="{idx}"]'

    def value():
        try:
            return (frame.eval_on_selector(sel, "el => el.value") or "").strip()
        except Exception:
            return ""

    if rs:
        # react-select swaps its <input> node on focus, taking our tag with it
        # ("Failed to find element matching selector" half-way through). The
        # id survives the swap, so address the field by that.
        if stable_id:
            sel = f'[id="{stable_id}"]'
        return fill_react_select(frame, sel, text)

    # A React typeahead ignores a programmatic fill: el.value gets set, the
    # component never re-renders, and the field LOOKS empty while read-back
    # passes. So a combobox always goes straight to real typing — and so does a
    # date: a picker accepted "2026-11-04" by value and then showed 11/10/2026.
    if not combobox and not is_date:
        try:
            frame.fill(sel, str(text), timeout=4000)
            if value():
                if textarea:
                    # A controlled React textarea sometimes keeps the DOM text
                    # but not the state (Ashby: "Missing entry" under a full
                    # box). One real keystroke in and out commits it.
                    try:
                        frame.press(sel, "End", timeout=2000)
                        frame.type(sel, " ", delay=30)
                        frame.press(sel, "Backspace", timeout=2000)
                    except Exception:
                        pass
                return True, "fill"
        except Exception:
            pass
    # Typeahead/date widgets ignore programmatic fill — type like a person.
    if is_date:
        # A picker parses what is typed in ITS format: "2026-11-04" once became
        # 11/10/2026 on Ashby. Type, read back, and move to a form it cannot
        # misread (the month by name) until the echo shows our day and month.
        last = ""
        for form in _date_forms(text):
            try:
                click_field(frame, sel)
                frame.press(sel, "Control+A")
                frame.press(sel, "Backspace")
                frame.type(sel, form, delay=int(cfg("browser.type_delay_ms", 25)))
                frame.wait_for_timeout(800)
                frame.press(sel, "Enter")
                frame.wait_for_timeout(500)
                last = value()
            except Exception as e:
                return False, str(e)[:60]
            if _date_echoed(last, text):
                return True, f"typed date ({form})"
        return (True, f"typed (date echo unverified: {last[:20]!r})") if last else (False, "value did not stick")
    try:
        click_field(frame, sel)
        frame.eval_on_selector(sel, "el => { el.value=''; }")
        frame.type(sel, str(text), delay=int(cfg("browser.type_delay_ms", 25)))
        frame.wait_for_timeout(1000)
        # A rendered dropdown option must be CLICKED. Greenhouse's country
        # autocomplete ignores Enter, leaving the field visibly empty while
        # el.value looks set — which is how a required field stayed blank.
        picked = False
        want = str(text).split(",")[0].strip().lower()
        for osel in ('[role="option"]', "li[id*='option']", ".select__option",
                     "[class*='option']:not(input)"):
            try:
                opts = frame.locator(osel)
                n = min(opts.count(), 12)
                for i in range(n):
                    o = opts.nth(i)
                    if not o.is_visible(timeout=400):
                        continue
                    if want in (o.inner_text() or "").strip().lower():
                        o.click(timeout=3000)
                        picked = True
                        break
            except Exception:
                continue
            if picked:
                break
        if not picked:
            frame.press(sel, "ArrowDown")
            frame.wait_for_timeout(400)
            frame.press(sel, "Enter")
        frame.wait_for_timeout(500)
        v = value()
        if v and (not combobox or v.lower() != str(text).lower()
                  or frame.eval_on_selector(
                      sel, "el => el.getAttribute('aria-expanded') !== 'true'")):
            return True, "typed"
        if v:
            return True, "typed (unconfirmed selection)"
    except Exception as e:
        return False, str(e)[:60]
    return False, "value did not stick"


def _rs_visible(frame):
    """react-select's rendered menu rows: [(locator, lower-cased text)]."""
    found = []
    for osel in ('.select__option', '[role="option"]'):
        try:
            opts = frame.locator(osel)
            for i in range(min(opts.count(), 20)):
                o = opts.nth(i)
                if o.is_visible(timeout=200):
                    found.append((o, (o.inner_text() or "").strip().lower()))
        except Exception:
            continue
        if found:
            break
    return found


def rs_options(frame, sel, limit=20):
    """Open a react-select, read what its menu offers, close it again."""
    out = []
    try:
        click_field(frame, sel)
        for _ in range(6):
            frame.wait_for_timeout(500)
            out = [t for _, t in _rs_visible(frame)]
            if out and not all(t in ("loading...", "loading", "no options") for t in out):
                break
        frame.press(sel, "Escape")
    except Exception:
        pass
    return out[:limit]


def fill_react_select(frame, sel, text):
    """Greenhouse (react-select): open, type to filter, pick the option, then
    read the rendered single-value — el.value is always '' after a pick."""
    want_full = str(text).strip().lower()
    # A stored answer is often a sentence ("Yes. I post-trained Qwen3-4B …")
    # for what is a Yes/No dropdown. Try the full text, the first clause,
    # then the bare yes/no it opens with.
    cands = [want_full, want_full.split(",")[0].strip()]
    m = re.match(r"^(yes|no)\b", want_full)
    if m:
        cands.append(m.group(1))
    seen, order = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c); order.append(c)
    def visible_options():
        return _rs_visible(frame)

    try:
        click_field(frame, sel)
        frame.wait_for_timeout(500)
        # A Yes/No dropdown given a sentence that does not start with yes/no is
        # a question nobody has answered yet. Never guess it.
        vis0 = visible_options()
        menu = {t for _, t in vis0}
        eq = equivalent_choice(want_full, [t for _, t in vis0])
        if eq is not None and want_full not in menu:
            try:
                vis0[eq][0].click(timeout=3000)
                frame.wait_for_timeout(400)
                shown = rs_value(frame, sel)
                if shown:
                    return True, f"react-select ({shown[:30]})"
            except Exception:
                pass
        if menu and menu <= {"yes", "no", "n/a", "not applicable", "prefer not to say"} and not m:
            frame.press(sel, "Escape")
            return False, "Yes/No dropdown, stored answer is not a yes/no — needs your answer"
        for want in order:
            frame.press(sel, "Control+A")
            frame.press(sel, "Backspace")
            frame.type(sel, want[:60], delay=int(cfg("browser.type_delay_ms", 25)))
            # A city typeahead fetches its suggestions (Culture Amp's
            # "Location (City)" took ~2 s); a Yes/No menu is instant. Poll.
            vis = []
            for _ in range(8):
                frame.wait_for_timeout(500)
                vis = visible_options()
                if vis and not all(t in ("loading...", "loading", "no options") for _, t in vis):
                    break
            # Exact first, then prefix, then substring — "India" typed into the
            # phone-country picker once landed on "British Indian Ocean
            # Territory (+246)" because that sorts first.
            ranked = ([o for o in vis if o[1] == want]
                      + [o for o in vis if o[1].startswith(want) and o[1] != want]
                      + [o for o in vis if want in o[1] and not o[1].startswith(want)])
            for o, ot in ranked:
                try:
                    o.click(timeout=3000)
                except Exception:
                    continue
                frame.wait_for_timeout(400)
                shown = rs_value(frame, sel)
                if shown:
                    return True, f"react-select ({shown[:30]})"
        # Nothing matched by text. Taking whatever the menu highlights would be
        # a guess on a form that goes out under his name, so leave it empty.
        # Say what the menu DID offer, so the next answer can be one of them.
        frame.press(sel, "Control+A")
        frame.press(sel, "Backspace")
        frame.wait_for_timeout(500)
        offered = [t for _, t in visible_options()][:12]
        frame.press(sel, "Escape")
        return False, (f"react-select: no option matched {order[-1][:30]!r}"
                       + (f"; menu offers: {offered}" if offered else ""))
    except Exception as e:
        return False, str(e)[:60]


LISTBOX_OPTIONS = ('[role="option"], li[role="option"], [data-automation-id="promptOption"], '
                   '[data-automation-id="menuItem"], [role="menuitem"]')


def listbox_options(frame, sel):
    """Open a listbox button, read ITS options, close it again. Options another
    menu left in the page (Workday keeps the phone-code list: "India (+91)")
    were there before the click, so only the new ones count."""
    def visible():
        try:
            return frame.evaluate("""(sel) => [...document.querySelectorAll(sel)]
                .filter(o => o.getClientRects().length).map(o => (o.innerText || '').trim())""", LISTBOX_OPTIONS) or []
        except Exception:
            return []
    before = set(visible())
    out = []
    try:
        click_field(frame, sel)
        frame.wait_for_timeout(600)
        after = visible()
        out = [o for o in after if o not in before] or after
    except Exception:
        pass
    try:
        frame.keyboard.press("Escape")
    except Exception:
        pass
    return [o for o in out if o and not re.match(r"^(select one|select\.{0,3}|choose|please select|--+)$", o.strip(), re.I)]


def fill_listbox(frame, sel, text):
    """Pick from a <button aria-haspopup=listbox> menu: exact > prefix > contains,
    with the yes/no cascade; never an unmatched option."""
    want_full = str(text).strip().lower()
    cands = [want_full, want_full.split(",")[0].strip()]
    m = re.match(r"^(yes|no)\b", want_full)
    if m:
        cands.append(m.group(1))
    order = []
    for c in cands:
        if c and c not in order:
            order.append(c)
    try:
        # All options in one JS read: a country list has ~250 entries and the
        # right one must compete, or a substring ("British INDIAn Ocean
        # Territory") wins over the exact "India" further down.
        def read():
            try:
                rows = frame.evaluate("""(sel) => [...document.querySelectorAll(sel)].map((o, i) => {
                    const r = o.getBoundingClientRect(), cs = getComputedStyle(o);
                    const shown = r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
                    // never the page header's account menu, nor already-picked pills
                    const noise = o.closest('header, nav, [data-automation-id="selectedItemList"], [data-automation-id="selectedItem"], [data-automation-id^="utility"]');
                    return [i, shown && !noise ? (o.innerText || '').trim() : null];
                  }).filter(x => x[1])""", LISTBOX_OPTIONS) or []
                loc = frame.locator(LISTBOX_OPTIONS)
                return [(loc.nth(i), t.lower()) for i, t in rows]
            except Exception:
                return []

        vis = []
        for opening in range(2):
            click_field(frame, sel)
            for _ in range(5):                       # menus render late; a second click re-opens a toggled-shut one
                frame.wait_for_timeout(500)
                vis = read()
                if vis:
                    break
            if vis:
                break
            frame.keyboard.press("Escape")
            frame.wait_for_timeout(300)
        if not vis:
            # Keep the evidence: what the button says about itself and any popup
            # in the DOM, so a menu that will not open can be diagnosed offline.
            try:
                info = frame.evaluate("""(sel) => {
                  const b = document.querySelector(sel);
                  const attrs = b ? [...b.attributes].map(a => a.name + '=' + a.value.slice(0, 80)).join(' ') : 'no button';
                  const pops = [...document.querySelectorAll('[role="listbox"], [role="menu"], [data-automation-id*="menu" i], [data-automation-id*="popup" i], [data-automation-id*="List"]')]
                    .map(e => e.outerHTML.slice(0, 1200));
                  return {attrs, pops: pops.slice(0, 6), html: b ? (b.parentElement.parentElement.outerHTML.slice(0, 2500)) : ''};
                }""", sel)
                os.makedirs(os.path.join(DATA, "debug"), exist_ok=True)
                fn = os.path.join(DATA, "debug", f"listbox-{dt.datetime.now():%m%d%H%M%S}.json")
                json.dump(info, open(fn, "w"), indent=1)
            except Exception:
                pass
        menu = {t for _, t in vis}
        eq = equivalent_choice(want_full, [t for _, t in vis])
        if eq is not None and not any(t == want_full for _, t in vis):
            o, ot = vis[eq]
            try:
                o.scroll_into_view_if_needed(timeout=2000)
                o.click(timeout=3000)
                frame.wait_for_timeout(400)
                return True, f"{ot} (for {want_full[:30]!r})"
            except Exception:
                pass
        if menu and menu <= {"yes", "no", "n/a", "not applicable", "prefer not to say"} and not m:
            frame.keyboard.press("Escape")
            return False, "Yes/No dropdown, stored answer is not a yes/no — needs your answer"
        for want in order:
            ranked = ([o for o in vis if o[1] == want]
                      + [o for o in vis if o[1].startswith(want) and o[1] != want]
                      + [o for o in vis if want in o[1] and not o[1].startswith(want)])
            for o, ot in ranked:
                try:
                    o.scroll_into_view_if_needed(timeout=2000)
                    o.click(timeout=3000)
                except Exception:
                    continue
                frame.wait_for_timeout(400)
                shown = ""
                try:
                    shown = (frame.locator(sel).first.inner_text() or "").strip()
                except Exception:
                    pass
                return True, shown or ot
        frame.keyboard.press("Escape")
        offered = [t for _, t in vis][:12]
        return False, (f"listbox: no option matched {order[-1][:30]!r}"
                       + (f"; menu offers: {offered}" if offered else ""))
    except Exception as e:
        return False, str(e)[:60]


CHOICE_STATE = """el => {
    const p = el.getAttribute('aria-pressed'), c = el.getAttribute('aria-checked'), s = el.getAttribute('aria-selected');
    if (p !== null || c !== null || s !== null) return (p === 'true' || c === 'true' || s === 'true') ? 'on' : 'off';
    if (/\\b(selected|active|checked|pressed)\\b/i.test(el.className || '') || el.dataset.state === 'on' || el.dataset.state === 'checked') return 'on';
    if (/\\b(selected|active|checked|pressed)\\b/i.test([...el.parentElement.querySelectorAll('button')].map(b => b.className).join(' '))) return 'off';
    return 'unknown';
}"""


def click_choice(frame, idx):
    """Click a <button>-rendered choice and READ IT BACK where the button says
    its state (aria-pressed / aria-checked / a selected class). Ashby's Yes/No
    once reported 'force-click' on a button that never took: the sticky panel
    swallowed the click. Escalate through a real click, a forced one, then a
    DOM click."""
    sel = f'[data-jobbot-idx="{idx}"]'

    def state():
        try:
            return frame.eval_on_selector(sel, CHOICE_STATE)
        except Exception:
            return "unknown"

    def centred_click():
        # Playwright scrolls a target minimally, which parks it at the top edge —
        # under Ashby's sticky "Autofill from resume" panel — and the click lands
        # on the panel. A DOM click then flips the button's look but not the
        # form's value ("Missing entry" on submit). Centre it, then click for real.
        frame.eval_on_selector(sel, "el => el.scrollIntoView({block: 'center'})")
        frame.wait_for_timeout(200)
        frame.click(sel, timeout=4000)

    rungs = (("click", centred_click),
             ("force-click", lambda: frame.click(sel, timeout=3000, force=True)),
             ("js-click", lambda: frame.eval_on_selector(sel, "el => el.click()")))
    err = ""
    for name, fn in rungs:
        try:
            fn()
        except Exception as e:
            err = str(e)[:60]
            continue
        frame.wait_for_timeout(250)
        st = state()
        if st in ("on", "unknown"):
            return True, name
    return False, (f"choice did not take ({err})" if err else "choice did not take")


def robust_check(frame, idx, first=None):
    """Tick a checkbox/radio that a styled overlay intercepts clicks for.

    Greenhouse (and most modern ATS) visually hide the real input and render a
    styled box on top, so Playwright's click lands on the overlay and check()
    reports the state never changed. Escalate: native check, then click the
    associated <label>, then set it directly and fire the events a framework
    listens for. `first` is the rung a pass-1 recipe recorded as the one that
    worked; a replay tries it before the rest.
    """
    sel = f'[data-jobbot-idx="{idx}"]'
    checked = "el => !!(el.checked || el.getAttribute('aria-checked') === 'true')"

    def native():
        frame.check(sel, timeout=3000)
        return True

    def label_click():
        frame.eval_on_selector(sel, """el => {
            const l = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
                            : el.closest('label');
            if (l) l.click();
        }""")
        return bool(frame.eval_on_selector(sel, "el => el.checked"))

    def force_click():
        # A styled overlay that swallows the normal click still takes a forced one.
        frame.eval_on_selector(sel, "el => el.scrollIntoView({block: 'center'})")
        frame.click(sel, timeout=3000, force=True)
        return bool(frame.eval_on_selector(sel, checked))

    def js_set():
        frame.eval_on_selector(sel, """el => {
            if (!(el instanceof HTMLInputElement)) { el.click(); return; }
            const set = Object.getOwnPropertyDescriptor(
                HTMLInputElement.prototype, 'checked').set;
            set.call(el, true);
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            el.dispatchEvent(new Event('click', {bubbles: true}));
        }""")
        return bool(frame.eval_on_selector(sel, "el => el.checked"))

    rungs = [("check", native), ("label-click", label_click),
             ("force-click", force_click), ("js-set", js_set)]
    if first:
        rungs.sort(key=lambda r: r[0] != first)
    err = ""
    for name, fn in rungs:
        try:
            if fn():
                return True, name
        except Exception as e:
            err = str(e)[:80]
    return False, err or "state unchanged after all strategies"


def robust_uncheck(frame, idx):
    """Untick a box (a placeholder from exploration that is not the answer)."""
    sel = f'[data-jobbot-idx="{idx}"]'
    checked = "el => !!(el.checked || el.getAttribute('aria-checked') === 'true')"
    try:
        if not frame.eval_on_selector(sel, checked):
            return True
        frame.uncheck(sel, timeout=3000)
    except Exception:
        try:
            frame.click(sel, timeout=3000, force=True)
        except Exception:
            pass
    try:
        return not frame.eval_on_selector(sel, checked)
    except Exception:
        return False


def _virtual_display():
    """A headed Chrome on an Xvfb display nobody looks at: the browser is real
    (never headless — portals treat that differently) but never covers a window
    on the desktop. Starts Xvfb once and reuses it. Returns ":N" or None."""
    import shutil as _sh, subprocess as _sp, time as _t
    if not _sh.which("Xvfb"):
        return None
    n = int(cfg("browser.virtual_display", 99))
    disp, lock = f":{n}", f"/tmp/.X{n}-lock"
    if os.path.exists(lock):
        try:
            _sp.run(["xdpyinfo", "-display", disp], capture_output=True, timeout=5, check=True)
            return disp                                    # already up
        except Exception:
            try:
                os.remove(lock)                            # stale lock from a crash
            except OSError:
                return None
    try:
        _sp.Popen(["Xvfb", disp, "-screen", "0", "1400x1800x24", "-nolisten", "tcp"],
                  stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, start_new_session=True)
    except OSError:
        return None
    for _ in range(20):
        _t.sleep(0.25)
        if os.path.exists(lock):
            return disp
    return None


def _wait(page, ms):
    """A one-off pause, scaled by browser.pace (polling loops are not scaled:
    they already stop as soon as the page is ready)."""
    page.wait_for_timeout(max(150, int(ms * float(cfg("browser.pace", 0.5)))))


def _profile_busy():
    """PID of a live Chrome holding the persistent profile, or None. Chrome
    writes SingletonLock -> '<host>-<pid>'; a stale link (crash) points at a
    dead pid and does not count."""
    try:
        target = os.readlink(os.path.join(PROFILE_DIR, "SingletonLock"))
    except OSError:
        return None
    try:
        pid = int(target.rsplit("-", 1)[1])
        os.kill(pid, 0)
        return pid
    except (ValueError, IndexError, ProcessLookupError, PermissionError):
        return None


def _wait_for_profile(max_s=None):
    """One real Chrome profile, several processes that may want it (the
    pipeline, an inbox prep the review page starts, a submit run). Wait for the
    holder to finish rather than crash on Chrome's ProcessSingleton."""
    max_s = max_s or cfg("browser.profile_wait_s", 1800)
    waited = 0
    while waited < max_s:
        pid = _profile_busy()
        if pid is None:
            return
        if waited == 0:
            print(f"  [browser] profile in use by Chrome pid {pid} (another run) — waiting", flush=True)
        time.sleep(5)
        waited += 5
    raise RuntimeError(f"browser profile still in use after {max_s}s")


def _browser(pw):
    """Real Chrome on the persistent profile. Where its window goes is
    browser.display: `virtual` (Xvfb, invisible; the default when Xvfb is
    installed), `offscreen` (a real window parked far outside the screen), or
    `own` (on the desktop, for watching it work)."""
    os.makedirs(PROFILE_DIR, exist_ok=True)
    _wait_for_profile()
    mode = cfg("browser.display", "auto")
    args = ["--disable-blink-features=AutomationControlled"]
    env = None
    if mode in ("auto", "virtual"):
        disp = _virtual_display()
        if disp:
            env = dict(os.environ, DISPLAY=disp)
        elif mode == "virtual":
            print("  [browser] Xvfb not available — falling back to an off-screen window")
            mode = "offscreen"
        else:
            mode = "offscreen"
    if mode == "offscreen" and env is None:
        args += ["--window-position=-32000,-32000", "--window-size=1280,1600"]
    kw = {"channel": "chrome", "headless": cfg("browser.headless", False),
          "viewport": {"width": 1280, "height": 1600}, "args": args}
    if env:
        kw["env"] = env
    return pw.chromium.launch_persistent_context(PROFILE_DIR, **kw)


# A posting that was filled or withdrawn after it was found. Checked right after
# the page opens: a Workday tenant's "page doesn't exist" once kept a wizard
# driver hunting for an Apply button for ten minutes.
DEAD_RX = re.compile(r"page you are looking for (doesn.t|does not) exist|has been filled|"
                     r"no longer accepting applications|no longer available|job (is|has been) closed|"
                     r"position (has been|is) (closed|filled)|this job is closed|job (has )?expired|"
                     r"posting (has been )?removed|job (you are looking for|requested) (is|was) not found", re.I)


def dead_posting(page, wait_s=15):
    """The closing phrase if the opened page is a dead posting, else None.
    Checks every second until the phrase appears, or the page is plainly live
    (a form with controls, or a long job description), or wait_s passes. A
    Workday tenant paints its header first and the "doesn't exist" message
    seconds later, so a single early look says nothing. Only a short page
    counts: a live JD can mention 'no longer available' in passing."""
    for _ in range(wait_s):
        for f in page.frames:
            try:
                body = f.inner_text("body") or ""
            except Exception:
                continue
            m = DEAD_RX.search(body[:4000])
            if m and len(body) < 3000:
                return m.group(0)
            if len(body) >= 3000 or count_controls(f) >= 3 or any(walk.is_start(b) for b in walk.buttons(f)):
                return None                     # a live page: a JD, a form, or an Apply button
        page.wait_for_timeout(1000)
    return None


def begin_exploration(page, ctx, rep):
    """After the page is open: settle which platform this really is, and what is
    known about it. Returns (knowledge, custom_explore_or_None)."""
    # The link's platform was decided from its host; an embedded form names
    # the real one (a company page holding a Greenhouse iframe). Re-detect
    # now that the frames are here.
    pid, is_known = platforms.detect(ctx["url"], [f.url for f in page.frames])
    if is_known and pid != ctx.get("portal"):
        print(f"  [platform] {ctx.get('portal')!r} -> {pid!r} (embedded form)")
        ctx["portal"] = pid
    rep.platform = ctx.get("portal")
    gone = dead_posting(page)
    if gone:
        raise SystemExit(f"EXPIRED: posting is closed ({gone!r}) — {ctx['url']}")
    # What is known: the application's own hooks, then the platform module —
    # either may bring a driver (Workday's wizard needs an account). Anything
    # else is walked generically.
    know = hooks.knowledge(rep.platform, ctx["company_slug"])
    print(f"  [know] {know.describe()}")
    return know, know.custom("explore")


def carry_recipes(ctx, rep):
    """A slug explored before keeps what worked: its recipes become hints, so
    the controls that were right take the fast path and only the rest is news."""
    doc = R.load({"company_slug": ctx["company_slug"]})
    if doc.get("recipes"):
        rep.recipes = doc["recipes"]
        print(f"  [carry] {len(rep.recipes)} recipe(s) from the previous exploration"
              f"{' (which reached the last page)' if doc.get('reached_end') else ''}")


def finish_exploration(ctx, rep, res, page, shot_abs, custom, know, resolve=None):
    """Screenshot, warnings summary, the no-form check, replay.json. Returns
    (filled, warnings, questions, placeholders, replay_rel, shot_ok)."""
    filled, warnings, questions = res["filled"], res["warnings"], res["questions"]
    rep.reached_end = bool(res.get("reached_end"))
    blocked = [t for f in page.frames for t in walk.blocked_clicks(f)]
    if blocked:
        warnings.append(f"guard blocked {len(blocked)} click(s) on submit-looking buttons: {blocked[:3]}")
    # Fields a page refused, and every placeholder, become questions.
    questions, _ = questions_from_missing(res["missing"], questions, resolve=resolve, warnings=warnings)
    for i, q in enumerate(questions):
        q["qid"] = i
    page.wait_for_timeout(500)
    shot_ok = True
    try:
        page.screenshot(path=shot_abs, full_page=True)
    except Exception as e:
        warnings.append(f"screenshot failed: {e}")
        shot_ok = False
    placeholders = rep.placeholders
    if placeholders:
        warnings.append(f"{len(placeholders)} placeholder(s) entered to explore the whole form — "
                        f"each is a question below; nothing is submitted while one is unanswered")
    print(f"  [fill] {len(filled)} fields, {len(placeholders)} placeholders, "
          f"{len(rep.record)} recipes ({rep.replayed} by earlier recipe), {len(warnings)} warnings; "
          f"{res.get('pages_done', 0)} page(s), "
          f"{'reached the last page' if rep.reached_end else 'did NOT reach the last page'}")
    for w in warnings:
        print(f"    ! {w}")
    # No form at all (a login wall, a JS shell, a listing page): hand the role to
    # the apply-by-hand pack instead of queueing an empty item.
    if not custom and not filled and not questions and not any((p.get("controls") or 0) >= 3 for p in rep.pages):
        raise SystemExit(f"Portal '{ctx.get('portal')}' is not supported for autofill — "
                         f"no application form found at {ctx['url']}")
    replay_rel = rep.save(ctx)
    if replay_rel:
        print(f"  [replay] {replay_rel}")
    R.annotate_questions(questions, placeholders)
    return filled, warnings, questions, placeholders, replay_rel, shot_ok


def write_item(ctx, answers, pay, rep, know, item_id, shot_rel, filled, warnings, questions, placeholders, replay_rel):
    """The queue item the phone shows. Drafted options and answers already given
    on an earlier unsubmitted item for this slug are carried across."""
    prev = None
    try:
        import glob as _glob
        olds = sorted(_glob.glob(os.path.join(QUEUE_DIR, f"{ctx['company_slug']}-*.json")))
        for f in reversed(olds):
            cand = json.load(open(f))
            if cand.get("status") not in ("submitted",):
                prev = cand
                break
    except Exception:
        prev = None
    if prev:
        old_q = {norm(q.get("label", "")): q for q in prev.get("questions") or []}
        carried = 0
        for q in questions:
            o = old_q.get(norm(q.get("label", "")))
            if not o:
                continue
            if o.get("options") and not q.get("options"):
                q["options"] = o["options"]
                carried += 1
            if o.get("status") == "answered":
                q.update({"selected": o.get("selected"), "status": "answered",
                          "answered_via": o.get("answered_via")})
        if carried:
            print(f"  [carry] reused {carried} drafted answer set(s) from {prev['id']}")

    item = {
        "id": item_id,
        "company": ctx["company"], "company_slug": ctx["company_slug"],
        "role": ctx["role"], "location": ctx["location"],
        "url": ctx["url"], "portal": ctx["portal"],
        "market": ctx["market"], "salary_quoted": pay["expected_text"],
        "score": ctx.get("score"),
        "resume": resume_path(answers, ctx["company_slug"], ctx["portal"]),
        "screenshot": shot_rel,
        "fields": filled, "warnings": warnings,
        "placeholders": placeholders, "replay": replay_rel,
        "pages": len(rep.pages), "reached_end": rep.reached_end, "knowledge": know.describe(),
        "questions": questions,
        # An exploration that did not reach the last page is not ready to review:
        # approving one (Palo Alto, NVIDIA) approved a route nobody had walked.
        "status": ("needs_input" if questions else "pending") if rep.reached_end else "failed",
        "fail_reason": None if rep.reached_end else
            ("exploration did not reach the last page — " + (warnings[-1][:200] if warnings else "no reason given")),
        "created": dt.datetime.now().isoformat(timespec="seconds"),
    }
    os.makedirs(QUEUE_DIR, exist_ok=True)
    with open(os.path.join(QUEUE_DIR, f"{item_id}.json"), "w") as f:
        json.dump(item, f, indent=2)
    print(f"  [queue] {item_id} -> {item['status']}")
    return item


def fill_application(ctx, answers, resolve, pay, submit=False):
    """PASS 1. Explores, screenshots, queues. Contains no submit path whatsoever."""
    assert submit is False, "fill_application never submits; use submit_approved()"
    item_id = f"{ctx['company_slug']}-{dt.datetime.now():%m%d%H%M}"
    # Exploration: walk to the last page, placeholders where no answer exists,
    # a recipe per control. Nothing here can submit.
    rep = ctx["replay"] = R.Replay(explore=True)
    carry_recipes(ctx, rep)
    shot_rel = os.path.join("data", "queue", f"{item_id}.png")   # relative to TOOL
    shot_abs = os.path.join(TOOL, shot_rel)

    with sync_playwright() as pw:
        br = _browser(pw)
        page = br.new_page()
        walk.guard_exploration(page)         # submit-looking buttons are inert on this pass
        print(f"  [browser] opening {ctx['url']}")
        page.goto(ctx["url"], wait_until="domcontentloaded", timeout=60000)
        _wait(page, 4000)          # let embedded ATS iframes load
        know, custom = begin_exploration(page, ctx, rep)
        res = custom(page, ctx, answers, resolve, rep) if custom else None
        if res is None:
            res = walk.explore(page, ctx, answers, resolve, rep)
        filled, warnings, questions, placeholders, replay_rel, shot_ok = \
            finish_exploration(ctx, rep, res, page, shot_abs, custom, know, resolve=resolve)
        br.close()
    return write_item(ctx, answers, pay, rep, know, item_id, shot_rel if shot_ok else None,
                      filled, warnings, questions, placeholders, replay_rel)


CODE_INPUTS = ('input[autocomplete="one-time-code"], input[maxlength="1"], '
               'input[name*="code" i], input[id*="code" i], input[aria-label*="code" i], '
               'input[aria-label*="digit" i], input[aria-label*="character" i]')


def enter_verification_code(page, target, it, submit_desc=None):
    """If the portal is waiting on an emailed code, get it from Sunil and enter
    it. `submit_desc` is the button press_submit already resolved as THE submit
    button; re-find that one specific button rather than searching again, so a
    same-text decoy elsewhere on the page (a header "Apply" link, say) is never
    a candidate. Returns True when a code was typed (the caller re-checks the
    outcome)."""
    try:
        body = (target.inner_text("body") or "").lower()
    except Exception:
        return False
    if not re.search(r"verification code|security code|code (was|has been) sent|enter the .{0,12}code", body):
        return False
    from jobpilot.review.ask import ask
    code = ask(f"code-{it['id']}",
               f"{it['company']} — {it['role']}: the portal emailed a verification code "
               f"to sunil4832sharma@gmail.com. Paste it here.",
               hint="8 characters, from the email that just arrived")
    code = re.sub(r"\s+", "", code or "")
    if not code:
        it.setdefault("warnings", []).append("verification code not received in time — not submitted")
        return False
    try:
        boxes = target.locator(CODE_INPUTS)
        n = boxes.count()
        if n == 0:
            it.setdefault("warnings", []).append("verification code asked for but no input found")
            return False
        if n >= len(code):
            # One box per character; typing into the first advances in every
            # OTP widget seen so far, and the per-box fill is the fallback.
            boxes.first.click(timeout=3000, force=True)
            page.keyboard.type(code, delay=90)
            page.wait_for_timeout(600)
            if not (boxes.nth(1).input_value() or "").strip():
                for i, ch in enumerate(code[:n]):
                    boxes.nth(i).fill(ch)
        else:
            boxes.first.fill(code)
        _wait(page, 1500)
        wait_dom_stable(target, max_s=8, quiet=2)      # the portal checks the code before it re-enables Submit
        loc = walk.find_button(target, submit_desc) if submit_desc else None
        pressed_what = (submit_desc or {}).get("text")
        if loc is None:
            b = next((b for b in walk.buttons(target) if walk.is_submit(b) and not b["disabled"]), None)
            loc = walk.find_button(target, b) if b else None
            pressed_what = b["text"] if b else None
        pressed = loc is not None and walk.click(target, loc)
        it.setdefault("warnings", []).append(
            f"verification code entered; {'pressed ' + repr(pressed_what) if pressed else 'no enabled submit button to press'}")
        return True
    except Exception as e:
        it.setdefault("warnings", []).append(f"verification code entry failed: {str(e)[:80]}")
        return False


def park_after_failure(it, resolve=None):
    """A submit that did not go through decides its own next step — no operator.

    - required fields the form would not take  -> the item goes back to the
      phone as `needs_input`, those fields as questions (label + menu options);
    - no verification code arrived in time     -> stays `approved`, retried
      on the next run;
    - anything else, or the 3rd failed attempt -> `failed`, shown in its own
      section on the review page with the reason and screenshot, where a tap
      on "Retry" re-approves it."""
    max_attempts = cfg("pipeline.submit_attempts", 3)
    warns = it.get("submit_fill", {}).get("warnings", []) + it.get("warnings", [])[-3:]
    if any("verification code not received" in w for w in warns) and it["attempts"] < max_attempts:
        it["status"] = "approved"
        it["fail_reason"] = "no verification code arrived in time — will retry next run"
        return
    missing = it.get("submit_fill", {}).get("missing") or []
    qs, added = questions_from_missing(missing, it.get("questions") or [], resolve=resolve,
                                       warnings=it.setdefault("warnings", []))
    if added and it["attempts"] < max_attempts:
        it["questions"] = qs
        it["status"] = "needs_input"
        it["fail_reason"] = f"{added} required field(s) the form would not take"
        return
    it["status"] = "failed"
    last = next((w for w in reversed(it.get("warnings", []))
                 if w.startswith(("submit did NOT", "submit error", "no submit button"))), "")
    it["fail_reason"] = (f"attempt {it['attempts']}/{max_attempts}: " + (last or "unknown")).strip()[:300]


def notify_outcome(it):
    """One Telegram line per attempt, so nothing needs watching from a terminal."""
    try:
        from jobpilot.core.daily import telegram, form_link
    except Exception:
        return
    link = form_link().replace("/?", f"/a/{it['id']}?")
    head = f"<b>{it.get('company', '?')}</b> · {str(it.get('role', ''))[:60]}"
    st = it.get("status")
    if st == "submitted":
        text = f"✅ <b>Filed</b> — {head}"
    elif st == "needs_input":
        n = len([q for q in it.get("questions", []) if q.get("status") != "answered"])
        text = (f"❓ <b>Needs {n} answer{'s' if n != 1 else ''}</b> — {head}\n"
                f"The form would not take what was stored. Answer + Approve:\n{link}")
    elif st == "expired":
        text = f"🗑 <b>Posting closed</b> — {head}\nThe employer took it down before it could be filed. Nothing was sent."
    elif st == "approved":
        text = f"⏳ <b>Not filed yet</b> — {head}\n{it.get('fail_reason', '')}"
    else:
        text = (f"⚠️ <b>Failed</b> — {head}\n{it.get('fail_reason', '')}\n"
                f"Screenshot + Retry:\n{link}")
    try:
        telegram(text)
    except Exception:
        pass



def resolver_for_item(it, answers):
    """(ctx, resolve) for an approved item. What he answered on the review page
    for THIS application beats every heuristic and even learned.yaml's fuzzy
    match: exact label first."""
    from jobpilot.fill import autofill                                   # circular by design; late import
    ctx = {"market": it["market"], "company": it["company"],
           "company_slug": it["company_slug"], "role": it["role"],
           "portal": it["portal"], "location": it["location"], "url": it["url"]}
    base_resolve, _pay = autofill.build_resolver(answers, ctx)
    # Order: his review-page answers, then replay.json (his answers written
    # there + every recorded non-placeholder value), then the item's fields.
    own = {}
    for qq in it.get("questions") or []:
        if qq.get("status") == "answered" and (qq.get("selected") or "").strip():
            own[norm(qq.get("label", ""))] = qq["selected"]
    for k, v in R.values(it).items():
        if isinstance(v, str) and v.strip():
            own.setdefault(k, v)
    for k, v in (it.get("fields") or {}).items():
        if isinstance(v, str) and v.strip() and norm(k) not in own:
            own.setdefault(norm(k), v)

    def resolve(label, _own=own, _base=base_resolve):
        L = norm(label)
        if L in _own:
            v = _own[L]
            return {"yes": YES, "no": NO}.get(v.strip().lower(), v)
        # Containment only between two LONG labels (the same question, cut at a
        # different length). A short label inside a long question is a different
        # field: "company" sits inside "…worked at Capital One or a company
        # acquired…" and every employer became "No"; "from" did the same to dates.
        if len(L) > 20:
            for k, v in _own.items():
                if len(k) > 20 and (k in L or L in k):
                    return {"yes": YES, "no": NO}.get(v.strip().lower(), v)
        return _base(label)
    return ctx, resolve


def _should_resolve(it):
    """Part 2 runs only after a real failure the agent could plausibly fix."""
    if not cfg("fill.resolve_submit", True):
        return False
    # The live submit session replays through the generic walker. A platform
    # with its own submit driver (Workday: sign-in, a repainting wizard) cannot
    # be reproduced there, and the agent may not sign in — it could only give up.
    if hooks.knowledge(it.get("portal"), it["company_slug"]).custom("submit"):
        return False
    warns = " ".join(it.get("warnings", [])[-4:])
    if "verification code not received" in warns:
        return False                       # waiting on him, not on a fix; retried next run
    return True


def submit_approved(answers, one=None, limit=None):
    """PASS 2 — approved items only, in two parts:
      1. replay   the recorded route + true values + recorded Submit, code only;
      2. resolve  only if part 1 did not go through: a claude -p session drives
                  a live submit session (submit_flow.py) to fix and file it.
    `limit` caps how many are filed this call; the rest stay approved for later."""

    items = []
    for fn in sorted(os.listdir(QUEUE_DIR)):
        if not fn.endswith(".json") or fn.startswith("_"):   # _submission-*.json are form payloads
            continue
        it = json.load(open(os.path.join(QUEUE_DIR, fn)))
        if one and it.get("id") != one:
            continue
        if it.get("status") != "approved" or it.get("submitted_at"):
            continue
        items.append(it)

    if not items:
        print("  nothing approved to submit")
        return
    if limit is not None and limit < len(items):
        print(f"  {len(items)} approved; filing {limit} now, {len(items) - limit} stay approved")
        items = items[:limit]

    for it in items:
        ctx, resolve = resolver_for_item(it, answers)
        print(f"  [submit] {it['id']} — {it['company']} / {it['role']}")
        # The exploration's recipes. Every control that took a placeholder, and
        # every required one, must have a true value NOW — before a browser
        # opens. A placeholder can never be what goes out under his name.
        rep = R.Replay.from_doc(R.load(it))
        gaps = rep.gaps(resolve)
        if gaps:
            it["questions"], added = questions_from_missing(gaps, it.get("questions") or [])
            it["status"] = "needs_input"
            it["fail_reason"] = (f"{len(gaps)} explored field(s) still have no approved answer "
                                 f"— not opened, nothing sent")
            it["last_attempt_at"] = dt.datetime.now().isoformat(timespec="seconds")
            with open(os.path.join(QUEUE_DIR, f"{it['id']}.json"), "w") as f:
                json.dump(it, f, indent=2)
            print(f"    -> needs_input: {[g['label'][:40] for g in gaps][:5]}")
            notify_outcome(it)
            continue
        ctx["replay"] = rep
        try:
            know = hooks.knowledge(it.get("portal"), it["company_slug"])
            custom = know.custom("submit")
            with sync_playwright() as pw:
                br = _browser(pw)
                page = br.new_page()
                page.goto(it["url"], wait_until="domcontentloaded", timeout=60000)
                _wait(page, 4000)
                gone = dead_posting(page)
                if gone:
                    raise SystemExit(f"EXPIRED: posting is closed ({gone!r})")
                res = custom(page, ctx, answers, resolve, rep, it) if custom else None
                if res is None:
                    res = walk.replay(page, ctx, answers, resolve, rep, it)
                # Keep this pass's own fill report: the pass-1 warnings on the
                # item describe a different page load, and debugging a failed
                # submit from them sent the fixes to the wrong place once.
                it["submit_fill"] = {"filled": len(res["filled"]), "warnings": res["warnings"],
                                     "missing": res["missing"], "pages": res.get("pages_done"),
                                     "steps": res.get("steps"),
                                     "replayed": rep.replayed, "discovered": rep.discovered,
                                     "at": dt.datetime.now().isoformat(timespec="seconds")}
                print(f"    filled {len(res['filled'])} fields ({rep.replayed} by recipe, "
                      f"{rep.discovered} discovered) over {res.get('pages_done', 0)} page(s), "
                      f"{len(res['warnings'])} warnings")
                for w in res["warnings"]:
                    print(f"      ! {w[:160]}")
                clicked, submit_ok, errs = res.get("clicked", False), res.get("submit_ok", False), res.get("errs", 0)
                shot = os.path.join(DATA, "queue", f"{it['id']}-submitted.png")
                try:
                    page.screenshot(path=shot, full_page=True)
                    it["submit_screenshot"] = os.path.relpath(shot, TOOL)
                except Exception:
                    pass
                br.close()
            it["status"] = "submitted" if submit_ok else "failed"
            if not clicked:
                it.setdefault("warnings", []).append(
                    "no submit button matched" if res.get("reached_end")
                    else "did not reach the last page — " + (res["warnings"][-1][:120] if res["warnings"] else "no page advanced"))
            elif not submit_ok:
                it.setdefault("warnings", []).append(
                    f"submit did NOT go through — form still present"
                    f"{f' with {errs} invalid field(s)' if errs else ''}. "
                    f"Check the screenshot; required fields are likely empty.")
        except SystemExit as e:
            # A closed posting: nothing to file and nothing to fix, ever.
            it["status"] = "expired"
            it["fail_reason"] = str(e)[:300]
            it.setdefault("warnings", []).append(str(e)[:300])
            with open(os.path.join(QUEUE_DIR, f"{it['id']}.json"), "w") as f:
                json.dump(it, f, indent=2)
            print(f"    -> expired: {str(e)[:120]}")
            notify_outcome(it)
            continue
        except Exception as e:
            it["status"] = "failed"
            it.setdefault("warnings", []).append(f"submit error: {e}")

        if it["status"] == "submitted":
            R.record_submit_procedure(it, {
                "resolved_by": "code", "pressed": it.get("_pressed") or rep.submit,
                "verification_code": any("verification code entered" in w for w in it.get("warnings", []))})
        elif _should_resolve(it):
            # Part 2. The browser above is closed, so the Chrome profile is free
            # for the live session; the item on disk is what the session loads.
            it["fail_reason"] = next((w for w in reversed(it.get("warnings", []))
                                      if w.startswith(("submit did NOT", "submit error", "no submit", "did not reach"))),
                                     "submit did not go through")[:300]
            it.pop("_pressed", None)
            with open(os.path.join(QUEUE_DIR, f"{it['id']}.json"), "w") as f:
                json.dump(it, f, indent=2)
            print("    part 1 did not go through — handing to a claude resolve session")
            from jobpilot.fill import submit_resolve
            it = submit_resolve.resolve(it)
            print(f"    resolve -> {it.get('status')}")
        it.pop("_pressed", None)

        # submitted_at is the "this is final" marker (409 on the form, skipped
        # here). A failed attempt used to set it too, which made a failure
        # impossible to retry. Failures now only record when they were tried.
        now = dt.datetime.now().isoformat(timespec="seconds")
        if it["status"] == "submitted":
            it.setdefault("submitted_at", now)
        else:
            it["last_attempt_at"] = now
            it["attempts"] = (it.get("attempts") or 0) + 1
            park_after_failure(it, resolve)
        with open(os.path.join(QUEUE_DIR, f"{it['id']}.json"), "w") as f:
            json.dump(it, f, indent=2)
        rep.note_attempt(it, it.get("fail_reason") or it["status"])
        print(f"    -> {it['status']}")
        notify_outcome(it)

        # An application sitting in an ATS queue is the weakest form of applying.
        # On a real submit, immediately line up people to ask for a referral.
        if it["status"] == "submitted":
            try:
                import subprocess as _sp
                _sp.run([sys.executable, "-m", "jobpilot.outreach.referral_tracker",
                         "--for", it["company_slug"]], cwd=TOOL, timeout=300)
            except Exception as e:
                print(f"    [warn] referral targets: {type(e).__name__}")
