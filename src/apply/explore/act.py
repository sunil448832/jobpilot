"""act.py — do what the map says, control by control, and read each one back.

    act_rows(frame, controls, rows, facts, resume) -> [outcome]

The loop is see -> map -> act -> see:
    see     see.describe: the page's controls, each with an id, its HTML facts, its lists
    map     Claude: one row per control — [id, write|select, answer]
    act     this module: each row on its control, found by its id (the control see read,
            pinned in the page), checked by reading the control back
    see     again, by the caller: what still shows empty, what a pick put on the page, and
            the placeholders set here, go to the next map round

An answer, as the map writes it:
    <fact key>                     the fact's value
    option:<choice>                a choice the control offers
    option:<a> › <b> › <c>         a CHAIN in a nested list: opened once, each step picked
                                   in turn, down to the entry that opens nothing more
    guess:<chain 1>; <chain 2>; ... | <question>
                                   not sure: chain 1 is picked as a PLACEHOLDER so the form
                                   can go on; the question and every candidate are kept to
                                   ask the applicant
    search:<regex>; <regex>        the map's own patterns, most specific first (\bIndia\b, ^I):
                                   each run over the list's entries (a nested list's chains),
                                   what each matches goes back to the map (nothing picked)
    file:resume                    the resume file
    next / submit / start          the page's own buttons: not pressed here (the walk
                                   presses them once the page is filled)
    add:<n>                        a repeated section's Add, pressed n times
    press                          any other button the map wants pressed once (a block's Delete)
    <the question itself>          nothing stored: a required box gets a placeholder text
    text:<value>                   a literal value — the applicant's approved answer, given at replay
    keep:<any answer above>        the map saw the control already showing the right value:
                                   nothing is done to it (the answer is kept for the record)

Which routine acts on a control comes from what its HTML says (see.facts_of): a box is
typed into, a date part keyed digit by digit, a <select> selected, a radio / checkbox
ticked, a choice button pressed, a file input given the file, a list opened and walked.
No name of a control is matched, no pattern read: the control is the one see pinned.
"""
import os
import re
import time

from jobpilot.apply.explore import see as S

WAIT = 5000
PLACEHOLDER_TEXT = "To be confirmed"


class NotFound(Exception):
    pass


# ---------------------------------------------------------------- reading an answer

def chain_of(text):
    """'Social Media › LinkedIn' -> ['Social Media', 'LinkedIn']."""
    return [p.strip() for p in str(text).split("›") if p.strip()]


def read_answer(answer, facts, resume):
    """(how, what) for one map answer:
        ("fact", (key, value))  ("chain", [steps])  ("guess", {"candidates": [chains], "question"})
        ("search", [terms])     ("file", path)      ("button", word)   ("add", n)
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
        body, _, question = a[6:].partition("|")
        cands = [chain_of(x) for x in body.split(";") if chain_of(x)]
        return "guess", {"candidates": cands[:5], "question": question.strip()}
    if low.startswith("search:"):
        return "search", [t.strip() for t in a[7:].split(";") if t.strip()][:3]
    if low == "file:resume":
        return "file", resume
    if low in ("next", "submit", "start"):
        return "button", low
    if low == "press":
        return "press", None
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
    punctuation aside)? Whether a shown value is the RIGHT answer is the map's to judge —
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


def type_into(frame, el, value):
    focus(el)
    el.fill(str(value), timeout=WAIT)
    got = el.input_value()
    return {"ok": plain(got) == plain(value), "shown": got}


def key_digits(frame, el, value):
    """A number / date part: its own text selected and typed over key by key (a date widget
    keeps its own state; a value set directly shows but is not what the page saves), once
    more slower if the first keys were swallowed, then set directly as a last resort."""
    want = str(value).strip()
    same = lambda s: s.strip().isdigit() and want.isdigit() and int(s) == int(want)
    for delay in (40, 120):
        focus(el)
        el.evaluate("el => el.select && el.select()")
        frame.page.keyboard.type(want, delay=delay)
        frame.page.keyboard.press("Tab")
        if same(el.input_value()):
            return {"ok": True, "shown": el.input_value()}
    try:
        el.fill(want, timeout=WAIT)
    except Exception:
        pass
    return {"ok": same(el.input_value()), "shown": el.input_value()}


def tick(el, on=True):
    """Tick (or untick) a radio / checkbox. Styled ones hide the real input under a drawing:
    then force it, then click its label."""
    if el.is_checked() == on:
        return {"ok": True, "shown": "checked" if on else "unchecked"}
    try:
        el.set_checked(on, timeout=WAIT)
    except Exception:
        try:
            el.set_checked(on, force=True, timeout=WAIT)
        except Exception:
            el.evaluate("el => (el.labels && el.labels[0] ? el.labels[0] : el).click()")
    return {"ok": el.is_checked() == on, "shown": "checked" if el.is_checked() else "unchecked"}


def press(frame, el):
    """A choice button (Yes / No): pressed unless it already says it is — a second press
    would undo it. One that reports no state is taken at its word."""
    state = lambda: (el.get_attribute("aria-pressed") or el.get_attribute("aria-checked") or "").lower()
    if state() != "true":
        S.tap(el)
        frame.wait_for_timeout(200)
    return {"ok": state() in ("true", ""), "shown": "pressed" if state() == "true" else "clicked"}


def select_native(el, label):
    el.select_option(label=str(label), timeout=WAIT)
    got = el.evaluate("el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : ''")
    return {"ok": plain(got) == plain(label), "shown": got}


def give_file(frame, el, path):
    """The field's own file input, set directly (a drop zone over it may take the click);
    else the file chooser its button opens. Done when the page shows the file's name."""
    inp = el if el.evaluate("el => el.tagName === 'INPUT' && el.type === 'file'") else \
        el.locator("xpath=ancestor::*[.//input[@type='file']][1]//input[@type='file']").first
    try:
        inp.set_input_files(path, timeout=WAIT)
    except Exception:
        with frame.page.expect_file_chooser(timeout=WAIT) as fc:
            el.click(timeout=WAIT)
        fc.value.set_files(path)
    name = os.path.basename(str(path)).lower()
    for _ in range(40):
        try:
            if name in (frame.inner_text("body") or "").lower():
                return {"ok": True, "shown": os.path.basename(str(path))}
        except Exception:
            pass
        time.sleep(0.25)
    return {"ok": False, "shown": "the page never showed the file"}


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


def pattern(term):
    """The map's regex, matched case-insensitively; one that does not compile is its text."""
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
    """What each of the map's patterns matches in the control's list — nothing picked. First
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
            el.press_sequentially(text, delay=20)
            now = lambda: [e[0] for e in S._entries(frame, before)[0]]
            _wait_for(frame, lambda: now() and now() != opened, 2.5)
            got = now()
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
    """Do one map row on its control. The outcome: ok, what the control shows, where the
    value came from, and — when it was not the applicant's answer — the placeholder to ask
    about; or what the list offered, for the next map round. `acted`: something was done to
    the page (typed, picked, ticked, pressed, a file given, a block added) — a round in which
    nothing was acted, nothing failed and nothing was turned back leaves the page as it is."""
    how, what = read_answer(answer, facts, resume)
    out = {"id": c.id, "control": f"{c.role} {c.ref!r}", "kind": kind, "answer": answer, "how": how,
           "group": c.group or ""}
    if how == "button":
        return {**out, "ok": True, "button": what, "shown": "left for the walk"}
    if how == "keep":                                     # the map judged it already right
        inner, value = what
        src = {"fact": lambda: f"fact:{value[0]}", "chain": lambda: "choice", "file": lambda: "fact:file:resume"}
        return {**out, "ok": True, "kept": True, "shown": "; ".join(shows(frame, c))[:80],
                "source": src.get(inner, lambda: "shown")()}
    if how == "press":
        S.tap(locate(frame, c))
        frame.wait_for_timeout(300)
        return {**out, "ok": True, "acted": True, "shown": "pressed", "source": "press"}
    if how == "add":
        return {**out, **add_blocks(frame, c, what), "source": "add", "acted": True}
    if how == "search":
        return {**out, "ok": False, "offered": search_list(frame, c, what),
                "error": "searched: what the list holds for each term goes back to the map"}
    el, n = locate(frame, c), nature(c)
    if how == "file":
        if not what:
            return {**out, "ok": False, "error": "no resume file built for this application"}
        return {**out, **give_file(frame, el, what), "source": "fact:file:resume", "acted": True}

    placeholder, source = None, None
    if how == "fact":
        key, value = what
        source = f"fact:{key}"
        target = chain_of(value) if n in ("list", "select") else value
    elif how == "chain":
        target, source = what, "choice"
    elif how == "guess":
        cands = what["candidates"]
        if not cands:
            return {**out, "ok": False, "error": "a guess without candidates"}
        target, source = cands[0], f"placeholder:{what['question']}"
        placeholder = {"question": what["question"], "candidates": [" › ".join(x) for x in cands],
                       "used": " › ".join(cands[0])}
    else:                                                 # the question itself: nothing stored
        if n in ("type", "digits") and (c.facts or {}).get("required"):
            target, source = PLACEHOLDER_TEXT if n == "type" else "1", f"placeholder:{what}"
            placeholder = {"question": what, "candidates": [], "used": target}
        else:
            return {**out, "ok": True, "shown": "left empty (optional, nothing stored)", "question": what}

    if placeholder and n in ("type", "list", "select") and holds(frame, c, placeholder["used"].split(" › ")[-1]):
        # it already holds the placeholder an earlier round put there: settled, still to be asked
        return {**out, "ok": True, "kept": True, "shown": "; ".join(shows(frame, c))[:80], "source": source,
                "placeholder": placeholder, "nature": n}
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
        r = pick_chain(frame, c, target if isinstance(target, list) else chain_of(target))
    elif n == "digits":
        r = key_digits(frame, el, target[-1] if isinstance(target, list) else target)
    else:
        r = type_into(frame, el, " › ".join(target) if isinstance(target, list) else target)
    wanted = " › ".join(target) if isinstance(target, list) else str(target)
    return {**out, **r, "source": source, "placeholder": placeholder, "nature": n, "acted": True, "wanted": wanted}


def list_open(frame):
    """Is a list (a dropdown, a menu) showing? Escape is pressed only then: on some boxes it
    also clears what was just typed."""
    try:
        return bool((frame.evaluate(S.ENTRIES_JS) or {}).get("entries"))
    except Exception:
        return False


ORDER = {"select": 1, "write": 2}          # Add first, then choices (they may add or hide fields), then typing


def act_rows(frame, controls, rows, facts, resume, log=print):
    """Every map row on its control, in order: Add buttons, then picks, then typing. Each
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
    for cid, kind, answer in (r[:3] for r in good):
        c = by_id[cid]
        try:
            o = act_row(frame, c, kind, answer, facts, resume)
        except Exception as ex:
            o = {"id": cid, "control": f"{c.role} {c.ref!r}", "kind": kind, "answer": answer, "ok": False,
                 "error": f"{type(ex).__name__}: {str(ex).splitlines()[0][:160]}"}
        if list_open(frame) and not S._close(frame):   # a list left open covers the next control
            log(f"      a list is still open after {cid} — it may cover the next control")
        outcomes.append(o)
        log(f"      {cid:4} {'✓' if o.get('ok') else '✗'} {o.get('control', '')[:44]:44} {str(answer)[:48]:48} "
            f"-> {str(o.get('shown') or o.get('error') or '')[:60]}")
    return outcomes
