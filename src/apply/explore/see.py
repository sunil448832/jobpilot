"""see.py — a page as a screen reader reads it.

The accessibility snapshot (Playwright's aria_snapshot) lists every control's
ROLE and NAME — the same role and name act.py later finds it by. No HTML, no
class names. This module turns the snapshot into Control records, finds the
frame that holds the form, and reads the page's own error messages.

Gaps closed against the first parser (docs/see-step-claude-json.md): escaped
quotes in names, radios without a group (their question is the text line above),
spinbutton / searchbox / switch / listbox roles, forms inside iframes.
"""
import re
from dataclasses import dataclass, field

# one snapshot line:   - radio "No" [checked]      - textbox "Email*": a@b.com
LINE = re.compile(r'^(\s*)- (\w+)(?: "((?:[^"\\]|\\.)*)")?((?: \[[^\]]*\])*)(?::\s*(.*))?$')
CONTROLS = ("textbox", "searchbox", "combobox", "spinbutton", "radio", "checkbox", "switch",
            "button", "listbox", "option")
# landmark regions: page chrome (banner, navigation, footer) is told to the map, which skips it
REGIONS = ("banner", "navigation", "contentinfo", "complementary", "main", "dialog", "search")
FORM_ROLES = ("textbox", "searchbox", "combobox", "spinbutton", "radio", "checkbox", "switch", "listbox")


@dataclass
class Control:
    role: str
    name: str
    value: str = ""
    checked: bool = False
    pressed: bool = False
    group: str = ""                  # the group line it sits under (a radio's question)
    above: str = ""                  # the nearest text line above it (a question with no group)
    region: str = ""                 # the page region it sits in: banner / navigation / contentinfo / ...
    nth: int = 1                     # the how-many-th control with this role and name on the page
    count: int = 1                   # how many controls on the page have this role and name

    @property
    def ref(self):
        """The name to act on: 'Year #3' when the page has several 'Year's."""
        return f"{self.name} #{self.nth}" if self.count > 1 else self.name
    options: list = field(default_factory=list)
    above_heading: bool = False      # the text above is a heading: a section's name, not a question

    def __str__(self):
        state = " [checked]" if self.checked else " [pressed]" if self.pressed else ""
        where = f"   (in {self.group!r})" if self.group else ""
        return f"{self.role} {self.name!r}{' = ' + repr(self.value) if self.value else ''}{state}{where}"


def unescape(s):
    return re.sub(r'\\(.)', r'\1', s or "")


def snapshot(frame):
    """The frame's accessibility snapshot, or '' when it cannot be read."""
    try:
        return frame.locator("body").aria_snapshot(timeout=8000)
    except Exception:
        return ""


def parse(snap):
    """Every control in a snapshot, with the group it sits in and the text above it."""
    out, groups, regions, last_text, last_heading = [], [], [], "", False
    for line in snap.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        indent, role, name = len(m[1]), m[2], unescape(m[3])
        attrs, value = m[4] or "", unescape((m[5] or "").strip().strip('"'))
        while groups and groups[-1][0] >= indent:
            groups.pop()
        while regions and regions[-1][0] >= indent:
            regions.pop()
        if role in REGIONS:
            regions.append((indent, role))
            continue
        if role in ("group", "radiogroup"):
            groups.append((indent, name))
            continue
        if role in ("text", "heading", "paragraph", "strong", "emphasis"):
            t = value or name
            if t and len(t) > 2:
                last_text, last_heading = t, role == "heading"
            continue
        if role == "option" and out and out[-1].role in ("listbox", "combobox"):
            out[-1].options.append(name)
            continue
        if role in CONTROLS:
            out.append(Control(role, name, value, "[checked" in attrs, "[pressed" in attrs,
                               groups[-1][1] if groups else "", last_text, regions[-1][1] if regions else "",
                               above_heading=last_heading))
    return out


def form_controls(snap, frame=None):
    """Every input, menu, choice and button on the page, each numbered in page order
    when its role and name repeat ('Year', 'Year #2', 'Year #3'). Which buttons are
    questions (a dropdown, a file picker, a Yes/No) the page's markup says — mapper.describe."""
    out = [c for c in parse(snap) if c.role in FORM_ROLES or c.role == "button"]
    question_runs(out, frame)
    seen = {}
    for c in out:
        if c.group and c.role in ("radio", "button"):
            continue                              # a choice is told apart by its group, not a number
        seen[(c.role, c.name)] = seen.get((c.role, c.name), 0) + 1
        c.nth = seen[(c.role, c.name)]
    for c in out:
        c.count = seen.get((c.role, c.name), 1)
    return out


PART_JS = r"""(el, alsoFile) => {
  if (el.tagName === 'INPUT') return false;
  if (el.getAttribute('aria-haspopup') && el.getAttribute('aria-haspopup') !== 'false') return false;
  // the first box around the button that holds another input: when that input is its only
  // one and a combobox (or a file input), the button belongs to it
  const CTRL = 'input:not([type="hidden"]), textarea, select, [role="combobox"], [role="textbox"]';
  let n = el;
  for (let i = 0; i < 6 && n.parentElement; i++) {
    n = n.parentElement;
    const xs = [...n.querySelectorAll(CTRL)].filter(x => x !== el);
    if (xs.length) {
      if (xs.length !== 1) return false;
      return xs[0].getAttribute('role') === 'combobox' || (alsoFile && xs[0].type === 'file');
    }
  }
  return false;
}"""


def is_part(loc, also_file=False):
    """A button in another control's own box — a combobox's toggle / clear (and with
    also_file, a file field's Replace / Delete) — that opens no list of its own."""
    try:
        return bool(loc.evaluate(PART_JS, also_file))
    except Exception:
        return False


def question_runs(out, frame=None):
    """Choices a page lays out without an ARIA group (Yes / No buttons, a radio or
    checkbox list under a question): one question each, named by the text above them.
    A run is consecutive radios / checkboxes (each after the label of the one before)
    or buttons under the same text; a button named like that text is a field's own
    button, not a choice; text that names another control is that control's label
    (buttons after a field — Back / Next — are not its choices)."""
    names = {c.name for c in out} | {c.group for c in out if c.group}   # text of another control or question

    def part(b):                                  # with the page: a button of a combobox / file field
        k = sum(1 for c in out[:out.index(b)] if c.role == b.role and c.name == b.name)
        return frame is not None and is_part(frame.get_by_role(b.role, name=b.name, exact=True).nth(k), True)
    i = 0
    while i < len(out):
        c = out[i]
        if c.group or c.role not in ("radio", "checkbox", "button"):
            i += 1
            continue
        run = [c]
        for d in out[i + 1:]:
            if d.group or d.role != c.role:
                break
            # after the label of a choice in this run (a short label, "C1", is not kept as text)
            if c.role in ("radio", "checkbox") and d.above not in {x.name for x in run} | {d.name}:
                break
            if c.role == "button" and d.above != c.above:
                break
            run.append(d)
        q = c.above if c.above != c.name else ""
        if len(run) >= 2 and q and q not in names and not c.above_heading and not (c.role == "button" and any(part(b) for b in run)):
            for d in run:
                d.group = q
        i += len(run)


CHOICES = 'input[type=radio], input[type=checkbox], button, [role=radio], [role=checkbox], [role=button]'


def group_scope(frame, name):
    """Where a question's choices are: its ARIA group, else — a question the page
    does not group — the nearest block around the question's text holding choices.
    None when neither is there."""
    g = frame.get_by_role("group", name=name)
    if g.count():
        return g.first
    text = re.sub(r"[\s*✱]+$", "", name)                # the required mark sits in its own element
    t = frame.get_by_text(text, exact=True)
    if not t.count():
        t = frame.get_by_text(text)
    if not t.count():
        # its opening words: a name retyped may differ in a quote mark or its ending
        head = re.split(r"[’'\"“”]", text)[0][:60].strip()
        t = frame.get_by_text(head) if len(head) >= 20 else t
    if not t.count():
        return None
    el = t.first
    for _ in range(6):
        el = el.locator("xpath=..")
        if el.locator(CHOICES).count():
            return el
    return None


ROLE_CSS = {
    "textbox": 'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([type="file"]), textarea, [role="textbox"]',
    "combobox": '[role="combobox"], select',
    "button": 'button, [role="button"]',
    "spinbutton": '[role="spinbutton"], input[type="number"]',
    "checkbox": 'input[type="checkbox"], [role="checkbox"]',
    "file": 'input[type="file"]',
}


def under_question(frame, text, role):
    """The one control of this role under a question's text — for a control whose own
    name says nothing ("Select One Required", "Type here...", none at all). The nearest
    block around the text holding such a control; a list button when it holds several
    buttons. None when it is not one clear control."""
    css = ROLE_CSS.get(role)
    if not css:
        return None
    head = re.sub(r"[\s*✱]+$", "", text)
    t = frame.get_by_text(head, exact=True)
    if not t.count():
        t = frame.get_by_text(head)
    if not t.count():
        return None
    el = t.first
    for _ in range(6):
        el = el.locator("xpath=..")
        found = el.locator(css).locator("visible=true")
        n = found.count()
        if n == 1:
            return found.first
        if n > 1:
            lists = el.locator('button[aria-haspopup="listbox"]').locator("visible=true") if role == "button" else None
            return lists.first if lists is not None and lists.count() == 1 else None
    return None


def group_options(frame, name):
    """The choices of a question group, as a person reads them: the radios' or
    buttons' own names (labels) — not their values, which may be 'true' / 'false'."""
    try:
        snap = group_scope(frame, name).aria_snapshot(timeout=5000)
    except Exception:
        return []
    return [c.name for c in parse(snap) if c.role in ("radio", "button", "checkbox") and c.name]


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
  const out = [];
  for (const e of document.querySelectorAll('[role="alert"], [aria-live="assertive"], [data-automation-id="errorMessage"], .error, .error-message, [class*="error" i]')) {
    const t = (e.innerText || '').trim();
    if (vis(e) && t && t.length < 400 && !out.includes(t)) out.push(t);
  }
  for (const e of document.querySelectorAll('[aria-invalid="true"]')) {
    const l = (e.labels && e.labels[0] && e.labels[0].innerText) || e.getAttribute('aria-label') || e.name || '';
    if (vis(e) && l) out.push('invalid: ' + l.trim());
  }
  return out.slice(0, 12);
}"""


def errors(frame):
    """What the page itself says is wrong: alerts, error text, invalid fields."""
    try:
        return frame.evaluate(ERRORS_JS) or []
    except Exception:
        return []


def body_text(frame, limit=6000):
    try:
        return (frame.inner_text("body") or "")[:limit]
    except Exception:
        return ""
