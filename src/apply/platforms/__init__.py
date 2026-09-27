#!/usr/bin/env python3
"""
platforms — what the tool knows about a specific application platform, one module
per platform, discovered by file name. apply.py asks it to recognise a link and
fetch the JD; the see / map / act engine (explore/, submit/) is
platform-free and asks it only for what happens BETWEEN pages. Everything on a
page is mapped and filled the same way on every platform.

Everything is optional except ID; a platform that says nothing is walked
generically (form in the frame with the most controls; Apply / Next / Submit are
whatever the page map calls start / next / submit).

    ID = "greenhouse"                 # the platform's name, == the file name
    HOSTS = ("greenhouse.io",)        # URL host contains one of these -> this platform
    ROUTE = "mobile"                  # apply.py routing: mobile | desktop | manual | review
    MANUAL = False                    # True: never automated (README rule 1)
    def match_url(url) -> bool        # recognise a link beyond HOSTS (Greenhouse's ?gh_jid=)
    def fetch_jd(url) -> dict | None  # {title, company, location, text, apply_url}
    FRAME_PATTERNS = (...)            # URL fragments of the iframe the form lives in
    def form_url(url) -> str          # the form, when the posting URL is not it (Ashby)
    STANDARD = True                   # its common fields (name, email, resume, EEO) are
                                      # shared across companies in the map store
    def company_key(url) -> str       # the tenant / board: whose page maps to reuse

    # a wizard with more than a Next button between pages (Workday):
    def start(page, ctx, log, press_start) -> bool   # posting -> first page (the account gate);
                                              # press_start(page) presses the map's `start` button
    def step(page) -> str                     # which page is showing
    def next_page(page, step, name) -> (bool, [str])  # press the map's `next` button; the page's errors
    def is_last(step) -> bool                 # the page Submit is on

Unknown platform: `detect()` names it after the host that serves the form
(jobs.smartrecruiters.com -> "smartrecruiters"; a company's own domain ->
"acme-com") and it is walked generically; its page maps are kept like any other.
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
