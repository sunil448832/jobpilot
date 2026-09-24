#!/usr/bin/env python3
"""
platforms — everything the tool knows about a specific application platform,
one module per platform, discovered by file name. The fill engine (browser.py,
walk.py) is platform-free and asks this registry for hints; apply.py asks it
to recognise a link and fetch the JD.

A platform module is plain data plus optional functions. Everything is optional
except ID; the generic walker copes with a platform that says nothing.

    ID = "greenhouse"                 # the platform's name, == the file name
    HOSTS = ("greenhouse.io",)        # URL host contains one of these -> this platform
    ROUTE = "mobile"                  # apply.py routing: mobile | desktop | manual | review
    MANUAL = False                    # True: never automated (README rule 1)

    def match_url(url) -> bool        # recognise a link beyond HOSTS (Greenhouse's ?gh_jid=)
    def fetch_jd(url) -> dict | None  # {title, company, location, text, apply_url} via the
                                      # platform's own API; None -> HTML scrape
    FRAME_PATTERNS = (...)            # URL fragments of the iframe the form lives in
    START = {"text": "Apply now"}     # button descriptors (walk.descriptor) tried first
    NEXT = [{"text": "Next"}]         # per page, in order
    SUBMIT = {"text": "Submit application"}

    def walker(page, ctx, answers, resolve, rep) -> object with start/fill/next/retry/
                                      status/finalize (Workday) — lets a live session stop
                                      on a page, fix it, and retry just that page
    def fill_step(page, step, flow) -> bool   # hooks: take over one wizard step (True = done)
    def after_fill(page, step, flow)          # hooks: extra touches after the generic fill of a step
                                              # (flow.answers, flow.resolve, flow.warnings, flow.filled;
                                              #  Workday widgets: pick_listbox, type_prompt, set_date_any)
    def explore(page, ctx, answers, resolve, rep) -> res | None
    def submit(page, ctx, answers, resolve, rep, it) -> res | None
                                      # a custom driver for a wizard the generic walker
                                      # cannot handle (Workday). Return None to decline
                                      # and let the walker run. `res` is walk's result dict.

Unknown platform: `detect()` names it after the host that serves the form
(jobs.smartrecruiters.com -> "smartrecruiters"; a company's own domain ->
"acme-com"). After a successful exploration the observed route is written as a
module here (fill/platform_learn.py, write_from_observation), so the platform is
known next time; a form the walker could not finish is handed to a Claude session
that writes and tests the adapter (platform_learn.with_agent).
"""
import importlib
import os
import pkgutil
import re
import urllib.parse

_MODULES = None


def _load():
    global _MODULES
    if _MODULES is None:
        _MODULES = {}
        for m in pkgutil.iter_modules(__path__):
            if m.name.startswith("_"):
                continue
            try:
                mod = importlib.import_module(f"{__name__}.{m.name}")
            except Exception as e:            # a half-written adapter must not take the registry down
                print(f"  [platforms] {m.name}: import failed ({type(e).__name__}: {str(e)[:80]}) — ignored")
                continue
            _MODULES[getattr(mod, "ID", m.name)] = mod
    return _MODULES


def reload():
    """Forget the cache: a module was just written."""
    global _MODULES
    _MODULES = None
    for k in [k for k in list(importlib.sys.modules) if k.startswith(__name__ + ".")]:
        del importlib.sys.modules[k]
    return _load()


def known():
    return sorted(_load())


def get(platform_id):
    return _load().get(platform_id or "")


def host_of(url):
    return (urllib.parse.urlparse(url or "").netloc or "").lower().split(":")[0]


def slug_for_host(host):
    """A platform id from a host: the registrable part, hyphenated.
    jobs.smartrecruiters.com -> smartrecruiters; careers.acme.co.uk -> acme-co-uk."""
    if re.match(r"^[\d.]+$", host or ""):
        return host.replace(".", "-")           # a bare address: keep it whole
    parts = [p for p in (host or "").split(".") if p]
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "ac", "gov"):
        parts = parts[:-2]                      # acme.co.uk -> acme
    elif len(parts) >= 2:
        parts = parts[:-1]                      # jobs.smartrecruiters.com -> jobs.smartrecruiters
    name = parts[-1] if parts else ""
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:40]


def detect(url, frame_urls=()):
    """The platform for a link. Known modules first (host, then match_url), then
    the host of any frame that holds the form, then a name derived from the host.
    Returns (platform_id, known: bool)."""
    mods = _load()
    for u in [url, *frame_urls]:
        h = host_of(u)
        for pid, mod in mods.items():
            if any(x in h for x in getattr(mod, "HOSTS", ())):
                return pid, True
    for pid, mod in mods.items():
        fn = getattr(mod, "match_url", None)
        if fn and fn(url):
            return pid, True
    for u in frame_urls:                       # an embedded form names its platform by host
        if host_of(u) and host_of(u) != host_of(url):
            return slug_for_host(host_of(u)), False
    return slug_for_host(host_of(url)) or "unknown", False


def frame_patterns():
    out = []
    for mod in _load().values():
        out.extend(getattr(mod, "FRAME_PATTERNS", ()))
    return tuple(out)


def route(platform_id):
    mod = get(platform_id)
    return getattr(mod, "ROUTE", "review") if mod else "review"


def is_manual(platform_id):
    mod = get(platform_id)
    return bool(mod and getattr(mod, "MANUAL", False))


def fetch_jd(platform_id, url):
    mod = get(platform_id)
    fn = getattr(mod, "fetch_jd", None) if mod else None
    return fn(url) if fn else None
