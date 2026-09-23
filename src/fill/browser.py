#!/usr/bin/env python3
"""
browser.py — the Playwright half of Phase 2. Imported by autofill.py.

Runs HEADED real Chrome against a persistent profile (~/.config/jobbot/chrome-profile)
so sessions, cookies and human-looking fingerprints persist. Never headless: portals
treat headless traffic differently, and you want to be able to watch it work.

fill_application()  — pass 1. Fills, screenshots, queues as PENDING. Cannot submit:
                      there is no submit call on this path at all.
submit_approved()   — pass 2. Only touches queue items the phone marked APPROVED.
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
PROFILE_DIR = os.path.expanduser("~/.config/jobbot/chrome-profile")

YES, NO = "__YES__", "__NO__"


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[*\u2217]", "", s or "")).strip().lower()

# Tag every control and read its label the way a human would: explicit <label>,
# then ARIA, then the nearest preceding text, then placeholder/name.
EXTRACT_JS = r"""
() => {
  const out = [];
  const ctrls = document.querySelectorAll('input, textarea, select');
  let i = 0;
  for (const el of ctrls) {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) continue;
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') continue;

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
    const GENERIC = /^(select\.{0,3}|choose\.{0,3}|please select|--+|none|n\/a)$/i;
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
      if (!g) {
        let n = el.parentElement, hops = 0;
        while (n && hops < 4 && !g) {
          const first = (n.innerText || '').split('\n')[0].trim();
          if (first && first.length > 8 && first.length < 200 &&
              !/^(yes|no)$/i.test(first)) g = first;
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
    const st = window.getComputedStyle(b);
    if (st.display === 'none' || st.visibility === 'hidden') continue;
    const box = b.closest('fieldset,[role="group"],[role="radiogroup"]') || b.parentElement;
    if (!box) continue;
    if (!groups.has(box)) groups.set(box, []);
    groups.get(box).push(b);
  }
  for (const [box, btns] of groups) {
    if (btns.length < 2) continue;
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
  return out;
}
"""


# Companies that host Greenhouse/Lever/Ashby on their own domain embed the real
# application form in an iframe. Querying only the top-level document finds the
# site nav and no form at all, so locate the form frame first.
ATS_FRAME_PATTERNS = (
    "greenhouse.io/embed/job_app", "job-boards.greenhouse.io",
    "boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
    "ashbyhq.com/embed", "smartrecruiters.com",
)


def count_controls(frame):
    try:
        return frame.evaluate(
            "() => document.querySelectorAll('input:not([type=hidden]),select,textarea').length")
    except Exception:
        return 0


def pick_form_frame(page, timeout_ms=None, poll_ms=500):
    timeout_ms = timeout_ms or cfg("browser.form_frame_timeout_ms", 20000)
    """Return the frame holding the application form, and how it was chosen.

    Embedded ATS iframes attach late and at variable speed, so POLL for one
    rather than sleeping a fixed amount — a fixed wait silently falls back to the
    site nav and fills nothing.
    """
    waited = 0
    while waited < timeout_ms:
        for f in page.frames:
            if any(pat in (f.url or "") for pat in ATS_FRAME_PATTERNS):
                n = count_controls(f)
                if n >= 3:
                    return f, f"ATS iframe ({n} controls) after {waited}ms"
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
    d_words = set(re.findall(r"\w+", d))              # word overlap
    best, best_score = None, 0
    for i, o in enumerate(low):
        score = len(d_words & set(re.findall(r"\w+", o)))
        if score > best_score:
            best, best_score = opts[i], score
    return best if best_score else None


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
        return by_key.get(_key(x)) or (by_id.get(x["id"]) if x.get("id") else None)

    for x in (everyone or []):
        g = match(x)
        if g:
            x["idx"] = g["idx"]
    return match(f)


def fill_fields(page, resolve, answers, ctx):
    """Fill everything mappable. `page` may be a Page or a Frame."""
    fields = page.evaluate(EXTRACT_JS)
    filled, warnings = {}, []
    radio_groups = {}

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
                continue
        try:
            page.set_input_files(f'[data-jobbot-idx="{f["idx"]}"]', rp)
            filled[f["label"] or "Resume"] = os.path.basename(rp)
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
    for f in fields:
        sel = f'[data-jobbot-idx="{f["idx"]}"]'
        label = f["label"] or f["name"]
        ftype = f["type"]

        if ftype == "file":
            continue                            # handled in phase 1
        if ftype == "search" and not f["required"]:
            continue                            # a widget's own filter box (phone country picker)

        if ftype in ("radio", "checkbox", "buttongroup"):
            radio_groups.setdefault(f["name"] or f.get("group_label") or label,
                                    []).append(f)
            continue

        # A re-render between two fields drops every tag; find this one again
        # by what it IS rather than giving up on the rest of the form.
        if not _alive(page, f["idx"]):
            g = _refind(page, f, everyone=fields)
            if not g:
                warnings.append(f"'{label[:60]}' vanished after a re-render — not filled")
                continue
            sel = f'[data-jobbot-idx="{f["idx"]}"]'

        val = resolve(label)
        if val is None:
            if f["required"] and not f["value"]:
                warnings.append(f"REQUIRED and unmapped: '{label}'")
            continue

        try:
            if ftype == "select-one" or ftype == "select":
                choice = pick_option(f.get("options", []), val)
                if choice:
                    page.select_option(sel, label=choice)
                    filled[label] = choice
                else:
                    warnings.append(f"no option matched on '{label}' "
                                    f"(wanted {val!r}, options: {f.get('options', [])[:6]})")
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
                if str(text).strip():
                    ok, how = fill_verified(page, f["idx"], text, is_date,
                                            combobox=bool(f.get("combobox")),
                                            rs=bool(f.get("rs")),
                                            textarea=f.get("tag") == "textarea",
                                            stable_id=f.get("id") or "")
                    if ok:
                        filled[label] = str(text)
                    else:
                        warnings.append(
                            f"NOT FILLED{' (REQUIRED)' if f['required'] else ''}: "
                            f"'{label[:60]}' — {how}. Needs a manual entry.")
                    time.sleep(0.08)
        except Exception as e:
            warnings.append(f"could not fill '{label}': {e}")

    # Radios: decide once per group, then click the option whose own label matches.
    for gname, opts in radio_groups.items():
        # Prefer the group question (legend) over an individual option's label.
        question = next((o.get("group_label") for o in opts if o.get("group_label")), "")
        if not question:
            question = next((o["label"] for o in opts if o["label"]), gname)
        val = resolve(question)
        if val is None:
            # The identifying words are often in the OPTIONS, not the heading.
            val = resolve(question + " " + " ".join(o["label"] for o in opts))
        if val is None:
            # A radio group left blank is visible on the form, so always warn —
            # required or not. Silent skips are how a half-filled form gets sent.
            req = " REQUIRED" if any(o["required"] for o in opts) else ""
            warnings.append(f"radio group unmapped{req}: '{question}'")
            continue
        # A single-option checkbox group is an acknowledgement: YES means tick
        # the one box, whose label is the statement rather than the word "yes".
        if (val is YES or val == YES) and len(opts) == 1:
            ok, how = robust_check(page, opts[0]["idx"])
            if ok:
                filled[question] = opts[0]["label"] or "confirmed"
            else:
                warnings.append(f"COULD NOT TICK '{question[:60]}' ({how})")
            continue
        want = {YES: "yes", NO: "no"}.get(val, str(val).lower())
        # A stored answer is the option's own text (possibly with a trailing
        # note like "— this one only"), so match on the leading phrase.
        want_core = re.split(r"\s+[—-]{1,2}\s+", want)[0].strip()
        for o in opts:
            ol = (o["label"] or "").lower()
            if (want in ol or ol in want or want_core in ol
                    or (want_core and ol.startswith(want_core[:40]))
                    or want == (o["value"] or "").lower()):
                if not _alive(page, o["idx"]):
                    g = _refind(page, o, everyone=[x for grp in radio_groups.values() for x in grp])
                    if not g:
                        warnings.append(f"'{question[:60]}' vanished after a re-render — not ticked")
                        break
                if o["type"] == "buttongroup":
                    ok, how = click_choice(page, o["idx"])
                else:
                    ok, how = robust_check(page, o["idx"])
                if ok:
                    filled[question] = o["label"] or want
                else:
                    warnings.append(
                        f"COULD NOT TICK '{o['label'][:60]}' for "
                        f"'{question[:60]}' ({how}) — needs a manual click")
                break
        else:
            warnings.append(f"no radio matched '{question}' (wanted {want!r})")

    return filled, warnings


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
        qs.append({
            "qid": len(qs), "label": label[:400],
            "kind": "choice" if f["type"].startswith("select") else "text",
            "required": bool(f["required"]), "form_group": f["name"],
            "form_options": f.get("options") or [],
            "options": (f.get("options") or [])[:6],
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
    # passes. So a combobox always goes straight to real typing.
    if not combobox:
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
    try:
        click_field(frame, sel)
        frame.eval_on_selector(sel, "el => { el.value=''; }")
        frame.type(sel, str(text), delay=45)
        frame.wait_for_timeout(1000)
        if is_date:
            frame.press(sel, "Enter")
        else:
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

    try:
        click_field(frame, sel)
        frame.wait_for_timeout(500)
        # A Yes/No dropdown given a sentence that does not start with yes/no is
        # a question nobody has answered yet. Never guess it.
        menu = {t for _, t in visible_options()}
        if menu and menu <= {"yes", "no", "n/a", "not applicable", "prefer not to say"} and not m:
            frame.press(sel, "Escape")
            return False, "Yes/No dropdown, stored answer is not a yes/no — needs your answer"
        for want in order:
            frame.press(sel, "Control+A")
            frame.press(sel, "Backspace")
            frame.type(sel, want[:60], delay=40)
            frame.wait_for_timeout(800)
            vis = visible_options()
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
        frame.press(sel, "Control+A")
        frame.press(sel, "Backspace")
        frame.press(sel, "Escape")
        return False, f"react-select: no option matched {order[-1][:30]!r}"
    except Exception as e:
        return False, str(e)[:60]


def click_choice(frame, idx):
    """Click a <button>-rendered choice and confirm it took."""
    sel = f'[data-jobbot-idx="{idx}"]'
    try:
        how = click_field(frame, sel)
        return True, how
    except Exception as e:
        return False, str(e)[:60]


def robust_check(frame, idx):
    """Tick a checkbox/radio that a styled overlay intercepts clicks for.

    Greenhouse (and most modern ATS) visually hide the real input and render a
    styled box on top, so Playwright's click lands on the overlay and check()
    reports the state never changed. Escalate: native check, then click the
    associated <label>, then set it directly and fire the events a framework
    listens for.
    """
    sel = f'[data-jobbot-idx="{idx}"]'
    try:
        frame.check(sel, timeout=3000)
        return True, "check"
    except Exception:
        pass
    try:
        frame.eval_on_selector(sel, """el => {
            const l = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
                            : el.closest('label');
            if (l) l.click();
        }""")
        if frame.eval_on_selector(sel, "el => el.checked"):
            return True, "label-click"
    except Exception:
        pass
    # A styled overlay that swallows the normal click still takes a forced one.
    try:
        frame.eval_on_selector(sel, "el => el.scrollIntoView({block: 'center'})")
        frame.click(sel, timeout=3000, force=True)
        if frame.eval_on_selector(sel, "el => !!(el.checked || el.getAttribute('aria-checked') === 'true')"):
            return True, "force-click"
    except Exception:
        pass
    try:
        frame.eval_on_selector(sel, """el => {
            if (!(el instanceof HTMLInputElement)) { el.click(); return; }
            const set = Object.getOwnPropertyDescriptor(
                HTMLInputElement.prototype, 'checked').set;
            set.call(el, true);
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            el.dispatchEvent(new Event('click', {bubbles: true}));
        }""")
        if frame.eval_on_selector(sel, "el => el.checked"):
            return True, "js-set"
    except Exception as e:
        return False, str(e)[:80]
    return False, "state unchanged after all strategies"


def _browser(pw):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        PROFILE_DIR, channel="chrome", headless=cfg("browser.headless", False),
        viewport={"width": 1280, "height": 1600},
        args=["--disable-blink-features=AutomationControlled"])


def fill_application(ctx, answers, resolve, pay, submit=False):
    """PASS 1. Fills and screenshots. Contains no submit path whatsoever."""
    assert submit is False, "fill_application never submits; use submit_approved()"
    os.makedirs(QUEUE_DIR, exist_ok=True)
    item_id = f"{ctx['company_slug']}-{dt.datetime.now():%m%d%H%M}"
    shot_rel = os.path.join("data", "queue", f"{item_id}.png")   # relative to TOOL
    shot_abs = os.path.join(TOOL, shot_rel)

    with sync_playwright() as pw:
        br = _browser(pw)
        page = br.new_page()
        print(f"  [browser] opening {ctx['url']}")
        page.goto(ctx["url"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)          # let embedded ATS iframes load

        target, how = pick_form_frame(page)
        print(f"  [form] {how}")
        # An uploaded resume triggers the portal's own autofill, which overwrites
        # fields mid-run. Wait for that to finish before touching anything.
        for _ in range(20):
            try:
                body = (target.inner_text("body") or "").lower()
            except Exception:
                break
            if "parsing your resume" not in body and "autofilling" not in body:
                break
            page.wait_for_timeout(1000)
        filled, warnings = fill_fields(target, resolve, answers, ctx)
        if not filled:
            warnings.append("no fields filled — form frame may not have rendered")
        questions = harvest_questions(target, resolve, warnings)
        page.wait_for_timeout(500)
        try:
            page.screenshot(path=shot_abs, full_page=True)
        except Exception as e:
            warnings.append(f"screenshot failed: {e}")
            shot_rel = None
        print(f"  [fill] {len(filled)} fields, {len(warnings)} warnings")
        for w in warnings:
            print(f"    ! {w}")
        br.close()

    # Re-filling must not throw away work already done on this application:
    # drafted answer options cost a Claude session each, and answers Sunil already
    # picked are his. Carry both across from the most recent unsubmitted item.
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
        "questions": questions,
        "status": "needs_input" if questions else "pending",
        "created": dt.datetime.now().isoformat(timespec="seconds"),
    }
    with open(os.path.join(QUEUE_DIR, f"{item_id}.json"), "w") as f:
        json.dump(item, f, indent=2)
    print(f"  [queue] {item_id} -> pending. Run: python jobs/bot.py --serve")
    return item


SUBMIT_SELECTORS = [
    'button:has-text("Submit application")', 'button:has-text("Submit Application")',
    'button:has-text("Submit")', 'input[type="submit"]',
    'button[type="submit"]', 'button:has-text("Apply")',
]


CODE_INPUTS = ('input[autocomplete="one-time-code"], input[maxlength="1"], '
               'input[name*="code" i], input[id*="code" i], input[aria-label*="code" i], '
               'input[aria-label*="digit" i], input[aria-label*="character" i]')


def enter_verification_code(page, target, it):
    """If the portal is waiting on an emailed code, get it from Sunil and enter it.
    Returns True when a code was typed (the caller re-checks the outcome)."""
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
        page.wait_for_timeout(1500)
        for sel in SUBMIT_SELECTORS:
            try:
                btn = target.locator(sel).first
                if btn.count() and btn.is_visible(timeout=800) and btn.is_enabled():
                    btn.click(timeout=5000)
                    break
            except Exception:
                continue
        it.setdefault("warnings", []).append("verification code entered")
        return True
    except Exception as e:
        it.setdefault("warnings", []).append(f"verification code entry failed: {str(e)[:80]}")
        return False


def submit_approved(answers, one=None, limit=None):
    """PASS 2. Re-fill deterministically, then submit — approved items only.
    `limit` caps how many are filed this call; the rest stay approved for later."""
    from jobpilot.fill import autofill                                   # circular by design; late import

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
        ctx = {"market": it["market"], "company": it["company"],
               "company_slug": it["company_slug"], "role": it["role"],
               "portal": it["portal"], "location": it["location"], "url": it["url"]}
        base_resolve, pay = autofill.build_resolver(answers, ctx)
        # What he answered on the review page for THIS application beats every
        # heuristic and even learned.yaml's fuzzy match: exact label first.
        own = {}
        for qq in it.get("questions") or []:
            if qq.get("status") == "answered" and (qq.get("selected") or "").strip():
                own[norm(qq.get("label", ""))] = qq["selected"]
        for k, v in (it.get("fields") or {}).items():
            if isinstance(v, str) and v.strip() and norm(k) not in own:
                own.setdefault(norm(k), v)

        def resolve(label, _own=own, _base=base_resolve):
            L = norm(label)
            if L in _own:
                v = _own[L]
                return {"yes": YES, "no": NO}.get(v.strip().lower(), v)
            for k, v in _own.items():
                if len(k) > 20 and (k in L or L in k):
                    return {"yes": YES, "no": NO}.get(v.strip().lower(), v)
            return _base(label)
        print(f"  [submit] {it['id']} — {it['company']} / {it['role']}")
        try:
            with sync_playwright() as pw:
                br = _browser(pw)
                page = br.new_page()
                page.goto(it["url"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)
                target, _ = pick_form_frame(page)
                # Keep this pass's own fill report: the pass-1 warnings on the
                # item describe a different page load, and debugging a failed
                # submit from them sent the fixes to the wrong place once.
                s_filled, s_warn = fill_fields(target, resolve, answers, ctx)
                it["submit_fill"] = {"filled": len(s_filled), "warnings": s_warn,
                                     "at": dt.datetime.now().isoformat(timespec="seconds")}
                print(f"    filled {len(s_filled)} fields, {len(s_warn)} warnings")
                for w in s_warn:
                    print(f"      ! {w[:160]}")
                page.wait_for_timeout(800)

                clicked = False
                for sel in SUBMIT_SELECTORS:
                    try:
                        btn = target.locator(sel).first
                        if not btn.count() or not btn.is_visible(timeout=1500):
                            continue
                        btn.scroll_into_view_if_needed(timeout=3000)
                        page.wait_for_timeout(400)
                        try:
                            btn.click(timeout=5000)
                        except Exception:
                            btn.click(timeout=5000, force=True)
                        clicked = True
                        break
                    except Exception:
                        continue
                # Greenhouse may now hold the submission behind an emailed
                # 8-character code. Ask him for it and type it in.
                page.wait_for_timeout(2500)
                if enter_verification_code(page, target, it):
                    clicked = True
                # Submission can take a while — poll for the outcome instead of
                # screenshotting a page that is still mid-request.
                for _ in range(12):
                    page.wait_for_timeout(1000)
                    try:
                        b = (target.inner_text("body") or "").lower()
                    except Exception:
                        break
                    if any(w in b for w in ("thank you for applying", "application received",
                                            "application submitted", "we have received",
                                            "thanks for applying", "successfully submitted")):
                        break

                # Did it actually go through? A visible submit button or a
                # validation error means it did not.
                still_there = False
                try:
                    for sel in SUBMIT_SELECTORS:
                        loc = target.locator(sel).first
                        if loc.count() and loc.is_visible(timeout=800):
                            still_there = True
                            break
                except Exception:
                    pass
                errs = 0
                try:
                    errs = target.locator(
                        '[aria-invalid="true"], .error:visible, [role="alert"]').count()
                except Exception:
                    pass
                confirmed = False
                try:
                    body = (target.inner_text("body") or "").lower()
                    confirmed = any(w in body for w in (
                        "thank you for applying", "application received",
                        "application submitted", "we have received",
                        "thanks for applying", "successfully submitted"))
                except Exception:
                    pass
                submit_ok = confirmed or (clicked and not still_there and errs == 0)

                shot = os.path.join(DATA, "queue", f"{it['id']}-submitted.png")
                try:
                    page.screenshot(path=shot, full_page=True)
                    it["submit_screenshot"] = os.path.relpath(shot, TOOL)
                except Exception:
                    pass
                br.close()
            it["status"] = "submitted" if submit_ok else "failed"
            if not clicked:
                it.setdefault("warnings", []).append("no submit button matched")
            elif not submit_ok:
                it.setdefault("warnings", []).append(
                    f"submit did NOT go through — form still present"
                    f"{f' with {errs} invalid field(s)' if errs else ''}. "
                    f"Check the screenshot; required fields are likely empty.")
        except Exception as e:
            it["status"] = "failed"
            it.setdefault("warnings", []).append(f"submit error: {e}")

        # submitted_at is the "this is final" marker (409 on the form, skipped
        # here). A failed attempt used to set it too, which made a failure
        # impossible to retry. Failures now only record when they were tried.
        now = dt.datetime.now().isoformat(timespec="seconds")
        if it["status"] == "submitted":
            it["submitted_at"] = now
        else:
            it["last_attempt_at"] = now
            it["attempts"] = (it.get("attempts") or 0) + 1
        with open(os.path.join(QUEUE_DIR, f"{it['id']}.json"), "w") as f:
            json.dump(it, f, indent=2)
        print(f"    -> {it['status']}")

        # An application sitting in an ATS queue is the weakest form of applying.
        # On a real submit, immediately line up people to ask for a referral.
        if it["status"] == "submitted":
            try:
                import subprocess as _sp
                _sp.run([sys.executable, "-m", "jobpilot.outreach.referral_tracker",
                         "--for", it["company_slug"]], cwd=TOOL, timeout=300)
            except Exception as e:
                print(f"    [warn] referral targets: {type(e).__name__}")
