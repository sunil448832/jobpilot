"""see / act — fill a web form the way a person does, and record it as code.

    SEE   page.aria_snapshot(): the page as a screen reader reads it — every
          control's ROLE (textbox, combobox, radio, button...) and NAME (its label).
          No HTML, no class names, no frontend code.
          A menu's options are not on the page until it is opened: map_menu
          opens it and every category in it (see -> act -> see) and returns the
          whole option tree, so the answer is found by its PATH.
    ACT   find the control BY ROLE AND NAME (page.get_by_role — the browser does
          the matching, from the same label the snapshot showed), run the fixed
          routine for that role, then CHECK what the control shows now.
    RECORD every successful act becomes a recipe {step, role, name, action, source}.
    REPLAY runs the recipes again as plain code — same locator, same routine,
          the approved value — and only then presses Submit.

Nothing here is specific to Workday or Greenhouse: a portal's custom widget
still tells the browser its role and name. This module uses only Playwright.
"""
import os
import re
import time
from dataclasses import dataclass, field

SUBMIT_RX = re.compile(r"^\s*(submit|submit application|send application|apply now|finish)\b", re.I)
# one line of an aria snapshot:   - radio "No" [checked]      - textbox "Email*": sunil@x.com
LINE = re.compile(r'^(\s*)- (\w+)(?: "((?:[^"\\]|\\.)*)")?((?: \[[^\]]*\])*)(?::\s*(.*))?$')
CONTROLS = ("textbox", "combobox", "radio", "checkbox", "button", "option")


# ============================================================== SEE

@dataclass
class Control:
    role: str
    name: str
    value: str = ""
    checked: bool = False
    pressed: bool = False
    group: str = ""                  # the question a radio / Yes-No button belongs to

    @property
    def required(self):
        return self.name.rstrip().endswith("*") or self.group.rstrip().endswith("*")

    def __str__(self):
        state = " [checked]" if self.checked else " [pressed]" if self.pressed else ""
        grp = f"   (in {self.group!r})" if self.group else ""
        val = f" = {self.value!r}" if self.value else ""
        return f"{self.role:9} {self.name!r}{val}{state}{grp}"


def see(page, root="main, form, body"):
    """Every control on the page, as a screen reader lists it."""
    snap = page.locator(root).first.aria_snapshot()
    out, groups = [], []                          # groups: stack of (indent, name)
    for line in snap.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        indent, role, name, attrs, value = len(m[1]), m[2], m[3] or "", m[4] or "", (m[5] or "").strip().strip('"')
        while groups and groups[-1][0] >= indent:
            groups.pop()
        if role in ("group", "radiogroup"):
            groups.append((indent, name))
            continue
        if role in CONTROLS:
            out.append(Control(role, name, value, "[checked" in attrs, "[pressed" in attrs,
                               groups[-1][1] if groups else ""))
    return out


def current_step(page):
    """Where the wizard is: the progress bar's active step, else the page heading."""
    cur = page.locator('[aria-current="step"]')
    if cur.count():
        return cur.first.inner_text().strip()
    h = page.get_by_role("heading", level=2)
    return h.first.inner_text().strip() if h.count() else ""


def alert(page):
    a = page.get_by_role("alert")
    return a.first.inner_text().strip() if a.count() else ""


# ============================================================== SEE A MENU COMPLETELY

CHEVRON = re.compile(r"\s*[›»▸>]\s*$")


def _menu_options(page):
    """The options of the open menu, as (name, is_category). Only options inside a
    listbox — never a native <select>'s, which belong to a different control."""
    out = []
    for o in page.get_by_role("listbox").get_by_role("option").all():
        text = o.inner_text().strip()
        category = (o.get_attribute("aria-haspopup") not in (None, "false")
                    or o.get_attribute("aria-expanded") is not None or bool(CHEVRON.search(text)))
        out.append((CHEVRON.sub("", text), category))
    return out


def map_menu(page, role, name, group=None, log=None):
    """The whole option tree of a menu: {option: None (a leaf) | {child: ...}}.

    The loop is see -> act -> see: open the menu and read the top level; for each
    option marked as a CATEGORY (aria-haspopup / aria-expanded / a chevron), act —
    open it — and see again to read its children. A LEAF is only read, never
    clicked: a click on a leaf would pick it. {} means the menu shows nothing until
    you type (a search-only menu): there, search is the way in."""
    loc = locate(page, role, name, group)
    if loc.evaluate("el => el.tagName") == "SELECT":              # a native menu: its options are all there
        return {o: None for o in loc.evaluate("el => [...el.options].map(o => o.text.trim())")
                if o and not re.match(r"^(select|choose)", o, re.I)}
    loc.click()                                                    # see: the top level
    top = _menu_options(page)
    page.keyboard.press("Escape")
    tree = {n: ({} if cat else None) for n, cat in top}
    for n, cat in top:
        if not cat:
            continue
        locate(page, role, name, group).click()                    # act: reopen, open the category
        page.get_by_role("listbox").get_by_role("option", name=n).first.click()
        tree[n] = {c: ({} if cc else None) for c, cc in _menu_options(page)}   # see: its children
        page.keyboard.press("Escape")
        if log:
            log(f"      map   {name!r}: {n!r} opens {list(tree[n])}")
    return tree


def find_path(tree, value):
    """[category, ..., value] leading to `value` in the tree, or None."""
    for n, sub in (tree or {}).items():
        if n.lower() == str(value).lower():
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


# ============================================================== ACT

class NotFound(Exception):
    pass


class Refused(Exception):
    pass


def locate(page, role, name, group=None):
    """The control with this role and name (inside question `group` when names repeat)."""
    scope = page.get_by_role("group", name=group) if group else page
    loc = scope.get_by_role(role, name=name)
    if loc.count() > 1:
        loc = scope.get_by_role(role, name=name, exact=True)
    if loc.count() == 0:
        raise NotFound(f"no {role} named {name!r}" + (f" in {group!r}" if group else ""))
    return loc.first


def _field_text(loc):
    """Everything the control's field box shows — where a picked value / pill / file name appears."""
    return loc.evaluate("""el => (el.closest('.field, fieldset, [role="group"], [data-automation-id^="formField-"]')
                                  || el.parentElement).innerText""")


def act(page, role, name, action, value=None, group=None, path=(), mode="explore"):
    """Do one thing to one control and check it took. Returns what the control shows now.

    actions, one routine per kind of control:
      open     combobox               open the menu and report its top-level options (to decide a category)
      fill     textbox                type, then read the value back
      choose   combobox               a searchable menu: type + Enter, pick the option
                                      (path: categories to open first, "Social Media")
                                      — or a native <select>: select the option
      pick     button (dropdown)      open it, click the option; the button then shows it
      check    radio / checkbox       tick; read it back
      press    button (Yes/No)        press; aria-pressed must read true
      attach   button ("Attach")      click, answer the file chooser, wait for the file name
      click    button                 anything else (Next, Sign In) — NEVER Submit while exploring
    """
    if action == "pick":
        # a Workday dropdown's name is its label plus what it shows ("Country* Select One"):
        # find it by the label, so it is found again once it shows the pick
        name = name.split(" Select One")[0]
    loc = locate(page, role, name, group)

    if action == "open":
        loc.click()
        offered = [o.inner_text().strip() for o in page.get_by_role("option").all()]
        page.keyboard.press("Escape")
        return {"ok": True, "shown": "", "offered": offered}

    if action == "fill":
        loc.fill(value)
        got = loc.input_value()
        ok = got == value

    elif action == "choose":
        if loc.evaluate("el => el.tagName") == "SELECT":
            loc.select_option(label=value)
            got = loc.evaluate("el => el.options[el.selectedIndex].text")
        else:
            if path:                                     # browse: open the menu, walk into the category
                loc.click()
                for cat in path:
                    page.get_by_role("option", name=cat).first.click()
            else:                                        # search: type, Enter, wait for options
                loc.click()
                loc.fill(value)
                loc.press("Enter")
            opt = page.get_by_role("option", name=value, exact=True)
            try:
                opt.first.wait_for(timeout=3000)
            except Exception:
                offered = [o.inner_text().strip() for o in page.get_by_role("option").all()]
                page.keyboard.press("Escape")
                return {"ok": False, "shown": "", "offered": offered}
            opt.first.click()
            got = value if value in _field_text(locate(page, role, name, group)) else ""
        ok = got == value

    elif action == "pick":
        loc.click()
        page.get_by_role("option", name=value, exact=True).first.click()
        got = locate(page, role, name, group).inner_text().strip()
        ok = got == value

    elif action == "check":
        loc.check()
        ok = locate(page, role, name, group).is_checked()
        got = "checked" if ok else "unchecked"

    elif action == "press":
        loc.click()
        ok = loc.get_attribute("aria-pressed") == "true"
        got = "pressed" if ok else "not pressed"

    elif action == "attach":
        with page.expect_file_chooser(timeout=5000) as fc:
            loc.click(timeout=5000)          # a hidden input can't be clicked: fail fast, not after 30 s
        fc.value.set_files(value)
        base, got = os.path.basename(value), ""
        for _ in range(20):                              # the portal uploads, then shows the name
            if base in _field_text(loc):
                got = base
                break
            time.sleep(0.25)
        ok = bool(got)

    elif action == "click":
        if mode == "explore" and SUBMIT_RX.search(name):
            raise Refused(f"{name!r} would send the application — never pressed while exploring")
        loc.click()
        page.wait_for_timeout(300)
        ok, got = True, "clicked"

    else:
        raise ValueError(action)
    return {"ok": ok, "shown": got}


# ============================================================== RECORD / REPLAY

@dataclass
class Recipe:
    step: str
    role: str
    name: str
    action: str
    source: str = ""                  # where the value comes from: fact:<key> | placeholder | file:resume | ""
    value: str = ""                   # what was used while exploring (a placeholder is marked as such)
    group: str = ""
    path: list = field(default_factory=list)


class Recorder:
    """act() + a recipe for every step that worked. The agent only decides WHAT to
    do; this records HOW, so the submit pass needs no agent."""

    def __init__(self, page, log=print):
        self.page, self.recipes, self.log = page, [], log

    def do(self, role, name, action, value=None, source="", group=None, path=()):
        step = current_step(self.page)          # before acting: a click can move to the next page
        r = act(self.page, role, name, action, value, group, path)
        self.log(f"      act {action:6} {role} {name!r}" + (f" in {group!r}" if group else "")
                 + (f" via {list(path)}" if path else "") + (f" = {value!r}" if value else "")
                 + ("   ✓ " + r["shown"] if r["ok"] else "   ✗ " + str(r.get("offered") or r["shown"])))
        if r["ok"] and action != "open":
            if action == "pick":
                name = name.split(" Select One")[0]
            self.recipes.append(Recipe(step, role, name, action, source,
                                       "" if value is None else str(value), group or "", list(path)))
        return r


def value_for(rec, facts, approved):
    """The value a recipe carries on the submit pass — never the placeholder."""
    if rec.source.startswith("fact:"):
        return facts[rec.source[5:]]
    if rec.source == "placeholder":
        # an answer is filed under its question, with or without the "*"
        q = lambda s: re.sub(r"\s*(\*|Select One)\s*$", "", (s or "").replace("* Select One", "")).strip()
        answers = {q(k): v for k, v in approved.items()}
        got = answers.get(q(rec.group)) or answers.get(q(rec.name))
        if got is None:
            raise Refused(f"{rec.name!r} was a placeholder and has no approved answer — not submitting")
        return got
    if rec.source.startswith("file:"):
        return facts[rec.source]
    return rec.value or None


def replay(page, recipes, facts, approved, log=print):
    """PASS 2: every recipe, in order, as code, with the true values; then Submit."""
    for rec in recipes:
        v = value_for(rec, facts, approved)
        if rec.source == "placeholder" and rec.action == "check":
            # a placeholder radio: tick the APPROVED option instead
            rec = Recipe(rec.step, rec.role, v, rec.action, group=rec.group)
            v = None
        r = act(page, rec.role, rec.name, rec.action, v, rec.group or None, rec.path, mode="replay")
        log(f"      replay {rec.action:6} {rec.role} {rec.name!r} -> {'✓ ' + r['shown'] if r['ok'] else '✗'}")
        if not r["ok"]:
            raise RuntimeError(f"replay step failed: {rec}")


# ============================================================== the account gate (Workday)

def sign_in_or_create(page, email, password, log=print):
    """Workday's account gate, done with the same act() routines. Not recorded:
    the submit pass signs in (the account exists by then) instead of creating it."""
    act(page, "textbox", "Email Address*", "fill", email)
    act(page, "textbox", "Password*", "fill", password)
    act(page, "button", "Sign In", "click")
    if current_step(page) != "Sign In":
        log("    gate: signed in")
        return True
    msg = alert(page)
    if re.search(r"wrong .*password|locked", msg, re.I):
        log(f"    gate: STOP — {msg}")                       # never create over an existing account
        return False
    log(f"    gate: {msg!r} -> creating the account")
    act(page, "button", "Create Account", "click")
    act(page, "textbox", "Email Address*", "fill", email)
    act(page, "textbox", "Password*", "fill", password)
    act(page, "textbox", "Verify New Password*", "fill", password)
    act(page, "checkbox", "I agree to the Candidate Privacy Terms", "check")
    act(page, "button", "Create Account", "click")
    ok = current_step(page) not in ("Sign In", "Create Account", "")
    log("    gate: account created, signed in" if ok else f"    gate: failed — {alert(page)}")
    return ok
