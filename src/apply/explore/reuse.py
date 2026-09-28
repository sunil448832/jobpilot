"""reuse.py — page maps kept for reuse, so a page already seen needs no Claude — and
the read of a page AGAINST its stored map: the snapshot is parsed into controls, each
stored entry is matched to the control it names (role and name, then the group or the
text above it — the page's own words, no table of kinds); what fits is acted on without
Claude, what the map does not fit is handed to Claude (see.describe).

    parse(snap)                every node a person could act on, as a Control
    match(controls, entries)   (fitted entries, controls no entry fits, stale entries)
    page_map(snap, entries)    parse + match

    data/maps/<platform>/<company>/<page>.json   one page of one company's form
    data/maps/<platform>/_standard.json          fields every company on a platform
                                                 asks (name, email, resume, EEO...):
                                                 platforms with STANDARD = True
                                                 (Greenhouse, Lever, Ashby)

A map holds entries — name, kind, fact KEY — never a value, so it is safe to
reuse across roles. It is always checked against the live page before use
(mapper.check); when the page has changed, the corrected map REPLACES the
company's one (docs/exploration-plan.md, decisions 1-4).
"""
import datetime as dt
import json
import os
import re
from dataclasses import dataclass, field

from jobpilot.core.paths import DATA
from jobpilot.apply import platforms

ROOT = os.path.join(DATA, "maps")

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
    above_heading: bool = False      # the text above is a heading: a section's name
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
    out, groups, regions, last_text, last_heading = [], [], [], "", False
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
                last_text, last_heading = t, role == "heading"
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
                               above_heading=last_heading, line=i))
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



# ---------------------------------------------------------------- a stored map against the page

def _same_name(a, b):
    n = lambda s: re.sub(r"\s+", " ", re.sub(r"[\s*✱]+$", "", (s or "").strip())).lower()
    return n(a) == n(b)


def fits(entry, c):
    """Does this stored entry name this control? The same role (when the entry has one)
    and the same name — the exact name with its number ("Year #3"), or the entry's name as
    the start of a name that grew with what the control shows ("State" for "State Delhi
    Required") — and, when the entry knows where it sat, the same group or text above."""
    if entry.get("role") and entry["role"] != c.role:
        return False
    name = entry.get("name") or ""
    base = re.sub(r"\s*#\d+$", "", name)
    n = int((re.search(r"#(\d+)$", name) or [0, "1"])[1])
    if _same_name(name, c.ref) or (_same_name(base, c.name) and c.nth == n):
        pass
    elif c.role == "button" and c.nth == n and c.name.lower().startswith(base.lower() + " "):
        pass                                                  # a list button's name grew with its choice
    else:
        return False
    for key in ("group", "above"):
        if entry.get(key) and c.__dict__.get(key) and not _same_name(entry[key], c.__dict__[key]):
            return False
    return True


def match(controls, entries):
    """(fitted, todo, stale): the entries that name a control on this page (each with its
    control in entry["_control"]), the controls no entry names, the entries naming nothing."""
    fitted, stale, taken = [], [], set()
    for e in entries or []:
        hit = next((c for c in controls if id(c) not in taken and fits(e, c)), None)
        if hit is None:
            stale.append(e)
            continue
        taken.add(id(hit))
        fitted.append({**e, "_control": hit})
    todo = [c for c in controls if id(c) not in taken]
    return fitted, todo, stale


def page_map(snap, entries):
    """The page read against its stored map: (controls, fitted, todo). When nothing in the
    map fits, every control is todo — Claude takes the page."""
    controls = parse(snap)
    fitted, todo, _stale = match(controls, entries)
    return controls, fitted, todo

# facts every company's form asks the same way: shared across companies
STANDARD_FACT = re.compile(r"^(personal\.|location\.|links\.|eeo\.|file:resume|job\.current_location)")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60] or "page"


def company_key(platform_id, url, company):
    """The tenant for Workday ('adobe'), the board for Greenhouse, else the company."""
    fn = getattr(platforms.get(platform_id), "company_key", None)
    try:
        k = fn(url) if fn else None
    except Exception:
        k = None
    return slug(k or company)


def _path(platform_id, company, step, accepted=True):
    """<page>.json: the map of a page the portal ACCEPTED (it moved on with it).
    <page>.draft.json: the map being worked on — used only while no accepted one exists."""
    return os.path.join(ROOT, slug(platform_id), company, slug(step) + ("" if accepted else ".draft") + ".json")


def _std_path(platform_id):
    return os.path.join(ROOT, slug(platform_id), "_standard.json")


def _read(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("entries") or []
    except (OSError, ValueError):
        return []


def load(platform_id, company, step):
    """The cached entries for this page: the company's own map, then the platform's
    standard fields for names the company map does not have."""
    own = _read(_path(platform_id, company, step)) or _read(_path(platform_id, company, step, accepted=False))
    if getattr(platforms.get(platform_id), "STANDARD", False):
        names = {e["name"] for e in own}
        own += [e for e in _read(_std_path(platform_id)) if e["name"] not in names]
    return own


def save(platform_id, company, step, entries, source="", accepted=False):
    """Save the company's map of this page. A draft while it is worked on; ACCEPTED
    once the portal took the page — that one replaces the accepted map, is trusted by
    later runs (still checked control by control), and feeds the platform's standard
    fields. A partial run never overwrites an accepted map with a draft."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    entries = resolved(entries)
    p = _path(platform_id, company, step, accepted)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"platform": platform_id, "company": company, "page": step, "updated": now,
                   "from": source, "accepted": accepted, "entries": entries}, f, indent=1, ensure_ascii=False)
    if accepted:
        try:
            os.remove(_path(platform_id, company, step, accepted=False))
        except OSError:
            pass
    if accepted and getattr(platforms.get(platform_id), "STANDARD", False):
        std = {e["name"]: e for e in _read(_std_path(platform_id))}
        for e in entries:
            if e.get("fact") and STANDARD_FACT.search(e["fact"]):
                std[e["name"]] = e
        with open(_std_path(platform_id), "w", encoding="utf-8") as f:
            json.dump({"platform": platform_id, "updated": now, "entries": list(std.values())},
                      f, indent=1, ensure_ascii=False)
    return p


def resolved(entries):
    """What a map may carry into another run: name, kind and a RESOLVED answer — a fact
    key, a choice the map matched (never a guess: that was asked on the phone for one run),
    or the question. Nothing of one run's state: a guess and its nearest entries, a search,
    a menu path, how many blocks a section needed that day."""
    out = []
    for e in entries or []:
        if e.get("kind") == "add":
            continue
        x = {"name": e["name"], "kind": e["kind"], "fact": e.get("fact"), "question": e.get("question")}
        if e.get("option") and not e.get("guess"):
            x["option"] = e["option"]
        if e.get("guess") and not x["fact"] and not x["question"]:
            x["question"] = e["name"]                  # asked again next time, not guessed from memory
        out.append(x)
    return out


# ---------------------------------------------------------------- using a saved map on a live page

GROUPED = ("radio-group", "yes-no-buttons", "checkbox-group")


KIND_ROLE = {"text": "textbox", "number": "spinbutton", "search-and-pick": ("combobox", "textbox"),
             "native-select": "combobox", "dropdown": "button", "checkbox": "checkbox", "file": "button",
             "add": "button"}


def _q(s):
    return re.sub(r"[\s*✱]+$", "", s or "").strip()


def covered(control, entries):
    """Does some entry already stand for this control?"""
    for e in entries:
        # named by its question (the control's own name says nothing): the control under it
        roles = KIND_ROLE.get(e["kind"], ())
        if control.role in (roles if isinstance(roles, tuple) else (roles,)) and _q(e["name"]) \
                and _q(e["name"]) in (_q(control.group), _q(control.above)) and _q(e["name"]) != _q(control.name):
            return True
        grp = e["name"].rsplit(" › ", 1)[0] if " › " in e["name"] else None
        m = re.match(r"^(.*?)\s*#(\d+)$", e["name"].rsplit(" › ", 1)[-1])
        base, nth = (m.group(1), int(m.group(2))) if m else (e["name"].rsplit(" › ", 1)[-1], 1)
        if e["kind"] in GROUPED and control.group and control.group == e["name"]:
            return True
        if grp is not None:
            # "<section> › <label>": the section's list button with that label; a name that
            # is only what it shows ("Select One Required"): the group's one list button
            if control.group == grp and control.role == "button" and e["kind"] == "dropdown":
                lists = [x for x in entries if x["kind"] == "dropdown" and x["name"].startswith(grp + " › ")]
                if control.name.startswith(base) or len(lists) == 1:
                    return True
            if control.group == grp and control.name == base:
                return True                           # the section's own button (Add Another), any kind
            continue
        q = lambda s: re.sub(r"[\s*✱]+$", "", s or "")
        if e["kind"] not in GROUPED and control.name != base and q(base) and (
                q(control.above) == q(base)
                or (control.role == "button" and e["kind"] == "dropdown" and q(control.group) == q(base))):
            return True                               # named by its question: the control under that text
        if getattr(control, "nth", 1) != nth:
            continue                                  # "Year #3" stands for the third "Year" only
        # a dropdown the map names by its label ("State") covers the page's "State Delhi Required"
        if control.name == base or (e["kind"] == "dropdown" and control.name.startswith(base + " ")):
            return True
    return False


def apply(frame, cached, facts, log=print):
    """The entries of a saved map that still fit the live page (mapper.check);
    the rest are dropped — their controls go to Claude as unmapped."""
    from jobpilot.apply.explore.mapper import check
    good, stale = check(frame, cached or [], facts)
    if stale:
        log(f"    [reuse] {len(stale)} saved entr{'y' if len(stale) == 1 else 'ies'} no longer fit: "
            + "; ".join(f"{e['name'][:30]!r} ({err[:40]})" for e, err in stale[:4]))
    elif cached:
        log(f"    [reuse] saved map: {len(good)} entr{'y' if len(good) == 1 else 'ies'} fit")
    return good
