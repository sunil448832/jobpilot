#!/usr/bin/env python3
"""
hooks.py — application-specific fill code: applications/<slug>/hooks.py.

A platform module (fill/platforms) describes a PLATFORM. One particular form can
still need more: a button the platform module does not name, an iframe on a
company domain, a page the walker misreads, a custom step. That goes here, for
this application only, with the same interface as a platform module (START,
NEXT, SUBMIT, FRAME_PATTERNS, form_url(), explore(), submit()) and the same
rule: the form's structure, never the answers.

Precedence, everywhere the engine asks:  application hooks > company hooks >
platform module > generic walker. "Company hooks" are the hooks.py of
ANOTHER application for the same employer (company name in JD.md): the second
NVIDIA role reuses what the first one needed, without anyone copying a file.

Written by a Claude session when an exploration on a KNOWN platform did not
reach the last page (platform_learn.refine_application: it may touch this file
and nothing else), or alongside a new platform module, or by hand. Loaded as
plain code on both passes; pass 2 never involves an LLM.
"""
import glob
import importlib.util
import os
import re

from jobpilot.core.paths import APPLICATIONS

FILE = "hooks.py"


def path_for(company_slug):
    return os.path.join(APPLICATIONS, company_slug, FILE)


def load(company_slug):
    """The application's hooks module, or None. A broken file is reported and ignored."""
    p = path_for(company_slug) if company_slug else ""
    if not p or not os.path.isfile(p):
        return None
    try:
        spec = importlib.util.spec_from_file_location("app_hooks_" + company_slug.replace("-", "_"), p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        print(f"  [hooks] {p}: import failed ({type(e).__name__}: {str(e)[:80]}) — ignored")
        return None


def company_of(company_slug):
    """The employer's key from an application's JD.md ('- **Company:** NVIDIA')."""
    try:
        t = open(os.path.join(APPLICATIONS, company_slug, "JD.md"), encoding="utf-8").read(3000)
    except OSError:
        return ""
    m = re.search(r"\*\*Company:\*\*\s*(.+)", t)
    return re.sub(r"[^a-z0-9]+", "", (m.group(1) if m else "").lower().replace("careers", ""))


def company_hooks(company_slug):
    """(module, slug) of the newest hooks.py written for another
    application to the same employer, or (None, None)."""
    key = company_of(company_slug)
    if not key:
        return None, None
    cands = []
    for p in glob.glob(os.path.join(APPLICATIONS, "*", FILE)):
        slug = os.path.basename(os.path.dirname(p))
        if slug != company_slug and company_of(slug) == key and _substantive(p):
            cands.append((os.path.getmtime(p), slug))
    for _, slug in sorted(cands, reverse=True):
        mod = load(slug)
        if mod is not None:
            return mod, slug
    return None, None


BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks_base.py")
STUB = open(BASE, encoding="utf-8").read()        # the base hooks every new application starts from


def ensure(company_slug):
    """Every application gets its own hooks.py: a copy of the employer's hooks
    (another application for the same employer) when they exist, else a copy of
    the base hooks (src/fill/hooks_base.py). Returns (path, where_from) —
    where_from is the company slug copied from, or None for the base."""
    p = path_for(company_slug)
    if os.path.isfile(p):
        return p, None
    mod, frm = company_hooks(company_slug)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if mod is not None:
        with open(path_for(frm), encoding="utf-8") as f:
            text = f"# seeded from {frm}/hooks.py (same employer) — edit for this form\n" + f.read()
    else:
        text, frm = STUB, None
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p, frm


def _substantive(p):
    """A hooks file that says something beyond the stub."""
    try:
        return open(p, encoding="utf-8").read().strip() != STUB.strip()
    except OSError:
        return False


class Knowledge:
    """What is known about one form: the application's hooks, the company's
    (another application's hooks for the same employer), then the platform."""

    def __init__(self, app=None, platform=None, company=None, company_from=None):
        self.app, self.platform, self.company, self.company_from = app, platform, company, company_from

    def get(self, name, default=None):
        for m in (self.app, self.company, self.platform):
            if m is not None and hasattr(m, name):
                return getattr(m, name)
        return default

    def custom(self, name):
        """A function (explore / submit / form_url) from whichever layer defines it first."""
        fn = self.get(name)
        return fn if callable(fn) else None

    def describe(self):
        parts = []
        if self.app is not None:
            parts.append("application hooks")
        if self.company is not None:
            parts.append(f"company hooks (from {self.company_from})")
        if self.platform is not None:
            parts.append(f"platform '{getattr(self.platform, 'ID', '?')}'")
        return " > ".join(parts + ["generic walker"])


def knowledge(platform_id, company_slug):
    from jobpilot.fill import platforms
    app = load(company_slug)
    if app is not None and not _substantive(path_for(company_slug)):
        app = None                        # an untouched stub adds nothing; the company's may
    comp, frm = (None, None) if app is not None else company_hooks(company_slug)
    return Knowledge(app, platforms.get(platform_id), comp, frm)
