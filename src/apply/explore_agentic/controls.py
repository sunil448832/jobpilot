"""controls.py — a page's accessibility snapshot as the controls a person could act on.

    snapshot(frame)   the frame's aria snapshot (YAML text)
    parse(snap)       every node a person could act on, as a Control: role, name, value,
                      state, the group it sits in and the text above it, which one of its
                      name it is
    unquote(line)     a snapshot line YAML-quoted (a name with a colon in it) read plainly
    shown(c)          what a control shows, as the snapshot has it
"""
import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------- the snapshot as controls

# one snapshot line:   - radio "No" [checked]      - textbox "Email*": a@b.com
LINE = re.compile(r'^(\s*)- (\w+)(?: "((?:[^"\\]|\\.)*)")?((?: \[[^\]]*\])*)(?::\s*(.*))?$')
TEXT = ("text", "heading", "paragraph", "strong", "emphasis")
REGIONS = ("banner", "navigation", "contentinfo", "complementary", "main", "dialog", "search")
CHROME = ("banner", "navigation", "contentinfo")          # the site's header, menus and footer
# roles a person acts on — listed to Claude; everything else in the snapshot is context
ACTED = ("textbox", "searchbox", "combobox", "spinbutton", "radio", "checkbox", "switch",
         "button", "listbox", "slider", "link")
FORM_ROLES = ("textbox", "searchbox", "combobox", "spinbutton", "radio", "checkbox", "switch", "listbox")


@dataclass
class Control:
    role: str
    name: str
    value: str = ""
    checked: bool = False
    pressed: bool = False
    group: str = ""                  # the group line it sits under
    above: str = ""                  # the nearest text line above it
    region: str = ""                 # the page region it sits in: banner / navigation / contentinfo / ...
    nth: int = 1                     # the how-many-th control with this role and name on the page
    count: int = 1                   # how many controls on the page have this role and name
    options: list = field(default_factory=list)   # a list's entries, as the snapshot shows them
    picked: list = field(default_factory=list)    # a box's own list of picks (a multi-select's pills)
    facts: dict = field(default_factory=dict)     # what its HTML says (FACTS_JS), once resolved
    line: int = -1                   # its line in the snapshot (where its id is written for Claude)
    id: str = ""                     # its id for Claude on this read of the page (c1, c2, ...)

    @property
    def ref(self):
        """The name to act on: 'Year #3' when the page has several 'Year's."""
        return f"{self.name} #{self.nth}" if self.count > 1 else self.name

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
    """Every node of a snapshot that a person could act on, with the group it sits
    in, the text above it, its region, and — for a list — its entries."""
    out, groups, regions, last_text = [], [], [], ""
    inside_option, option_labelled = None, False     # an option's own lines are read for its label only
    for i, line in enumerate(snap.splitlines()):
        m = LINE.match(unquote(line))
        if not m:
            continue
        indent, role, name = len(m[1]), m[2], unescape(m[3])
        attrs, value = m[4] or "", unescape((m[5] or "").strip().strip('"'))
        if inside_option is not None and indent > inside_option:
            # an option's visible label ("paragraph: LinkedIn" under a pill named
            # "LinkedIn, press delete to clear value.") is the words a person reads
            if role in ("text", "paragraph") and (value or name) and out and out[-1].options and not option_labelled:
                out[-1].options[-1] = value or name
                option_labelled = True
            continue
        inside_option = None
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
        if role in TEXT:
            t = value or name
            if t and len(t) > 2:
                last_text = t
            continue
        if role == "option" and out and out[-1].role in ("listbox", "combobox"):
            out[-1].options.append(name)
            inside_option, option_labelled = indent, False
            continue
        if role in ACTED:
            # its group: the nearest NAMED group around it (a question's choices often sit in a
            # nameless inner group, a grid, inside the group the question names)
            group = next((g[1] for g in reversed(groups) if g[1]), "")
            out.append(Control(role, name, value, "[checked" in attrs, "[pressed" in attrs,
                               group, last_text, regions[-1][1] if regions else "",
                               line=i))
    # a listbox straight after a box, under the same text, is that box's own list of picks
    for i in range(1, len(out)):
        c, prev = out[i], out[i - 1]
        if c.role == "listbox" and prev.role in ("textbox", "searchbox", "combobox") \
                and c.above == prev.above and c.group == prev.group:
            prev.picked = list(c.options)
    seen = {}
    for c in out:
        seen[(c.role, c.name)] = seen.get((c.role, c.name), 0) + 1
        c.nth = seen[(c.role, c.name)]
    for c in out:
        c.count = seen.get((c.role, c.name), 1)
    return out


def unquote(line):
    """A snapshot line the way the other lines read. Playwright writes a line as a YAML
    string when its text needs it (a name holding ": "): - 'combobox "Q: a?"' — the quotes
    around the whole line come off ('' inside a single-quoted one is a quote)."""
    body = line.lstrip()
    if not body.startswith("- ") or body[2:3] not in ("'", '"'):
        return line
    indent, q, rest = line[:len(line) - len(body)], body[2], body[3:]
    out, i = [], 0
    while i < len(rest):
        ch = rest[i]
        if q == "'" and ch == "'" and rest[i + 1:i + 2] == "'":
            out.append("'")
            i += 2
            continue
        if q == '"' and ch == "\\" and i + 1 < len(rest):
            out.append(rest[i + 1])
            i += 2
            continue
        if ch == q:
            return indent + "- " + "".join(out) + rest[i + 1:]
        out.append(ch)
        i += 1
    return line


def shown(c):
    """What a control shows, as the snapshot has it — no reading of what it means. A box:
    its value, or its picks; a choice: its own name when chosen, else nothing; a list
    button: the choice it displays; a list: its entries."""
    if c.picked:
        return "; ".join(c.picked)
    if c.role in ("radio", "checkbox", "switch"):
        return c.name if c.checked else ""
    if c.role == "button" and c.pressed:
        return c.name
    if c.role == "listbox":
        return "; ".join(c.options)
    return c.value or ""
