"""see.py — a page as a screen reader reads it, and what Claude is shown of it.

    snapshot(frame)              the page's text, as Playwright prints it
    describe(frame, open_lists, chrome)
                                 what the agent is given: every control a person can
                                 reach, each with an id (c1, c2, ...), what its HTML says about it,
                                 what it shows now, and for a list its choices — and the
                                 snapshot with each id written into its control's line.
                                 Nothing is decided here beyond "can a person reach it":
                                 whether a control is written into or picked from, which
                                 question it answers, which button moves on — Claude
                                 decides, and answers each control by its id.

The snapshot read as controls is controls.py's (parse / shown).
"""
from jobpilot.apply.explore_agentic.controls import snapshot, parse, shown, CHROME, FORM_ROLES  # noqa: F401


# ---------------------------------------------------------------- the live element behind a line

def element(frame, c):
    """The one element a snapshot line stands for: the nth of that role and exact name.
    Pins it (data-jp) so it is found again after its name grows with what it shows."""
    loc = frame.get_by_role(c.role, name=c.name, exact=True)
    if c.role == "button" and not loc.count():
        loc = frame.get_by_role("link", name=c.name, exact=True)
    if loc.count() < c.nth:
        return None
    el = loc.nth(c.nth - 1)
    try:
        el.evaluate("(el, r) => el.setAttribute('data-jp', r)", c.ref)
    except Exception:
        pass
    return el


# What the element's HTML says, as facts — no decision made here. Claude reads them
# (as hints); a person could read the same off the page's markup.
FACTS_JS = r"""el => {
  const a = n => el.getAttribute(n);
  const vis = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  // the control's own field box: the widest ancestor that holds no OTHER control (a
  // section holding several fields is not it) — structure only, no class names
  const CTRL = 'input:not([type="hidden"]), textarea, select, button, [role="combobox"], [role="textbox"], [role="button"]';
  let box = el;
  while (box.parentElement && ![...box.parentElement.querySelectorAll(CTRL)].some(x => x !== el && !el.contains(x))) box = box.parentElement;
  const f = {tag: el.tagName.toLowerCase(), type: el.type || '', role: a('role') || '', domid: el.id || '',
             haspopup: a('aria-haspopup') || '', autocomplete: a('aria-autocomplete') || '',
             valuetext: a('aria-valuetext') || '',
             placeholder: (a('placeholder') || '').slice(0, 40), readonly: el.readOnly === true || a('readonly') !== null,
             disabled: el.disabled === true || a('aria-disabled') === 'true',
             required: el.required === true || a('aria-required') === 'true',
             visible: vis(el), multiple: el.multiple === true || a('aria-multiselectable') === 'true',
             hidden_input: el.tagName === 'INPUT' && (getComputedStyle(el).opacity === '0' || el.offsetWidth <= 2 || el.offsetHeight <= 2)};
  // the button of a file field: the first box around it holding a file input holds no
  // other control (a section that also has a file field somewhere is not that)
  const OTHER = 'input:not([type="hidden"]):not([type="file"]), textarea, select, [role="combobox"], [role="textbox"]';
  let n = el;
  for (let i = 0; i < 4 && n.parentElement; i++) {
    n = n.parentElement;
    if (n.querySelector('input[type=file]')) { f.file_near = [...n.querySelectorAll(OTHER)].filter(x => x !== el && vis(x)).length === 0; break; }
  }
  // a list of its own in its box: a multi-select's picks live there; an icon: a list opens from it
  f.own_list = !!(box && box.querySelector('[role="listbox"]'));
  f.list_icon = !!(box && box.querySelector('svg, [class*="icon" i]'));

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
  if (el.tagName === 'INPUT' && !el.value && f.role === 'combobox') { const d = drawn(el); if (d) f.shows = d; }
  if (el.tagName === 'SELECT') f.options = [...el.options].map(o => o.text.trim()).filter(Boolean);
  if (el.tagName === 'BUTTON' || f.role === 'button') f.shows = (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 60);
  return f;
}"""


def facts_of(frame, c):
    """The element's facts (FACTS_JS), {} when it cannot be found."""
    el = element(frame, c)
    if el is None:
        return {}
    try:
        return el.evaluate(FACTS_JS) or {}
    except Exception:
        return {}


def reachable(f):
    """Can a person use this control at all? Shown on screen, not disabled, not a password
    box (signing in is the platform's own step). Whether it is written into or picked
    from, and whether it matters, Claude decides."""
    choice = f.get("type") in ("radio", "checkbox") or f.get("role") in ("radio", "checkbox", "switch")
    # a hidden text input is another control's value holder; a hidden radio / checkbox is the
    # real input under a drawn one, used through its label: it is reached
    # a box that declares itself a combobox is the control itself, however narrow a widget
    # draws it once it shows its pick (react-select shrinks it to 2px)
    holder = f.get("hidden_input") and f.get("role") != "combobox"
    return bool(f) and not f.get("disabled") and f.get("type") != "password" \
        and (choice or (bool(f.get("visible")) and not holder))


def has_list(f, c):
    """Does its HTML say a list of choices may belong to it? — then the list is opened and
    read before Claude maps it (a <select> is read from its HTML). A text box with an icon
    in its field is tried too: when nothing opens, nothing is shown."""
    return (f.get("tag") == "select" or c.role == "combobox" or f.get("autocomplete") == "list"
            or (f.get("haspopup") or "false") != "false" or bool(f.get("own_list"))
            or (bool(f.get("list_icon")) and c.role in ("textbox", "searchbox")))


# ---------------------------------------------------------------- a list, read whole: its tree

# the list entries showing now: [text, category, state]. category: its markup says it opens
# a list of its own (aria-haspopup, aria-expanded). state: "on" / "off" when the entry is a
# tick-box kind of choice (aria-checked, aria-selected, or its label saying "checked" / "not
# checked"), else null. more: the list says it holds more entries than it draws (aria-setsize),
# or — saying nothing — it scrolls.
ENTRIES_JS = r"""() => {
  const stateWord = /^(minimized|maximized|expanded|collapsed|selected|unselected|not selected|checked|unchecked|not checked)$/i;
  const tail = /[\s,]*\b(minimized|maximized|expanded|collapsed|selected|unselected|not selected|checked|unchecked|not checked)\s*$/i;
  const words = o => [o.innerText, o.getAttribute('aria-label'), o.getAttribute('title')]
      .map(x => (x || '').replace(/\s+/g, ' ').trim().replace(tail, '').trim()).find(x => x && !stateWord.test(x)) || '';
  const stateOf = o => {
    const v = o.getAttribute('aria-checked') || o.getAttribute('aria-selected');
    if (v === 'true') return 'on';
    if (v === 'false') {
      const l = (o.getAttribute('aria-label') || '').toLowerCase();
      if (/\bnot checked$|\bunchecked$/.test(l)) return 'off';
      if (/\bchecked$/.test(l)) return 'on';
      return o.getAttribute('aria-checked') !== null ? 'off' : 'off?';
    }
    return null;
  };
  // a field's own list of picks (its pills) is not an open list: every entry says it can be cleared
  const pills = l => { const os = [...l.querySelectorAll('[role="option"]')];
    return os.length > 0 && os.every(o => /press (delete|backspace)|clear value|remove/i.test(o.getAttribute('aria-label') || '')); };
  const out = []; let setsize = 0;
  for (const l of document.querySelectorAll('[role="listbox"], [role="tree"], [role="menu"]')) {
    if (!l.getClientRects().length || pills(l)) continue;
    for (const o of l.querySelectorAll('[role="option"], [role="treeitem"], [role="menuitem"], [role="menuitemcheckbox"]')) {
      if (!o.getClientRects().length || getComputedStyle(o).visibility === 'hidden') continue;
      const t = words(o);
      if (!t) continue;
      out.push([t, (o.getAttribute('aria-haspopup') || 'false') !== 'false' || o.hasAttribute('aria-expanded'), stateOf(o)]);
      setsize = Math.max(setsize, +(o.getAttribute('aria-setsize') || 0));
    }
  }
  // a set size below what is drawn says nothing (some lists give every entry a size of 1)
  let more = setsize >= out.length ? setsize > out.length : false;
  if (!setsize) {
    // a list that scrolls holds more than it draws only when its drawn entries do not fill
    // its scroll height (it draws rows as you scroll); a list that draws every row is whole
    const box = [...document.querySelectorAll('[role="listbox"], [role="tree"], [role="menu"]')].filter(b => b.getClientRects().length && !pills(b)).pop();
    let s = box;
    while (s && s !== document.body) {
      if (s.scrollHeight > s.clientHeight + 4 && /(auto|scroll)/.test(getComputedStyle(s).overflowY)) {
        const drawn = [...box.querySelectorAll('[role="option"], [role="treeitem"], [role="menuitem"]')]
          .reduce((h, o) => h + o.getBoundingClientRect().height, 0);
        more = drawn < s.scrollHeight * 0.8;
        break;
      }
      s = s.parentElement;
    }
  }
  return {entries: out, more};
}"""

# a point on the page's first heading: a click there lands on nothing a person acts on,
# and closes a list that Escape leaves open
HEADING_JS = r"""() => { const h = [...document.querySelectorAll('main h1, main h2, main h3, h1, h2, h3, h4')].find(e => e.getClientRects().length);
  if (!h) return null;
  // scrolled into view first: on a long page scrolled down, the heading is off screen and a
  // click at its old place lands on nothing
  h.scrollIntoView({block: 'center'});
  const r = h.getBoundingClientRect(); return [r.x + Math.min(10, r.width / 2), r.y + r.height / 2]; }"""


# does a click at the control's middle land on it (or inside it)? Scrolled to the middle of
# the screen first. False when something covers it: a pill, a drawn date part, a sticky footer
LANDS_JS = r"""el => {
  el.scrollIntoView({block: 'center', inline: 'nearest'});
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return false;
  const t = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
  return !!t && (t === el || el.contains(t));
}"""


def lands(el):
    try:
        return bool(el.evaluate(LANDS_JS))
    except Exception:
        return False


def tap(el, timeout=1500):
    """Click a control the way it can take a click: a real click when nothing covers it,
    else the click sent to the control itself — never a wait for a cover to go away."""
    if lands(el):
        try:
            el.click(timeout=timeout)
            return
        except Exception:
            pass
    el.evaluate("e => e.click()")


def _entries(frame, before=()):
    """The entries showing now that were not showing before, read until they stop changing
    (a list fills in over a moment). ([(text, category, state)], more)."""
    seen, same, got = None, 0, {"entries": [], "more": False}
    for _ in range(8):
        try:
            got = frame.evaluate(ENTRIES_JS) or got
        except Exception:
            pass
        now = [tuple(e) for e in got.get("entries", []) if e[0] not in before]
        same = same + 1 if now == seen else 0
        if (now and same) or (not now and same >= 2):
            return now, bool(got.get("more"))
        seen = now
        frame.wait_for_timeout(150)
    return seen or [], bool(got.get("more"))


def _open(frame, el, before=(), wait_s=2.5):
    """Open the list a control holds and wait until its entries show. A field's own picks
    (a pill) or the page's sticky footer may cover the control: it is scrolled to the middle
    of the screen and clicked; else clicked through what covers it; else opened from the
    keyboard (focus, arrow down); else sent the click directly. Each way stops as soon as
    entries show."""
    import time

    def showing(wait):
        t0 = time.time()
        while time.time() - t0 < wait:
            frame.wait_for_timeout(120)
            try:
                if [e for e in (frame.evaluate(ENTRIES_JS) or {}).get("entries", []) if e[0] not in before]:
                    return True
            except Exception:
                pass
        return False

    ways = ((lambda: el.click(timeout=1500)) if lands(el) else None,
            lambda: (el.focus(), frame.page.keyboard.press("ArrowDown")),
            lambda: el.evaluate("e => { for (const t of ['mousedown', 'mouseup', 'click']) "
                                "e.dispatchEvent(new MouseEvent(t, {bubbles: true})); }"))
    first = True
    for way in ways:
        if way is None:
            continue
        try:
            way()
        except Exception:
            continue
        if showing(wait_s if first else 1.2):
            return True
        first = False
    return False


def _still_open(frame, before=()):
    try:
        return [e for e in (frame.evaluate(ENTRIES_JS) or {}).get("entries", []) if e[0] not in before]
    except Exception:
        return []


def _close(frame, before=()):
    """Close an open list, and make sure it closed: Escape; one that stays open, by a click
    on the page's heading (scrolled into view); one that stays open still, by taking the
    focus off it (blur, then Tab). A list left open covers the next control and its rows
    would be read as that control's."""
    try:
        frame.page.keyboard.press("Escape")
        frame.wait_for_timeout(120)
        if not _still_open(frame, before):
            return True
        xy = frame.evaluate(HEADING_JS)
        if xy:
            frame.page.mouse.click(*xy)
            frame.wait_for_timeout(200)
        if not _still_open(frame, before):
            return True
        frame.evaluate("() => { const a = document.activeElement; if (a && a.blur) a.blur(); }")
        frame.page.keyboard.press("Tab")
        frame.wait_for_timeout(300)
        if not _still_open(frame, before):
            return True
        xy = frame.evaluate(HEADING_JS)
        if xy:
            frame.page.mouse.click(*xy)
            frame.wait_for_timeout(400)
        return not _still_open(frame, before)
    except Exception:
        return False


ENTRY_CSS = ('[role="listbox"] [role="option"], [role="tree"] [role="treeitem"], [role="menu"] [role="menuitem"], '
             '[role="menu"] [role="menuitemcheckbox"]')


def _showing(frame, text):
    return frame.locator(ENTRY_CSS).filter(has_text=text).locator("visible=true").count() > 0


def _click_entry(frame, text):
    frame.locator(ENTRY_CSS).filter(has_text=text).locator("visible=true").first.click(timeout=3000)
    frame.wait_for_timeout(120)


def _after_click(frame, before, names, t, wait_s=4.0):
    """What an entry's click did, once the list has changed: new entries (it opened its own
    list: a category), or the entry itself ticked. Read until one shows — a sub-list takes
    a moment to replace the list it opened from."""
    import time
    t0 = time.time()
    while time.time() - t0 < wait_s:
        try:
            raw = [tuple(e) for e in (frame.evaluate(ENTRIES_JS) or {}).get("entries", []) if e[0] not in before]
        except Exception:
            raw = []
        if any(e[0] not in names for e in raw):
            return _entries(frame, before)[0]              # the sub-list, read once it settles
        if any(e[0] == t and e[2] == "on" for e in raw):
            return raw
        frame.wait_for_timeout(120)
    return _entries(frame, before)[0]


def _state_of(frame, text):
    """'on' / 'off' / None: the tick state of the visible list entry with this text."""
    try:
        for e in (frame.evaluate(ENTRIES_JS) or {}).get("entries", []):
            if e[0] == text:
                return e[2]
    except Exception:
        pass
    return None


def _holds(frame, c, text):
    """Does the control's field hold this entry as a pick (its pills, read off the snapshot)?"""
    for x in parse(snapshot(frame)):
        if x.role == c.role and x.name == c.name and x.nth == c.nth:
            return text in x.picked or (x.value or "").strip() == text
    return False


def _undo_pick(frame, c, text, before):
    """Undo a pick made only to find out whether an entry opens a list: untick it where the
    list still shows it; else reopen the list and untick it there; else take the pick out
    of the field the way a person does (focus it, press Delete). True when the field no
    longer holds it."""
    if _state_of(frame, text) == "on":
        _click_entry(frame, text)
    _close(frame, before)
    if not _holds(frame, c, text):
        return True
    _open(frame, element(frame, c), before)
    if _state_of(frame, text) == "on":
        _click_entry(frame, text)
    _close(frame, before)
    if not _holds(frame, c, text):
        return True
    try:                                               # the pick as a pill in the field: removed
        el = element(frame, c)
        pill = el.locator("xpath=ancestor::*[.//*[@role='listbox']][1]").locator('[role="listbox"] [role="option"]') \
            .filter(has_text=text).first
        pill.click(timeout=3000)
        frame.page.keyboard.press("Delete")
        frame.wait_for_timeout(400)
        if _holds(frame, c, text):
            frame.page.keyboard.press("Backspace")
            frame.wait_for_timeout(400)
    except Exception:
        pass
    _close(frame, before)
    return not _holds(frame, c, text)


def read_list(frame, c, max_cats=12, max_entries=300):
    """(tree, more): the list that opens from this control, read whole — {entry: None,
    category: {entry: None, ...}} — and nothing left picked. Its top level; then its
    categories: the entries its markup marks as such, opened and read. A list whose
    entries mark nothing but are tick-box kind of choices (checked / not checked) is
    tried entry by entry: an entry that opens a list is a category (read); one that only
    got ticked is unticked again at once. An unmarked entry of a single-pick list is never
    clicked: a click would pick it. `more`: the list holds more than it draws.
    ({}, False) when nothing opens."""
    _close(frame)                                      # a list left open would hide this one's entries
    before = {e[0] for e in _entries(frame)[0]}
    el = element(frame, c)
    if el is None:
        return {}, False
    try:
        _open(frame, el, before)
        top, more = _entries(frame, before)
    finally:
        _close(frame, before)
    tree = {t: ({} if cat else None) for t, cat, _ in top}
    names = set(tree)
    marked = [t for t, cat, _ in top if cat]
    # entries are tried (clicked to see whether they open a list) only while the field holds
    # nothing: a click may replace what it holds, and that cannot be undone
    held = any(x.role == c.role and x.name == c.name and x.nth == c.nth and (x.picked or (x.value or "").strip())
               for x in parse(snapshot(frame)))
    tickable = bool(top) and not marked and not held and all(st in ("off", "on") for _, _, st in top)
    # a list's entries are tried only while they turn out to be categories: its first entry
    # being a plain one says the list is flat (a long list of tick-box entries is not tried
    # entry by entry)
    tryout = marked or ([t for t, _, st in top if st == "off"] if tickable else [])
    total = len(top)
    for t in tryout[:max_cats]:
        if total > max_entries:
            break
        try:
            for _ in range(3):                             # a list may reopen at the sub-list it last showed
                _open(frame, element(frame, c), before)
                if _showing(frame, t):
                    break
                _close(frame, before)
                frame.wait_for_timeout(250)
            else:
                continue                                   # never showed: left unread, never clicked blind
            _click_entry(frame, t)
            now = _after_click(frame, before, names, t)
            kids = [e for e in now if e[0] not in names]
            if kids:                                       # it opened a list of its own: a category
                tree[t] = {k: ({} if kc else None) for k, kc, _ in kids}
                total += len(kids)
            else:                                          # a plain entry: it got picked — undone
                if not _undo_pick(frame, c, t, before):
                    print(f"      [see] could not undo trying {t!r} in {c.role} {c.ref!r} — it may stay picked")
                    break
                if tickable and t == tryout[0]:
                    break                                  # the first entry tried is a plain one: a flat list
        except Exception:
            pass
        finally:
            _close(frame, before)
    return tree, more


LISTS = {}          # (page url, control) -> (tree, more): a list read once per page


def list_key(frame, c):
    return (frame.url, (c.facts or {}).get("domid") or f"{c.role} {c.ref}")


def forget_lists(c=None):
    """Forget the lists read: all of them (a new page), or one control's (it showed other
    entries than were read)."""
    if c is None:
        LISTS.clear()
        return
    for k in [k for k in LISTS if len(k) > 1 and k[1] in ((c.facts or {}).get("domid"), f"{c.role} {c.ref}")]:
        LISTS.pop(k, None)


def read_list_once(frame, c):
    """read_list, remembered for the page: a list's entries do not change while the page
    is filled, and reading a long or nested list takes time."""
    key = list_key(frame, c)
    if key not in LISTS:
        LISTS[key] = read_list(frame, c)
    return LISTS[key]


SCROLL_JS = r"""() => {
  const boxes = [...document.querySelectorAll('[role="listbox"], [role="tree"], [role="menu"]')].filter(b => b.getClientRects().length);
  if (!boxes.length) return false;
  let s = boxes[boxes.length - 1];
  while (s && s.scrollHeight <= s.clientHeight + 2) s = s.parentElement;
  if (!s || s === document.body || s === document.documentElement) return false;
  const before = s.scrollTop;
  s.scrollTop = before + Math.max(40, s.clientHeight - 10);
  return s.scrollTop > before;
}"""


def scroll_all(frame, before=(), max_steps=120):
    """Every entry of the open list, read while it is scrolled from top to bottom — a long
    list draws only the rows on screen."""
    seen = []
    for _ in range(max_steps):
        for e in _entries(frame, before)[0]:
            if e[0] not in seen:
                seen.append(e[0])
        if not frame.evaluate(SCROLL_JS):
            break
        frame.wait_for_timeout(60)
    return seen


def rows_once(frame, c):
    """The open list's every entry, read by scrolling it end to end — once per page."""
    key = list_key(frame, c) + ("rows",)
    if key not in LISTS:
        _close(frame)
        before = {e[0] for e in _entries(frame)[0]}
        _open(frame, element(frame, c), before)
        LISTS[key] = scroll_all(frame, before)
        _close(frame, before)
    return LISTS[key]


def compact_tree(tree):
    """A list's tree as Claude reads it: [a, b › [c, d], e ›] — a category with its entries
    in brackets, one whose entries were not read with a bare "›"."""
    q = lambda o: '"' + o.replace('"', "'") + '"' if any(ch in o for ch in ",[]") else o
    out = []
    for name, sub in (tree or {}).items():
        out.append(q(name) if sub is None else f"{q(name)} ›" + (f" {compact_tree(sub)}" if sub else ""))
    return "[" + ", ".join(out) + "]"


def tree_size(tree):
    return sum(1 + tree_size(sub) if sub else 1 for sub in (tree or {}).values())


def hints(c, f):
    """The component, in a person's words, from its facts: what Claude needs to know to
    answer it — never which answer."""
    tag, typ, role = f.get("tag", ""), f.get("type", ""), f.get("role") or c.role
    out = []
    if role in ("radio", "checkbox", "switch") or typ in ("radio", "checkbox"):
        out.append({"radio": "radio", "switch": "switch"}.get(role, "checkbox") + (": checked" if c.checked else ": not checked"))
    elif tag == "select":
        out.append("a <select>")
    elif typ == "file" or f.get("file_near"):
        out.append("a file picker")
    elif f.get("haspopup") and f["haspopup"] != "false" and tag == "button":
        out.append("a button that opens a list" + (f", shows {f['shows']!r}" if f.get("shows") else ""))
    elif role == "combobox" or f.get("autocomplete") == "list" or (f.get("own_list") and tag in ("input", "textarea")):
        out.append("a search box: type, then pick from the list that appears" + ("; holds several picks" if f.get("multiple") else ""))
    elif tag == "textarea":
        out.append("a text area")
    elif role == "spinbutton" or typ == "number":
        out.append("a number box" + (f" (a date part, shows {f['valuetext']!r})" if f.get("valuetext") else ""))
    elif typ == "password":
        out.append("a password box")
    elif tag == "input":
        out.append("a text box" + (f" type={typ}" if typ and typ != "text" else "") + (" with an icon" if f.get("list_icon") else ""))
    elif tag == "button" or role == "button":
        out.append("a button")
    elif tag == "a" or c.role == "link":
        out.append("a link")
    if f.get("required"):
        out.append("required")
    if f.get("placeholder"):
        out.append(f"placeholder {f['placeholder']!r}")
    if f.get("readonly"):
        out.append("read-only")
    return ", ".join(out)


def compact(opts):
    """A list as Claude reads it, in few tokens: [a, b, c] — each entry as the page words
    it; one holding a comma or a bracket in quotes."""
    q = lambda o: '"' + o.replace('"', "'") + '"' if any(ch in o for ch in ",[]") else o
    return "[" + ", ".join(q(str(o)) for o in opts) + "]"


SELECT_SHOWN = 300          # a <select>'s entries are in its HTML: shown whole up to this many


def describe(frame, open_lists=False, chrome=False):
    """(controls, text, snap): the page read. Every control a person can reach gets an id
    (c1, c2, ...) and
        text   one line each: id, role, name, what its HTML says, what it shows, and for
               a list its choices — a <select>'s from its HTML; with `open_lists`, any
               other list opened and read whole, categories and their entries
               (read_list), never picked from
        snap   the page's snapshot with each id written into the line of its control, so
               Claude sees every control in place — its label, its question, the text
               before and after it — and answers it by its id, never by a name of its own.
    Page chrome (header, navigation, footer) is left out unless `chrome`."""
    snap = snapshot(frame)
    controls = parse(snap)
    lines = snap.splitlines()
    for c in controls:
        c.facts = facts_of(frame, c)
    # links only where a person acts: the page's main region or a dialog (or anywhere but the
    # site's header / menus / footer, when the page marks no main region)
    has_main = any(c.region == "main" for c in controls)
    link_ok = lambda c: c.region in ("main", "dialog") or (not has_main and c.region not in CHROME)
    out, n = [], 0
    for c in controls:
        if c.role == "listbox" or not reachable(c.facts) or (not chrome and c.region in CHROME):
            continue
        if c.role == "link" and not link_ok(c):
            continue
        n += 1
        c.id = f"c{n}"
        now = shown(c) or c.facts.get("shows", "")
        line = f"{c.id}  {c.role} {c.ref!r}   [{hints(c, c.facts)}]" + (f"   shows: {now[:60]!r}" if now else "")
        if c.facts.get("options") is not None:
            opts = c.facts["options"]
            line += (f"   choices: {compact(opts)}" if len(opts) <= SELECT_SHOWN
                     else f"   a long list ({len(opts)} entries): search it")
        elif open_lists and has_list(c.facts, c):
            try:
                tree, more = read_list_once(frame, c)
            except Exception:
                tree, more = {}, False
            typed = c.role in ("textbox", "combobox", "searchbox")
            if tree and tree_size(tree) <= SELECT_SHOWN:
                line += f"   choices: {compact_tree(tree)}" + (
                    "   (only the first entries of a longer list: an entry not among them — search:<term>)"
                    if more else "")
            elif tree:
                line += f"   a long list ({tree_size(tree)}+ entries): search it"
            elif typed and (c.role == "combobox" or c.facts.get("autocomplete") == "list"):
                line += "   a list that fills in as you type: search it"
        out.append(line)
        if 0 <= c.line < len(lines):
            lines[c.line] = lines[c.line].replace("- ", f"- [{c.id}] ", 1)
    return controls, "\n".join(out) or "(none)", "\n".join(lines)


# ---------------------------------------------------------------- the frame and the page state

def form_frame(page, patterns=()):
    """The frame holding the form: a frame whose URL matches a platform's
    FRAME_PATTERNS, else the one with the most form controls."""
    best, most = page.main_frame, -1
    for f in page.frames:
        if patterns and any(p in (f.url or "") for p in patterns):
            if len([c for c in parse(snapshot(f)) if c.role in FORM_ROLES]) >= 2:
                return f
    for f in page.frames:
        n = len([c for c in parse(snapshot(f)) if c.role in FORM_ROLES])
        if n > most:
            best, most = f, n
    return best


ERRORS_JS = r"""() => {
  const vis = e => e.getClientRects().length > 0;
  const txt = x => ((x && (x.innerText || x.textContent)) || '').replace(/\s+/g, ' ').trim();
  // the question a control belongs to, as a person reads it: its label, the element that
  // labels it, its fieldset's legend, a label in its box — its aria-label last (on some
  // portals that is only what the control shows: "Select One Required")
  const labelOf = e => {
    if (e.labels && e.labels[0] && txt(e.labels[0])) return txt(e.labels[0]);
    const by = e.getAttribute('aria-labelledby');
    if (by) { const s = by.split(/\s+/).map(i => txt(document.getElementById(i))).filter(Boolean).join(' '); if (s) return s; }
    const fs = e.closest('fieldset'); const lg = fs && fs.querySelector('legend');
    if (lg && txt(lg)) return txt(lg);
    let n = e;
    for (let i = 0; i < 5 && n.parentElement; i++) { n = n.parentElement; const l = n.querySelector('label, legend'); if (l && txt(l)) return txt(l); }
    return e.getAttribute('aria-label') || e.name || '';
  };
  const out = [];
  // alerts only: a polite live region announces anything (a progress bar's "step 2 of 5")
  for (const e of document.querySelectorAll('[role="alert"], [aria-live="assertive"]')) {
    const t = txt(e);
    if (vis(e) && t && t.length < 400 && !out.includes(t)) out.push(t);
  }
  for (const e of document.querySelectorAll('[aria-invalid="true"]')) {
    const l = labelOf(e);
    if (vis(e) && l && !out.includes('invalid: ' + l)) out.push('invalid: ' + l.slice(0, 200));
  }
  return out.slice(0, 12);
}"""

def errors(frame):
    """What the page itself marks as wrong, by ARIA alone — live regions and alerts, and
    invalid controls named by their label. What the page SAYS in words is in its snapshot,
    which the agent reads for itself when a page is refused."""
    try:
        return list(frame.evaluate(ERRORS_JS) or [])[:12]
    except Exception:
        return []


def body_text(frame, limit=6000):
    try:
        return (frame.inner_text("body") or "")[:limit]
    except Exception:
        return ""
