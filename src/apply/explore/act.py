"""act.py — do one thing to one control, found by ROLE and NAME, and check it took.

The name is what the accessibility snapshot showed (see.py); the browser does
the matching (get_by_role), so no selector, id or frontend code is involved.
Names that repeat on a page (a second "Job Title*" in Work Experience 2) are
written "Job Title*#2"; a control inside a named group "Group › Name".

One routine per KIND — the kinds Claude's map uses (mapper.py):
    text             fill, read back
    number           a spinbutton / date part: type, read back
    search-and-pick  a combobox you type into — or, with a path, open the menu
                     and walk its categories ("Social Media" › "LinkedIn")
    native-select    a <select>: select the option by its text
    dropdown         a button showing "Select One" that opens a list: click, pick
    radio-group      check the option named <value> inside the group
    yes-no-buttons   press the button named <value> inside the group
    checkbox         tick or untick (value Yes / No)
    file             click the visible button, answer the file chooser, wait
                     until the page shows the file name
    button           click (which button is Next / Submit / Start the map decides)
"""
import os
import re
import time

from jobpilot.apply.explore import see as S

WAIT = 5000                                   # ms: a control that is not there fails fast


class NotFound(Exception):
    pass


def _split(name):
    """'Group › Name#2' -> ('Group', 'Name', 2)."""
    group, n = None, 1
    if " › " in name:
        group, name = name.rsplit(" › ", 1)
    m = re.match(r"^(.*?)\s*#(\d+)$", name)
    if m:
        name, n = m.group(1), int(m.group(2))
    return group, name, n


def locate(frame, role, name, group=None):
    """The one control with this role and name (inside `group` when given). Exact
    first; else a control whose name contains it — the map names a dropdown by its
    label ("State"), and the page's name for it grows with what it shows
    ("State Select One Required", then "State Delhi Required")."""
    g2, base, nth = _split(name)
    group = group or g2
    scope = S.group_scope(frame, group) if group else frame
    if scope is None:
        raise NotFound(f"no question {group!r} on the page")
    loc = scope.get_by_role(role, name=base, exact=True)
    if not loc.count():
        loc = scope.get_by_role(role, name=base)
    if not loc.count() and re.search(r"[*✱]\s*$", base):     # a required mark the name does not carry
        loc = scope.get_by_role(role, name=re.sub(r"[\s*✱]+$", "", base))
    if not loc.count() and role == "button":             # an Apply link that is a button to a person
        loc = scope.get_by_role("link", name=base)
    if not loc.count() and group and role == "button":
        # a question's own list button, named by what it shows ("Select One" -> "Yes"):
        # the group's one list button
        lists = scope.locator('button[aria-haspopup="listbox"]')
        if lists.count() == 1:
            loc = lists
    if not loc.count() and not group and nth == 1:
        # named by its question: the one such control under that text
        q = S.under_question(frame, base, role)
        if q is not None:
            return q
    if loc.count() < nth:
        raise NotFound(f"no {role} named {name!r}" + (f" in {group!r}" if group else ""))
    return loc.nth(nth - 1)


def _changed(frame, base, near, before, opened, term, wait_s=4.0):
    """The list's entries once they differ from how it opened (results take a moment to
    come) — or what it shows when the time is up."""
    got, t0 = [], time.time()
    while time.time() - t0 < wait_s:
        frame.wait_for_timeout(300)
        got = [t for t, _ in menu_options(frame, base)] or [l for l in _lines(near) if l not in before and l != term]
        if got and got != opened:
            return got
    return got


class _Picked(Exception):
    """Not an error: the entry was reached by scrolling and picked."""


def search_options(frame, name, term, limit=8, kind="search-and-pick"):
    """What a searchable list offers for this term, without picking: typed, read
    (a listbox, else the lines drawn under the box; a box that searches only on
    Enter gets Enter), then closed and emptied."""
    loc = locate(frame, "button" if kind == "dropdown" else search_role(frame, name), name)
    eid = loc.get_attribute("id")
    if eid:
        loc = frame.locator(f'[id="{eid}"]')
    near = loc.locator("xpath=..")
    base, before = {t for t, _ in menu_options(frame)}, set(_lines(near))
    press(loc)
    frame.wait_for_timeout(500)
    opened = [t for t, _ in menu_options(frame, base)]         # the list as it opened, unfiltered
    if kind == "dropdown":                                     # a list button: read whole, by scrolling
        got, _ = scroll_options(frame, base)
    else:
        loc.fill("")
        loc.press_sequentially(term, delay=25)
        got = _changed(frame, base, near, before, opened, term)
    if (not got or got == opened) and kind != "dropdown":
        loc.press("Enter")                                     # a box that searches only on Enter
        got = _changed(frame, base, near, before, opened, term) or got
    if kind == "dropdown" or got == opened:                    # unfiltered: the entries matching the term
        rx = re.compile(re.escape(term), re.I)
        got = [g for g in got if rx.search(g)] or ([] if got == opened else got)
    _escape(frame)
    if kind != "dropdown":
        try:
            loc.fill("")
        except Exception:
            pass
    return [g for g in got if g.lower() not in ("no items.", "no results", "no matches")][:limit]


def readback(frame, done, log=print):
    """Typed values read back once the whole page is filled: a later action can undo an
    earlier one (a segmented date's Year typed into its Month, a section re-rendering).
    done: [(kind, name, value)]. One redo each."""
    close_menus(frame)                                   # an open popup hides the rest of the page
    for kind, name, value in done:
        if kind not in ("text", "number") or value is None:
            continue
        try:
            got = (locate(frame, "spinbutton" if kind == "number" else "textbox", name).input_value() or "").strip()
            want = str(value).strip()
            if re.sub(r"[\s\-()]", "", got) == re.sub(r"[\s\-()]", "", want) or (kind == "number" and got.isdigit() and want.isdigit() and int(got) == int(want)):
                continue
            r = act(frame, kind, name, value)
            log(f"      recheck {name[:40]!r}: was {got[:20]!r}, set again -> {'ok' if r.get('ok') else r.get('shown')}")
        except Exception as ex:
            log(f"      recheck {name[:40]!r}: {type(ex).__name__}")


FIELD_HTML_JS = r"""el => {
  const box = el.closest('[data-automation-id^="formField-"], fieldset, [role="group"], .field, .form-field')
              || (el.parentElement && el.parentElement.parentElement) || el;
  const c = box.cloneNode(true);
  c.querySelectorAll('script, style, svg, path').forEach(n => n.remove());
  return c.outerHTML.replace(/\s+/g, ' ').replace(/ class="[^"]{40,}"/g, ' class="…"').slice(0, 1500);
}"""


def field_html(frame, role, name):
    """The control's field box as trimmed HTML — sent to the map with a failure, so
    it can see what the control really is."""
    try:
        return locate(frame, role, name).evaluate(FIELD_HTML_JS)
    except Exception:
        return ""


def close_menus(frame):
    """A list or popup left open covers the next control: close it before acting.
    Anything the page marks open (aria-expanded=true), or a visible listbox."""
    try:
        if frame.locator('[aria-expanded="true"]').count() or frame.get_by_role("listbox").count():
            frame.page.keyboard.press("Escape")
            frame.wait_for_timeout(250)
    except Exception:
        pass


def _lines(loc):
    try:
        return [l.strip() for l in (loc.inner_text(timeout=WAIT) or "").splitlines() if l.strip()]
    except Exception:
        return []


def _value(loc):
    try:
        return loc.input_value(timeout=WAIT)
    except Exception:
        return ""


def field_text(loc):
    """What the control's field box shows — where a picked value, pill or file name appears."""
    try:
        return loc.evaluate("""el => {
          let n = el;
          for (let i = 0; i < 5 && n.parentElement; i++) {
            n = n.parentElement;
            if (n.matches('[data-automation-id^="formField-"], fieldset, [role="group"], .field, .form-field, .application-question')) break;
          }
          return n.innerText || '';
        }""") or ""
    except Exception:
        return ""


OPTIONS_JS = r"""() => [...document.querySelectorAll('[role="listbox"] [role="option"]')]
  .filter(o => o.getClientRects().length && getComputedStyle(o).visibility !== 'hidden')
  .map(o => [(o.innerText || '').trim().replace(/^\d+ items? selected,?\s*/i, ''),
             (o.getAttribute('aria-haspopup') || 'false') !== 'false' || o.hasAttribute('aria-expanded')])
  .filter(x => x[0])"""


SCROLL_JS = r"""() => {
  const boxes = [...document.querySelectorAll('[role="listbox"]')].filter(b => b.getClientRects().length);
  if (!boxes.length) return false;
  let s = boxes[boxes.length - 1];
  while (s && s.scrollHeight <= s.clientHeight + 2) s = s.parentElement;
  if (!s || s === document.body || s === document.documentElement) return false;
  const before = s.scrollTop;
  s.scrollTop = before + Math.max(40, s.clientHeight - 20);
  return s.scrollTop > before;
}"""


def scroll_options(frame, ignore=(), want=None, max_steps=80):
    """The open list read whole, by scrolling it — a long list draws only the entries on
    screen. With `want`: stops once that entry is showing. Returns (entries, found)."""
    seen = []
    for _ in range(max_steps):
        try:
            rows = frame.evaluate(OPTIONS_JS) or []
        except Exception:
            rows = []
        for text, _ in rows:
            if text not in ignore and text not in seen:
                seen.append(text)
        if want and any(t.strip().lower() == str(want).strip().lower() for t, _ in rows):
            return seen, True
        if not frame.evaluate(SCROLL_JS):
            break
        frame.wait_for_timeout(150)
    return seen, False


def menu_options(frame, ignore=()):
    """The open menu's VISIBLE options, as (text, is_category) — read in one pass, and
    again until the list stops growing (long lists render in pieces and redraw).
    Not those in `ignore`: what was already on the page before this menu opened."""
    seen = None
    for _ in range(6):
        try:
            rows = frame.evaluate(OPTIONS_JS) or []
        except Exception:
            rows = []
        out = []
        for text, cat in rows:
            if text not in ignore and (text, cat) not in out:
                out.append((text, cat))
        if out and out == seen:
            return out
        seen = out
        frame.wait_for_timeout(350)
    return seen or []


def pick_option(frame, name, timeout=3500):
    """Click the visible option whose visible TEXT is exactly this — the text the map
    was shown (an option's accessible name may carry more). A hidden copy is never picked."""
    opt = frame.locator('[role="listbox"] [role="option"]').filter(
        has_text=re.compile(r"^\s*" + re.escape(name) + r"\s*$")).locator("visible=true")
    opt.first.wait_for(timeout=timeout)
    opt.first.click(timeout=WAIT)


def _escape(frame):
    try:
        frame.page.keyboard.press("Escape")
    except Exception:
        pass


def map_menu(frame, kind, name, max_options=60):
    """The whole option tree of a menu: {option: None (a leaf) | {child: ...}}.
    see -> act -> see: open the menu, read the top level; open each CATEGORY and
    read its children. A leaf is only read, never clicked (a click picks it).
    {} for a menu that shows nothing until you type, or a very long list: search it."""
    role = "button" if kind == "dropdown" else "combobox" if kind == "native-select" else search_role(frame, name)
    loc = locate(frame, role, name)
    if kind == "native-select":
        return {o: None for o in loc.evaluate("el => [...el.options].map(o => o.text.trim())")
                if o and not re.match(r"^(select|choose|please select|--)", o, re.I)}
    base = {t for t, _ in menu_options(frame)}           # on the page before this menu opened
    try:
        press(loc)
        frame.wait_for_timeout(500)
        top = menu_options(frame, base)
    finally:
        _escape(frame)
    if not top or len(top) > max_options:
        return {}
    tree = {n: ({} if cat else None) for n, cat in top}
    for n, cat in top:
        if not cat:
            continue
        try:
            press(locate(frame, role, name))
            frame.wait_for_timeout(400)
            pick_option(frame, n)
            frame.wait_for_timeout(500)
            tree[n] = {c: ({} if cc else None) for c, cc in menu_options(frame, base)}
        except Exception:
            tree[n] = {}
        finally:
            _escape(frame)
    return tree


def peek_choices(frame, role, name, small=10):
    """What a list control offers, read without picking: (choices, n). choices when it
    holds `small` or fewer (a category ends in " ›"), else None; n = how many showed —
    0 for a list that fills in only as you type (or no list at all)."""
    loc = locate(frame, role, name)
    if loc.evaluate("el => el.tagName") == "SELECT":
        opts = [o for o in loc.evaluate("el => [...el.options].map(o => o.text.trim())")
                if o and not re.match(r"^(select|choose|please select|--)", o, re.I)]
        return (opts if len(opts) <= small else None), len(opts)
    base = {t for t, _ in menu_options(frame)}
    try:
        press(loc)
        frame.wait_for_timeout(500)
        top = menu_options(frame, base)
    finally:
        _escape(frame)
        close_menus(frame)
    if 0 < len(top) <= small and any(cat for _, cat in top):
        # categories: opened, so the map sees "Social Media › LinkedIn", not only "Social Media ›"
        try:
            tree = map_menu(frame, "dropdown" if role == "button" else "search-and-pick", name)
            flat = [" › ".join(p) for p in _paths(tree)]
            if 0 < len(flat) <= small:
                return flat, len(flat)
        except Exception:
            pass
    return ([n + (" ›" if cat else "") for n, cat in top] if 0 < len(top) <= small else None), len(top)


def _paths(tree, pre=()):
    for n, sub in (tree or {}).items():
        if sub:
            yield from _paths(sub, pre + (n,))
        else:
            yield list(pre + (n,))


def find_path(tree, value):
    """[category, ..., option] leading to `value` in the tree (case-insensitive), or None."""
    for n, sub in (tree or {}).items():
        if n.strip().lower() == str(value).strip().lower():
            return [n]
        if sub:
            p = find_path(sub, value)
            if p:
                return [n] + p
    return None


def leaves(tree, prefix=""):
    """Every choosable option as 'Category › Option' — what the phone offers."""
    out = []
    for n, sub in (tree or {}).items():
        out += leaves(sub, f"{prefix}{n} › ") if sub else [prefix + n]
    return out


def search_role(frame, name):
    """A search-and-pick box is a combobox on most portals, a plain textbox on some (Workday)."""
    try:
        locate(frame, "combobox", name)
        return "combobox"
    except NotFound:
        return "textbox"


def press(loc):
    """Click a control; if something on top of it takes the click, focus it instead
    (a typed search needs focus, not a pointer)."""
    try:
        loc.click(timeout=WAIT)
    except Exception:
        loc.focus()


def tick(loc, on=True):
    """Tick (or untick) a radio / checkbox. Styled ones hide the real input under a
    drawing: then force it, then click its label."""
    if loc.is_checked() == on:
        return
    try:
        loc.set_checked(on, timeout=WAIT)
    except Exception:
        try:
            loc.set_checked(on, force=True, timeout=WAIT)
        except Exception:
            loc.evaluate("el => (el.labels && el.labels[0] ? el.labels[0] : el).click()")


def act(frame, kind, name, value=None, path=()):
    """Do it; return {"ok", "shown"} (+ "offered" when a menu did not have the value)."""
    page = frame.page

    if kind in ("text", "number"):
        loc = locate(frame, "spinbutton" if kind == "number" else "textbox", name)
        if loc.evaluate("el => el.type === 'password'"):
            raise NotFound(f"{name!r} is a password box: signing in is the platform's, never filled from a map")
        press(loc)                                     # a date part hidden behind a widget: focus instead
        if kind == "number":
            same = lambda s: s.strip().isdigit() and str(value).strip().isdigit() and int(s) == int(value)
            # key by key, the box emptied first: a date widget keeps its own state, and a
            # value set directly (fill) shows in the box but is not what the page saves.
            # Typed over its own selected text — the box is never left empty: in a segmented
            # date (Month / Year) an emptied box sends focus, and the next keys, back to
            # the box before it
            if loc.input_value():
                loc.evaluate("el => el.select && el.select()")
            frame.page.keyboard.type(str(value), delay=60)
            frame.page.keyboard.press("Tab")           # leave the box: the widget settles, then read
            if not same(loc.input_value()):
                try:
                    loc.fill(str(value), timeout=WAIT) # a plain number box that takes no keys
                except Exception:
                    pass
            got = loc.input_value()
            return {"ok": same(got), "shown": got}     # "6" is the month "06"
        loc.fill(str(value))
        got = loc.input_value()
        plain = lambda s: re.sub(r"[\s\-()]", "", s or "")      # a mask's spaces / dashes / brackets
        return {"ok": plain(got) == plain(str(value)), "shown": got}

    if kind == "native-select":
        loc = locate(frame, "combobox", name)
        loc.select_option(label=str(value))
        got = loc.evaluate("el => el.options[el.selectedIndex].text")
        return {"ok": got.strip() == str(value).strip(), "shown": got}

    if kind in ("search-and-pick", "dropdown"):
        role = "button" if kind == "dropdown" else search_role(frame, name)
        loc = locate(frame, role, name)
        if str(value).lower() in field_text(loc).lower() and kind == "search-and-pick":
            return {"ok": True, "shown": value}                       # already picked (a saved draft)
        eid = loc.get_attribute("id")
        if eid:                                        # pinned: a box's name may grow with its suggestions
            loc = frame.locator(f'[id="{eid}"]')
        base = {t for t, _ in menu_options(frame)}
        near = loc.locator("xpath=..")                 # the box's own field: suggestions drawn beside it
        before = set(_lines(near))
        press(loc)
        frame.wait_for_timeout(400)
        if path:
            for cat in path:
                pick_option(frame, cat)
                frame.wait_for_timeout(500)
        elif kind == "search-and-pick":
            loc.fill("")
            loc.press_sequentially(str(value), delay=25)  # key by key: some boxes suggest only on keystrokes
            frame.wait_for_timeout(800)
        try:
            try:
                pick_option(frame, str(value), timeout=2000)
            except Exception:
                if kind == "dropdown" and not path:
                    # a long list that draws only what is on screen: scrolled to the entry
                    _, there = scroll_options(frame, base, want=str(value))
                    if not there:
                        raise
                    pick_option(frame, str(value))
                    raise _Picked()
                if kind != "search-and-pick" or path:
                    raise
                drawn = [l for l in _lines(near) if l not in before]
                if drawn and not menu_options(frame, base):
                    # suggestions without a listbox role (plain lines under the box): the one asked for
                    near.get_by_text(str(value), exact=True).first.click(timeout=2000)
                else:
                    # a box that searches only on Enter — pressed only now: where typing already
                    # filters the list, Enter picks its first entry, not the one asked for
                    loc.press("Enter")
                    frame.wait_for_timeout(500)
                    if menu_options(frame, base):       # the list is showing: pick the entry itself
                        pick_option(frame, str(value))
                    # else Enter picked one and closed the list: the field below says which
        except _Picked:
            pass
        except Exception:
            offered = [t for t, _ in menu_options(frame, base)] or \
                [l for l in _lines(near) if l not in before and l.strip().lower() != str(value).strip().lower()]
            _escape(frame)
            return {"ok": False, "shown": "", "offered": offered}
        frame.wait_for_timeout(400)
        sub = [t for t, _ in menu_options(frame, base)]
        if sub and not path and str(value) not in sub:
            # the pick opened a sub-list: a category, not an answer — its entries go back
            _escape(frame)
            return {"ok": False, "shown": "", "offered": [f"{value} › {s}" for s in sub[:30]]}
        frame.wait_for_timeout(200)
        again = locate(frame, role, name)
        # a box that keeps the picked text as its value shows it there; else its field
        shown = again.inner_text() if kind == "dropdown" else (_value(again) or field_text(again))
        s, v = (shown or "").strip().lower(), str(value).lower()
        # the field shows the picked entry, or a short form of it ("+91" for "India +91")
        ok = bool(s) and (v in s or s in v)
        if not ok:
            _escape(frame)                                 # a list left open hides the rest of the page
        return {"ok": ok, "shown": (shown or "").strip()[:80]}

    if kind in ("radio-group", "checkbox-group"):
        loc = locate(frame, "radio" if kind == "radio-group" else "checkbox", str(value), group=name)
        tick(loc)
        ok = loc.is_checked()
        return {"ok": ok, "shown": "checked" if ok else "unchecked"}

    if kind == "yes-no-buttons":
        loc = locate(frame, "button", str(value), group=name)
        state = lambda: (loc.get_attribute("aria-pressed") or loc.get_attribute("aria-checked") or "").lower()
        if state() != "true":                          # already chosen: a second click would undo it
            loc.click(timeout=WAIT)
            frame.wait_for_timeout(300)
        # a button that reports its state must read "true"; one that reports nothing is taken at its word
        st = (loc.get_attribute("aria-pressed") or loc.get_attribute("aria-checked") or "").lower()
        return {"ok": st in ("true", ""), "shown": "pressed" if st == "true" else "clicked"}

    if kind == "checkbox":                             # value Yes / No: the box is set to it
        loc = locate(frame, "checkbox", name)
        on = str(value if value is not None else "Yes").strip().lower() in ("yes", "true", "1")
        tick(loc, on)
        return {"ok": loc.is_checked() == on, "shown": "checked" if loc.is_checked() else "unchecked"}

    if kind == "file":
        try:
            loc = locate(frame, "button", name)
        except NotFound:                               # named by its question: the file input under it
            loc = S.under_question(frame, name, "file")
            if loc is None:
                raise
        # the field's own file input, set directly (a drop zone over it may take the
        # click); else the file chooser its button opens
        inp = loc if loc.evaluate("el => el.tagName === 'INPUT' && el.type === 'file'") else \
            loc.locator("xpath=ancestor::*[.//input[@type='file']][1]//input[@type='file']").first
        try:
            inp.set_input_files(value, timeout=WAIT)
        except Exception:
            with page.expect_file_chooser(timeout=WAIT) as fc:
                loc.click(timeout=WAIT)
            fc.value.set_files(value)
        base = os.path.basename(str(value))
        for _ in range(40):                                # the portal uploads, then shows the name
            if base.lower() in field_text(loc).lower() or base.lower() in (frame.inner_text("body") or "").lower():
                return {"ok": True, "shown": base}
            time.sleep(0.25)
        return {"ok": False, "shown": "the page never showed the file"}

    if kind == "button":
        locate(frame, "button", name).click(timeout=WAIT)
        frame.wait_for_timeout(300)
        return {"ok": True, "shown": "clicked"}

    raise ValueError(f"unknown kind {kind!r}")
