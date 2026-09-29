"""act.py — the agent's rows done on the page, control by control, each read back.

    act_rows(frame, controls, rows, facts, resume) -> [outcome]     (form.act, the act tool)

A row is [id, write|select, answer]; the control is the one see read (see.describe), found
by the pin see put on it. An answer:
    <fact key>                     the fact's value
    option:<choice>                a choice the control offers
    option:<a> › <b> › <c>         a CHAIN in a nested list: opened once, each step picked
                                   in turn, down to the entry that opens nothing more
    guess:<chain 1>; <chain 2>; ... | <question> [| for:<fact key>]
                                   not sure: chain 1 is picked as a PLACEHOLDER so the form
                                   can go on; the question and every candidate are kept to
                                   ask the applicant. for: the stored fact the list could not
                                   hold: his pick is a stand-in for this form, never learned
    search:<regex>; <regex>        the agent's patterns, most specific first (\bIndia\b, ^I):
                                   each run over the list's entries (a nested list's chains);
                                   what each matches is reported, nothing picked
    file:resume                    the resume file
    add:<n>                        a repeated section's Add, pressed n times, each checked
    <the question itself>          nothing stored: a required box gets a placeholder text
    text:<value>                   a literal value — the applicant's approved answer, at filing
    keep:<any answer above>        the control already shows the right value: nothing is done

Which routine acts on a control comes from what its HTML says (see.facts_of): a box is
typed into, a date part keyed digit by digit, a <select> selected, a radio / checkbox
ticked, a choice button pressed, a file input given the file, a list opened and walked.
No name of a control is matched, no pattern read: the control is the one see pinned.
"""
import os
import re
import time

from jobpilot.apply.explore_agentic import see as S

WAIT = 5000
PLACEHOLDER_TEXT = "To be confirmed"


class NotFound(Exception):
    pass


# ---------------------------------------------------------------- reading an answer

def chain_of(text):
    """'Social Media › LinkedIn' -> ['Social Media', 'LinkedIn']."""
    return [p.strip() for p in str(text).split("›") if p.strip()]


def read_answer(answer, facts, resume):
    """(how, what) for one answer:
        ("fact", (key, value))  ("chain", [steps])  ("guess", {"candidates": [chains], "question"})
        ("search", [terms])     ("file", path)      ("add", n)          ("keep", (how, what))
        ("question", text)"""
    a = str(answer or "").strip()
    low = a.lower()
    if low.startswith("keep:"):
        return "keep", read_answer(a[5:], facts, resume)
    if low.startswith("text:"):                          # a literal value (an approved answer, at replay)
        return "fact", ("approved", a[5:].strip())
    if low.startswith("option:"):
        return "chain", chain_of(a[7:])
    if low.startswith("guess:"):
        body, _, rest = a[6:].partition("|")
        question, _, tail = rest.partition("|")
        tail = tail.strip()
        cands = [chain_of(x) for x in body.split(";") if chain_of(x)]
        return "guess", {"candidates": cands[:5], "question": question.strip(),
                         "for": tail[4:].strip() if tail.lower().startswith("for:") else ""}
    if low.startswith("search:"):
        return "search", [t.strip() for t in a[7:].split(";") if t.strip()][:3]
    if low == "file:resume":
        return "file", resume
    if low.startswith("add:"):
        n = "".join(ch for ch in a[4:] if ch.isdigit())
        return "add", int(n or 0)
    if a in facts:
        return "fact", (a, facts[a])
    return "question", a


# ---------------------------------------------------------------- the control

def nature(c):
    """How a control is acted on, from its HTML facts: type / digits / select / tick /
    press / file / list."""
    f = c.facts or {}
    tag, typ, role = f.get("tag"), f.get("type"), f.get("role") or c.role
    if role in ("radio", "checkbox", "switch") or typ in ("radio", "checkbox"):
        return "tick"
    if tag == "select":
        return "select"
    if typ == "file" or f.get("file_near"):
        return "file"
    if S.has_list(f, c):
        return "list"
    if role == "spinbutton" or typ == "number" or f.get("valuetext"):
        return "digits"
    if tag in ("input", "textarea") or role in ("textbox", "searchbox"):
        return "type"
    return "press"


def locate(frame, c):
    """The control see read: by the pin see put on it, else its DOM id (read when acting
    began), else found again by its role, exact name and number."""
    q = lambda v: str(v).replace("\\", "\\\\").replace('"', '\\"')
    for css in ([f'[data-jp="{q(c.ref)}"]'] if c.ref else []) + ([f'[id="{q(c.dom_id)}"]'] if getattr(c, "dom_id", "") else []):
        loc = frame.locator(css)
        if loc.count() == 1:
            return loc.first
    el = S.element(frame, c)
    if el is None:
        raise NotFound(f"{c.role} {c.ref!r} is no longer on the page")
    return el


def plain(s):
    """Letters and digits only, lower case: 'India (+91)' ~ 'india91', '0964-12 98471' ~ '09641298471'."""
    return "".join(ch.lower() for ch in str(s or "") if ch.isalnum())


# ---------------------------------------------------------------- techniques, tried in turn

# The same kind of control takes a different technique on different sites (Workday redraws a
# box while it is typed; Lever's location box wants a key press; Ashby's styled radio wants a
# real click). Each routine lists its techniques; attempt() tries them in turn until the check
# says the control holds what was wanted. The one that worked is remembered per platform and
# tried first next time (data/techniques.json).
PLATFORM = ""                                    # set by form.Form for its application
PREFER = None                                    # the technique the agent named for this row (act_row)
_LEARNED = None


def _learned():
    global _LEARNED
    if _LEARNED is None:
        import json
        from jobpilot.core.paths import DATA
        try:
            _LEARNED = json.load(open(os.path.join(DATA, "techniques.json")))
        except (OSError, ValueError):
            _LEARNED = {}
    return _LEARNED


def _remember(kind, name):
    import json
    from jobpilot.core.paths import DATA
    got = _learned().setdefault(PLATFORM or "any", {})
    if got.get(kind) != name:
        got[kind] = name
        try:
            json.dump(_LEARNED, open(os.path.join(DATA, "techniques.json"), "w"), indent=1)
        except OSError:
            pass


def attempt(kind, techniques, check):
    """Each (name, fn) of `techniques` in turn — the one that worked last time on this platform
    first — until check() is true: a technique that raises, or leaves the check false, gives way
    to the next. (ok, the technique that worked, [what the others hit])."""
    first = (_learned().get(PLATFORM or "any") or {}).get(kind)
    order = sorted(techniques, key=lambda t: (t[0] != PREFER, t[0] != first))
    tried = []
    for name, fn in order:
        try:
            fn()
            if check():
                if name != order[0][0] or first is None:
                    _remember(kind, name)
                return True, name, tried
            tried.append(f"{name}: did not take")
        except Exception as e:
            tried.append(f"{name}: {type(e).__name__}")
    return False, None, tried


def js_set(el, value):
    """Set a box's value the way a framework hears it: the native setter, then input / change."""
    el.evaluate("""(el, v) => {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
    }""", str(value))


# what the control shows now: its value, the choice a <select> or list button displays,
# and the picks its field holds (a list inside the field's own box — read with every
# dropdown closed, so an open list is never taken for picks)
SHOWS_JS = r"""el => {
  const CTRL = 'input:not([type="hidden"]), textarea, select, button, [role="combobox"], [role="textbox"], [role="button"]';
  let box = el;
  while (box.parentElement && ![...box.parentElement.querySelectorAll(CTRL)].some(x => x !== el && !el.contains(x))) box = box.parentElement;

  // the text drawn beside an empty box, in its own field: the nearest box around it that
  // holds text, no label / legend and no other control — a select widget draws its chosen
  // value there, beside the (empty) input
  const drawn = e => {
    let n = e;
    for (let i = 0; i < 4 && n.parentElement; i++) {
      n = n.parentElement;
      if (n.querySelector('label, legend')) return '';
      if ([...n.querySelectorAll('input:not([type="hidden"]), textarea, select')].some(x => x !== e)) return '';
      const t = (n.innerText || '').replace(/\s+/g, ' ').trim();
      if (t) return t.slice(0, 300);
    }
    return '';
  };
  const out = [];
  if (el.tagName === 'SELECT') out.push(el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : '');
  else if (el.tagName === 'INPUT' && !el.value) out.push(drawn(el));
  else if ('value' in el && el.tagName !== 'BUTTON') out.push(el.value || '');
  else out.push((el.innerText || '').trim());
  for (const o of box.querySelectorAll('[role="listbox"] [role="option"]')) out.push((o.innerText || '').trim());
  return out.filter(Boolean);
}"""


def shows(frame, c):
    try:
        return locate(frame, c).evaluate(SHOWS_JS) or []
    except Exception:
        return []


STATE_JS = r"""el => {
  const t = el.tagName, type = (el.type || '').toLowerCase();
  if (t === 'INPUT' && (type === 'checkbox' || type === 'radio')) return el.checked ? 'on' : 'off';
  if (t === 'SELECT') return el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : '';
  const s = el.getAttribute('aria-pressed') || el.getAttribute('aria-checked');
  if (s === 'true' || s === 'false') return s === 'true' ? 'on' : 'off';
  if (t === 'INPUT' || t === 'TEXTAREA') return el.value || '';
  return null;
}"""


def current(frame, c):
    """What a control holds now, read from its field the same way every time — for a record
    and, later, its check: a tick's or a choice button's state, a box's value, a select's
    choice, else what its field draws."""
    try:
        got = locate(frame, c).evaluate(STATE_JS)
    except Exception:
        return None
    return got if got is not None else "; ".join(shows(frame, c))


def holds(frame, c, entry):
    """Does the control now show exactly the entry that was picked (case, spaces and
    punctuation aside)? Whether a shown value is the RIGHT answer is the agent's to judge —
    it answers keep: — never code."""
    want = plain(entry)
    return bool(want) and any(want == plain(s) for s in shows(frame, c))


# ---------------------------------------------------------------- the routines

def focus(el):
    """Put the cursor in a box: a click when nothing covers it, else focus — never a wait
    for a cover (a drawn date part, a pill) to go away."""
    if S.lands(el):
        try:
            el.click(timeout=1500)
            return
        except Exception:
            pass
    el.focus()


def type_into(frame, el, value, c=None):
    """A text box, by the first technique that makes it hold the value once it is left: set in
    one step (a box that redraws while typed — Workday's — keeps up), typed key by key (a
    type-ahead that reacts to key presses — Lever's location), set by script with input /
    change events. After each, a suggestion the box offers that is the value (or starts with
    it) is picked. The box is found again (by `c`) before every read: a redrawn box is never
    waited on. A box that drops the value when left is reported, not taken as done."""
    text, picked = str(value), None
    box = (lambda: locate(frame, c)) if c is not None else (lambda: el)
    short = len(text) <= 80

    def now_value():
        try:
            return box().input_value(timeout=2000)
        except Exception:
            return ""

    def pick(before):
        nonlocal picked
        if before is None:
            return
        want = plain(text)
        got = suggestions_after(frame, box(), before, want)
        best = next((x for x in got if plain(x[0]) == want), None) or \
            next((x for x in got if plain(x[0]).startswith(want)), None)
        if best:
            best[1].click(timeout=WAIT)
            picked = best[0]
        elif got:
            S._close(frame)

    def leave():
        try:
            box().blur(timeout=2000)
            frame.wait_for_timeout(250)
        except Exception:
            pass

    def by(how):
        def run():
            b = box()
            focus(b)
            before = offered_now(frame, b) if short else None
            if how == "keys":
                b.fill("", timeout=WAIT)
                b.press_sequentially(text, delay=15)
            elif how == "js":
                js_set(b, text)
            else:
                b.fill(text, timeout=WAIT)
            pick(before)
            leave()
        return run

    holds_it = lambda: plain(now_value()) in {plain(text)} | ({plain(picked)} if picked else set())
    techniques = [("fill", by("fill")), ("keys", by("keys")), ("js", by("js"))] if short else \
        [("fill", by("fill")), ("js", by("js"))]
    ok, how, tried = attempt("type", techniques, holds_it)
    got = now_value()
    if not ok and not got.strip():
        return {"ok": False, "shown": got, "tried": tried, "error": f"the box would not keep {text!r} — it may want "
                                                                    "an entry picked from its suggestions: search it"}
    return {"ok": ok, "shown": got, "technique": how, **({"tried": tried} if not ok else {})}


def offered_now(frame, el):
    """What is on offer before typing: the open ARIA list's entries, the field's own texts."""
    return ({e[0] for e in (frame.evaluate(S.ENTRIES_JS) or {}).get("entries", [])}, set(S.field_texts(el)))


def suggestions_after(frame, el, before, want, wait_s=2.5):
    """The suggestions typing brought: entries of an ARIA list that opened, else texts that
    newly appeared in the box's own field (a type-ahead drawn as plain elements). Read until
    one is the wanted value or starts with it ("Loading" first, results after); nothing new
    within 0.8s: none. [(text, locator to click)]."""
    listed, drawn = before

    def now():
        aria = [e[0] for e in (frame.evaluate(S.ENTRIES_JS) or {}).get("entries", []) if e[0] not in listed]
        if aria:
            return [(t, _entry(frame, t).first) for t in aria]
        return [(t, S.suggestion(frame, i)) for i, t in enumerate(S.field_texts(el)) if t not in drawn]

    got, t0 = [], time.time()
    while time.time() - t0 < wait_s:
        frame.wait_for_timeout(200)
        got = now()
        if not got and time.time() - t0 > 0.8:
            break                                        # nothing offered: a plain box
        if any(plain(t).startswith(want) for t, _ in got):
            break
    return got


def same_number(got, want):
    got, want = str(got).strip(), str(want).strip()
    return got.isdigit() and want.isdigit() and int(got) == int(want)


def part_value(el):
    """What a date / number part holds: the widget's own value (aria-valuenow) when it keeps
    one, else the box's text."""
    try:
        return str(el.evaluate("el => { const n = el.getAttribute('aria-valuenow'); return n !== null ? n : el.value; }") or "")
    except Exception:
        return ""


def key_digits(frame, el, value):
    """A number / date part (a date widget keeps its own state; a value set directly may show
    but not be what the page saves): its text selected and typed over, then the same slower
    (the first keys swallowed), then set in one step, then by script."""
    want = str(value).strip()
    same = lambda: same_number(part_value(el), want)

    def keys(delay):
        def run():
            focus(el)
            el.evaluate("el => el.select && el.select()")
            frame.page.keyboard.type(want, delay=delay)
            frame.page.keyboard.press("Tab")
        return run
    ok, how, tried = attempt("digits", [("keys", keys(40)), ("keys-slow", keys(120)),
                                    ("fill", lambda: el.fill(want, timeout=WAIT)), ("js", lambda: js_set(el, want))], same)
    return {"ok": ok, "shown": part_value(el), "technique": how, **({"tried": tried} if not ok else {})}


# a radio / checkbox's state as the page keeps it: its own (checked, or aria-checked), and that
# of any control drawn for it in its field (a styled one keeps its state there: aria-checked,
# data-state="checked", data-checked) — the field being the nearest box around it holding no
# other radio / checkbox
TICK_JS = r"""el => {
  const own = el.matches('input') ? el.checked : el.getAttribute('aria-checked') === 'true';
  // what marks the edge of its field: another real input for an input (the role=radio drawn
  // beside it belongs to it); another role control for a control that is itself one
  const CHOICE = el.matches('input') ? 'input[type=radio], input[type=checkbox]'
                                     : '[role=radio], [role=checkbox], [role=switch]';
  let box = el;
  for (let i = 0; i < 4 && box.parentElement; i++) {
    const p = box.parentElement;
    if ([...p.querySelectorAll(CHOICE)].some(x => x !== el && !x.contains(el) && !el.contains(x))) break;
    box = p;
  }
  const drawn = [];
  document.querySelectorAll('[data-jp-drawn]').forEach(n => n.removeAttribute('data-jp-drawn'));
  for (const n of box.querySelectorAll('[aria-checked], [data-state], [data-checked]')) {
    if (n === el) continue;
    const a = n.getAttribute('aria-checked'), st = n.getAttribute('data-state'), dc = n.getAttribute('data-checked');
    let v = null;
    if (a === 'true' || a === 'false') v = a === 'true';
    else if (st === 'checked' || st === 'unchecked') v = st === 'checked';
    else if (dc === 'true' || dc === 'false') v = dc === 'true';
    if (v === null) continue;
    drawn.push(v);
    if (v !== own && !document.querySelector('[data-jp-drawn]')) n.setAttribute('data-jp-drawn', '1');
  }
  return {own, drawn};
}"""


def ticked(el):
    """(on, agreed): the control's own state, and whether every control drawn for it says the same."""
    try:
        got = el.evaluate(TICK_JS)
    except Exception:
        return el.is_checked(), True
    return bool(got["own"]), all(d == got["own"] for d in got["drawn"])


def tick(el, on=True):
    """A radio / checkbox, by the first technique that leaves it as wanted — its own state and
    that of any control drawn for it (a styled radio can show a tick its form never took: the
    input checked, its drawn control not): a real click where it lands (a styled one — Ashby's —
    registers only that), a click on its label, set checked, set checked forced, a click by
    script."""
    holds = lambda: (lambda st: st[0] == on and st[1])(ticked(el))
    if holds():
        return {"ok": True, "shown": "checked" if on else "unchecked"}
    still = lambda fn: (lambda: fn() if not holds() else None)   # never undo what took
    def drawn():                                         # the drawn control that disagrees, clicked itself
        ticked(el)
        d = el.page.locator('[data-jp-drawn="1"]')
        if not d.count():
            raise ValueError("no drawn control disagrees")
        S.tap(d.first)
    ok, how, tried = attempt("tick", [
        ("drawn", still(drawn)),
        ("click", still(lambda: S.tap(el))),
        ("label", still(lambda: el.evaluate("el => (el.labels && el.labels[0] ? el.labels[0] : el).click()"))),
        ("set", still(lambda: el.set_checked(on, timeout=WAIT))),
        ("force", still(lambda: el.set_checked(on, force=True, timeout=WAIT))),
        ("js", still(lambda: el.evaluate("el => el.click()"))),
    ], holds)
    own, agreed = ticked(el)
    return {"ok": ok, "shown": ("checked" if own else "unchecked") + ("" if agreed else " (its drawn control disagrees)"),
            "technique": how, **({"tried": tried} if not ok else {})}


def press(frame, el):
    """A choice button (Yes / No): pressed unless it already says it is — a second press
    would undo it — by a real click, a forced click, a click by script, until it says it is
    pressed. One that reports no state is taken at its word after the first click."""
    state = lambda: (el.get_attribute("aria-pressed") or el.get_attribute("aria-checked") or "").lower()
    if state() == "true":
        return {"ok": True, "shown": "pressed"}
    if state() == "":                                     # no state to read: one click, taken at its word
        S.tap(el)
        frame.wait_for_timeout(200)
        return {"ok": True, "shown": "clicked"}

    def click(fn):
        def run():
            if state() != "true":
                fn()
                frame.wait_for_timeout(200)
        return run
    ok, how, tried = attempt("press", [("click", click(lambda: S.tap(el))),
                                   ("force", click(lambda: el.click(force=True, timeout=WAIT))),
                                   ("js", click(lambda: el.evaluate("el => el.click()")))],
                         lambda: state() == "true")
    return {"ok": ok, "shown": "pressed" if state() == "true" else "clicked", "technique": how,
            **({"tried": tried} if not ok else {})}


def select_native(el, label):
    """A <select>: by the option's label, by the option whose text means it (spaces, case), by
    the keyboard (focused, the text typed)."""
    shown = lambda: el.evaluate("el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : ''")

    def by_text():
        want = plain(label)
        opts = el.evaluate("el => [...el.options].map(o => o.text)")
        hit = next((o for o in opts if plain(o) == want), None)
        if hit is None:
            raise ValueError("no such option")
        el.select_option(label=hit, timeout=WAIT)

    def keyboard():
        el.focus()
        el.press_sequentially(str(label)[:20], delay=30)
        el.press("Enter")
    ok, how, tried = attempt("select", [("label", lambda: el.select_option(label=str(label), timeout=WAIT)),
                                    ("text", by_text), ("keys", keyboard)],
                         lambda: plain(shown()) == plain(label))
    return {"ok": ok, "shown": shown(), "technique": how, **({"tried": tried} if not ok else {})}


def give_file(frame, el, path):
    """The field's own file input, set directly (a drop zone over it may take the click); else
    the file chooser its button opens. Done when the page shows the file's name."""
    name = os.path.basename(str(path)).lower()
    inp = lambda: el if el.evaluate("el => el.tagName === 'INPUT' && el.type === 'file'") else \
        el.locator("xpath=ancestor::*[.//input[@type='file']][1]//input[@type='file']").first

    def chooser():
        with frame.page.expect_file_chooser(timeout=WAIT) as fc:
            el.click(timeout=WAIT)
        fc.value.set_files(path)

    def shown():
        for _ in range(40):
            try:
                if name in (frame.inner_text("body") or "").lower():
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        return False
    ok, how, tried = attempt("file", [("input", lambda: inp().set_input_files(path, timeout=WAIT)), ("chooser", chooser)], shown)
    return {"ok": ok, "shown": os.path.basename(str(path)) if ok else "the page never showed the file", "technique": how,
            **({"tried": tried} if not ok else {})}


# marks the visible list entry whose words — read exactly as see reads an entry (its text,
# else its label; a state word dropped) — are this text, so the entry clicked is the one
# Claude was shown, however the page draws it (a search highlights part of an entry)
MARK_JS = r"""(want) => {
  const stateWord = /^(minimized|maximized|expanded|collapsed|selected|unselected|not selected|checked|unchecked|not checked)$/i;
  const tail = /[\s,]*\b(minimized|maximized|expanded|collapsed|selected|unselected|not selected|checked|unchecked|not checked)\s*$/i;
  const words = o => [o.innerText, o.getAttribute('aria-label'), o.getAttribute('title')]
      .map(x => (x || '').replace(/\s+/g, ' ').trim().replace(tail, '').trim()).find(x => x && !stateWord.test(x)) || '';
  const norm = s => s.replace(/\s+/g, ' ').trim().toLowerCase();
  document.querySelectorAll('[data-jp-entry]').forEach(e => e.removeAttribute('data-jp-entry'));
  const hit = [...document.querySelectorAll(%s)]
    .find(o => o.getClientRects().length && getComputedStyle(o).visibility !== 'hidden' && norm(words(o)) === norm(want));
  if (hit) hit.setAttribute('data-jp-entry', '1');
  return !!hit;
}""" % repr(S.ENTRY_CSS)


def _entry(frame, text):
    """The visible list entry whose words are exactly this text (read as see reads it)."""
    try:
        frame.evaluate(MARK_JS, str(text))
    except Exception:
        pass
    return frame.locator('[data-jp-entry="1"]')


def _typeable(el):
    try:
        return bool(el.evaluate("el => (el.tagName === 'INPUT' && !el.readOnly) || el.isContentEditable"))
    except Exception:
        return False


def _wait_for(frame, check, wait_s):
    t0 = time.time()
    while time.time() - t0 < wait_s:
        frame.wait_for_timeout(120)
        if check():
            return True
    return False


def _find_entry(frame, el, step, wait_s=3.0):
    """Bring the entry into view: showing already; else typed into the list's own box (a
    search box filters its list — some only once Enter is pressed); else the list scrolled
    until it is drawn."""
    if _entry(frame, step).count():
        return True
    if _typeable(el):
        el.fill("")
        el.press_sequentially(step, delay=25)
        if _wait_for(frame, lambda: _entry(frame, step).count() > 0, wait_s):
            return True
        el.press("Enter")                                 # a box that searches only on Enter
        if _wait_for(frame, lambda: _entry(frame, step).count() > 0, wait_s):
            return True
    for _ in range(80):
        if _entry(frame, step).count():
            return True
        if not frame.evaluate(S.SCROLL_JS):
            break
        frame.wait_for_timeout(60)
    return _entry(frame, step).count() > 0


def pick_chain(frame, c, chain):
    """Open the control's list and pick each step of the chain in turn — a category opens
    its own list, the next step is picked there — down to the last entry. Checked by what
    the control then shows. Nothing is typed into the field as its value."""
    S._close(frame)                                     # a list left open would be read as this one
    before = {e[0] for e in S._entries(frame)[0]}
    el = locate(frame, c)
    S._open(frame, el, before)
    for i, step in enumerate(chain):
        if not _find_entry(frame, el, step):
            showing = [e[0] for e in S._entries(frame)[0]]           # everything showing, as it is
            offered = [e for e in showing if e not in before][:40] or showing[:40]
            S._close(frame, before)
            S.forget_lists(c)                               # what see remembered of this list was not what showed
            where = " › ".join(chain[:i]) or "the list"
            return {"ok": False, "shown": "", "offered": offered,
                    "error": f"{step!r} was not found in {where}" + (
                        f"; it showed {offered[:15]}" if offered else "; no list showed (it did not open, or its search returned nothing)")}
        names = {e[0] for e in S._entries(frame, before)[0]}
        _entry(frame, step).first.click(timeout=WAIT)
        if i < len(chain) - 1:
            S._after_click(frame, before, names, step)     # its own list replaces the one it sat in
    S._close(frame, before)
    ok = _wait_for(frame, lambda: holds(frame, c, chain[-1]), 1.5) or holds(frame, c, chain[-1])
    return {"ok": ok, "shown": "; ".join(shows(frame, c))[:300]}


def pick_list(frame, c, chain):
    """A list's entry by the first technique that makes the control show it: its entries
    clicked, step by step (pick_chain); the last step typed into its box and picked from the
    suggestions that come up (a list that is only a search)."""
    result = {}

    def entries():
        result.update(pick_chain(frame, c, chain))

    def typed():
        el = locate(frame, c)
        if not _typeable(el):
            raise ValueError("not a box to type into")
        result.update(type_into(frame, el, chain[-1], c))

    ok, how, tried = attempt("list", [("entries", entries), ("typed", typed)],
                             lambda: holds(frame, c, chain[-1]) or bool(result.get("ok")))
    return {**result, "ok": ok, "technique": how, **({"tried": tried} if not ok else {})}


def pattern(term):
    """The agent's regex, matched case-insensitively; one that does not compile is its text."""
    try:
        return re.compile(term, re.I)
    except re.error:
        return re.compile(re.escape(term), re.I)


def letters(term):
    """What to type into a search box for a pattern: its literal text — escapes undone,
    anchors and other pattern signs dropped ('\\bIndia\\b' -> 'India', '^I' -> 'I')."""
    out, i = [], 0
    while i < len(term):
        ch = term[i]
        if ch == "\\" and i + 1 < len(term):
            nxt = term[i + 1]
            if not nxt.isalpha():                         # \( \+ \. ...: that sign itself
                out.append(nxt)
            i += 2
            continue
        if ch not in "^$.*?+|{}[]()":
            out.append(ch)
        i += 1
    return "".join(out).strip()


def fields(frame):
    """How many fields (boxes, lists, choices) the page shows."""
    return sum(1 for x in S.parse(S.snapshot(frame)) if x.role in S.FORM_ROLES)


def add_blocks(frame, c, n, wait_s=4.0):
    """A repeated section's Add pressed n times, each press checked: a new block brings new
    fields onto the page. Stops at a press that adds none."""
    added = 0
    for _ in range(n):
        before = fields(frame)
        S.tap(locate(frame, c))
        t0 = time.time()
        while time.time() - t0 < wait_s and fields(frame) <= before:
            frame.wait_for_timeout(150)
        if fields(frame) <= before:
            return {"ok": False, "shown": f"added {added} of {n} block(s)",
                    "error": f"Add was pressed and no new fields appeared (added {added} of {n})"}
        added += 1
    return {"ok": True, "shown": f"added {added} block(s)"}


def search_list(frame, c, terms, limit=25):
    """What each of the agent's patterns matches in the control's list — nothing picked. First
    the list as it can be read whole: its tree (see.read_list; a nested list chain by chain,
    "Social Media › LinkedIn", so an entry under two categories stays two answers), or a list
    that draws only the rows on screen, read by scrolling it end to end. A pattern that finds
    nothing there, in a box you type into, is then searched by the box itself: the pattern's
    literal text typed (Enter when typing alone does nothing), the pattern run over what comes
    back — a search box may open showing nothing, or only its first entries."""
    el = locate(frame, c)
    hits = {}
    tree, more = S.read_list_once(frame, c)
    paths = []
    if more or not tree:                                  # drawn in part, or read nothing: scrolled end to end, once
        paths = S.rows_once(frame, c)
    if not paths and tree:
        def walk(t, pre):
            for k, sub in (t or {}).items():
                (walk(sub, pre + [k]) if sub else paths.append(" › ".join(pre + [k])))
        walk(tree, [])
    for term in terms:
        rx = pattern(term)
        hits[term] = [p for p in paths if rx.search(p)][:limit]
    missing = [t for t in terms if not hits[t]]
    if not missing or not _typeable(el):
        return hits
    before = {e[0] for e in S._entries(frame)[0]}
    for term in missing:
        rx, text = pattern(term), letters(term)
        S._open(frame, el, before)
        opened = [e[0] for e in S._entries(frame, before)[0]]        # the list as it opens, unfiltered
        got = opened
        if text:
            el.fill("")
            drawn = set(S.field_texts(el))
            el.press_sequentially(text, delay=20)
            now = lambda: [e[0] for e in S._entries(frame, before)[0]]
            _wait_for(frame, lambda: now() and now() != opened, 2.5)
            got = now()
            if not got:                                   # a type-ahead drawn as plain elements in the field
                fresh = lambda: [t for t in S.field_texts(el) if t not in drawn]
                _wait_for(frame, lambda: any(rx.search(t) for t in fresh()), 2.5)
                got = fresh()
            if not got or got == opened:                  # typing filtered nothing: a box that searches on Enter
                el.press("Enter")
                _wait_for(frame, lambda: now() and now() != opened, 2.5)
                got = now()
        hits[term] = [g for g in got if rx.search(g)][:limit]
        el.fill("")
        S._close(frame, before)
    return hits


def clear_field(frame, c, tries=8):
    """Empty one control, as a person would: untick a box, empty a text box, set a <select>
    back to its first entry; a box holding picks (pills, a chosen value drawn beside it) has
    them taken out — Backspace in the box, else each pill focused and Delete. A list button
    (one that opens a list and shows its pick as its label) cannot be emptied: pick another
    entry instead. Returns {"ok", "shown"}."""
    el, n = locate(frame, c), nature(c)
    if n == "tick":
        return tick(el, False)
    if n == "select":
        el.select_option(index=0, timeout=WAIT)
        return {"ok": True, "shown": el.evaluate("e => e.options[e.selectedIndex] ? e.options[e.selectedIndex].text : ''")}
    if n in ("type", "digits"):
        focus(el)
        el.fill("", timeout=WAIT)
        return {"ok": not el.input_value(), "shown": el.input_value()}
    if not _typeable(el):
        return {"ok": False, "shown": "; ".join(shows(frame, c)),
                "error": "a list button cannot be emptied — pick another entry instead"}
    held = lambda: [x for x in shows(frame, c) if x.strip()]
    for _ in range(tries):                               # Backspace in the box takes the last pick out
        if not held():
            break
        focus(el)
        el.fill("")
        frame.page.keyboard.press("Backspace")
        frame.wait_for_timeout(200)
    for _ in range(tries):                               # a pill that stays: focused, then Delete
        if not held():
            break
        pill = el.locator("xpath=ancestor::*[.//*[@role='listbox']][1]").locator('[role="listbox"] [role="option"]').first
        try:
            pill.click(timeout=2000)
            frame.page.keyboard.press("Delete")
            frame.wait_for_timeout(250)
        except Exception:
            break
    S._close(frame)
    left = held()
    return {"ok": not left, "shown": "; ".join(left)}


# ---------------------------------------------------------------- one row, and a page of rows

def act_row(frame, c, kind, answer, facts, resume):
    """Do one row on its control. The outcome: ok, what the control shows, what was wanted
    and — when it was not the applicant's answer — the placeholder to ask about; or, for a
    search, what the list holds for each pattern."""
    how, what = read_answer(answer, facts, resume)
    out = {"id": c.id, "control": f"{c.role} {c.ref!r}", "kind": kind, "answer": answer, "how": how,
           "nature": nature(c)}
    if how == "keep":
        return {**out, "ok": True, "shown": "; ".join(shows(frame, c))[:80]}
    if how == "add":
        return {**out, **add_blocks(frame, c, what)}
    if how == "search":
        return {**out, "ok": False, "offered": search_list(frame, c, what),
                "error": "searched: what the list holds for each pattern"}
    el, n = locate(frame, c), out["nature"]
    if how == "file":
        if not what:
            return {**out, "ok": False, "error": "no resume file built for this application"}
        return {**out, **give_file(frame, el, what)}

    placeholder = None
    if how == "fact":
        target = chain_of(what[1]) if n in ("list", "select") else what[1]
    elif how == "chain":
        target = what
    elif how == "guess":
        cands = what["candidates"]
        if not cands:
            return {**out, "ok": False, "error": "a guess without candidates"}
        target = cands[0]
        placeholder = {"question": what["question"], "candidates": [" › ".join(x) for x in cands],
                       "used": " › ".join(cands[0]), "for": what.get("for") or ""}
    else:                                                 # the question itself: nothing stored
        if n in ("type", "digits") and (c.facts or {}).get("required"):
            target = PLACEHOLDER_TEXT if n == "type" else "1"
            placeholder = {"question": what, "candidates": [], "used": target}
        else:
            return {**out, "ok": True, "shown": "left empty (optional, nothing stored)", "question": what}

    if placeholder and n in ("type", "list", "select") and holds(frame, c, placeholder["used"].split(" › ")[-1]):
        # it already holds the placeholder an earlier act put there: settled, still to be asked
        return {**out, "ok": True, "shown": "; ".join(shows(frame, c))[:80], "placeholder": placeholder}
    if n == "tick":
        # a row on a radio, or on one of several choices of a question, means: pick THIS one;
        # only a lone checkbox ("I currently work here") is set either way by a Yes / No fact
        on = True
        lone = c.role != "radio" and (c.facts or {}).get("type") != "radio" and not getattr(c, "one_of_many", False)
        if how == "fact" and lone:                        # a row means tick — unless its fact says No
            on = str(what[1]).strip().lower() not in ("no", "false", "0", "n")
        r = tick(el, on)
    elif n == "press":
        r = press(frame, el)
    elif n == "select":
        r = select_native(el, target[-1] if isinstance(target, list) else target)
    elif n == "list" and kind == "select":
        r = pick_list(frame, c, target if isinstance(target, list) else chain_of(target))
    elif n == "digits":
        r = key_digits(frame, el, target[-1] if isinstance(target, list) else target)
    else:
        r = type_into(frame, el, " › ".join(target) if isinstance(target, list) else target, c)
    wanted = " › ".join(target) if isinstance(target, list) else str(target)
    return {**out, **r, "placeholder": placeholder, "wanted": wanted}


def list_open(frame):
    """Is a list (a dropdown, a menu) showing? Escape is pressed only then: on some boxes it
    also clears what was just typed."""
    try:
        return bool((frame.evaluate(S.ENTRIES_JS) or {}).get("entries"))
    except Exception:
        return False


ORDER = {"select": 1, "write": 2}          # Add first, then choices (they may add or hide fields), then typing


def date_groups(outcomes, by_id):
    """The date / number parts acted on, grouped into dates: parts in one group of the page,
    next to one another (a Month and its Year), in page order."""
    parts = sorted((o for o in outcomes if o.get("nature") == "digits" and o.get("wanted") and o.get("id") in by_id),
                   key=lambda o: by_id[o["id"]].line)
    kind = lambda c: str((c.facts or {}).get("valuetext") or "").strip().upper()   # MM / DD / YYYY, as read empty
    groups = []
    for o in parts:
        c = by_id[o["id"]]
        last = groups[-1][-1] if groups else None
        seen = {kind(by_id[x["id"]]) for x in groups[-1]} if groups else set()
        # the same date: next to the last part, in its group, and not a second part of a kind it
        # already has (a second MM begins the next date — a start date and an end date side by side)
        if last and by_id[last["id"]].group == c.group and 0 < c.line - by_id[last["id"]].line <= 6 \
                and not (kind(c).isalpha() and kind(c) in seen):
            groups[-1].append(o)
        else:
            groups.append([o])
    return groups


def pad(value, c):
    """A part's digits to its width, from what it showed empty (MM, DD, YYYY)."""
    ph = str((c.facts or {}).get("valuetext") or "")
    return str(value).strip().zfill(len(ph)) if ph.isalpha() and len(ph) in (2, 4) else str(value).strip()


def settle_dates(frame, by_id, outcomes, log):
    """Every date part set in this act, read again at the end: a later part that moved the focus
    back (Workday: the year's first key landed in the month) undid an earlier one. Such a date
    is typed in one go into its first part — the widget moving on by itself — and read again."""
    for g in date_groups(outcomes, by_id):
        held = lambda: [same_number(part_value(locate(frame, by_id[o["id"]])), o["wanted"]) for o in g]
        if all(held()):
            continue
        if len(g) > 1:
            try:
                el = locate(frame, by_id[g[0]["id"]])
                focus(el)
                el.evaluate("el => el.select && el.select()")
                frame.page.keyboard.type("".join(pad(o["wanted"], by_id[o["id"]]) for o in g), delay=60)
                frame.page.keyboard.press("Tab")
                frame.wait_for_timeout(300)
            except Exception as e:
                log(f"      a date typed in one go failed: {type(e).__name__}")
            if all(held()):
                _remember("date", "together")
        for o, ok in zip(g, held()):
            got = part_value(locate(frame, by_id[o["id"]]))
            if ok and len(g) > 1:
                o.update(ok=True, shown=got, technique="together")
            elif not ok:
                o.update(ok=False, shown=got, error=f"holds {got!r}, not {o['wanted']!r} — typing another part of this "
                                                    "date changed it")
            log(f"      {o['id']:4} {'✓' if ok else '✗'} date part re-checked -> {got}")


def act_rows(frame, controls, rows, facts, resume, log=print):
    """Every row on its control, in order: Add buttons, then picks, then typing. Each
    control is pinned by its DOM id first, so it is found again after its name changes with
    what it shows. Returns an outcome per row."""
    by_id = {c.id: c for c in controls if getattr(c, "id", "")}
    is_row = lambda r: isinstance(r, list) and len(r) >= 3 and r[0] in by_id
    # the choices of one question: checkboxes / radios sharing a named group with others
    choice = lambda c: c.role in ("radio", "checkbox") or (c.facts or {}).get("type") in ("radio", "checkbox")
    per_group = {}
    for c in controls:
        if choice(c) and c.group:
            per_group[c.group] = per_group.get(c.group, 0) + 1
    for c in controls:
        c.one_of_many = choice(c) and per_group.get(c.group, 0) > 1
    for r in filter(is_row, rows):                      # pinned by DOM id too: a name may change with a pick
        c = by_id[r[0]]
        try:
            c.dom_id = S.element(frame, c).get_attribute("id") or ""
        except Exception:
            c.dom_id = ""
    outcomes = [{"id": r[0] if isinstance(r, list) and r else None, "ok": False,
                 "error": "not a control of this page (its id was not given)"} for r in rows if not is_row(r)]
    good = sorted((r for r in rows if is_row(r)),
                  key=lambda r: 0 if str(r[2]).lower().startswith("add:") else ORDER.get(r[1], 1))
    global PREFER
    for r in good:
        cid, kind, answer = r[:3]
        hint = str(r[3]).strip() if len(r) > 3 and r[3] else ""
        c = by_id[cid]
        try:
            if hint.lower().startswith("keys:"):
                o = keys_row(frame, c, kind, answer, hint[5:])
            else:
                PREFER = hint[10:].strip() if hint.lower().startswith("technique:") else None
                o = act_row(frame, c, kind, answer, facts, resume)
        except Exception as ex:
            o = {"id": cid, "control": f"{c.role} {c.ref!r}", "kind": kind, "answer": answer, "ok": False,
                 "error": f"{type(ex).__name__}: {str(ex).splitlines()[0][:160]}"}
        finally:
            PREFER = None
        if list_open(frame) and not S._close(frame):   # a list left open covers the next control
            log(f"      a list is still open after {cid} — it may cover the next control")
        outcomes.append(o)
        log(f"      {cid:4} {'✓' if o.get('ok') else '✗'} {o.get('control', '')[:44]:44} {str(answer)[:48]:48} "
            f"-> {str(o.get('shown') or o.get('error') or '')[:60]}")
    settle_dates(frame, by_id, outcomes, log)
    return outcomes


def keys_row(frame, c, kind, answer, keys):
    """The agent's own key sequence for a control (a technique the routines do not know): the
    control focused, its text selected, the keys typed — {Tab} / {Enter} / {Escape} pressed —
    then what it holds."""
    el = locate(frame, c)
    focus(el)
    el.evaluate("el => el.select && el.select()")
    for part in re.split(r"(\{\w+\})", keys):
        if re.fullmatch(r"\{\w+\}", part):
            frame.page.keyboard.press(part[1:-1])
        elif part:
            frame.page.keyboard.type(part, delay=40)
    frame.wait_for_timeout(300)
    got = part_value(el) if nature(c) == "digits" else ("; ".join(shows(frame, c)) or part_value(el))
    return {"id": c.id, "control": f"{c.role} {c.ref!r}", "kind": kind, "answer": answer, "how": "keys",
            "nature": nature(c), "ok": True, "shown": got, "technique": "keys:" + keys,
            "note": "your own key sequence: check what it holds"}
