#!/usr/bin/env python3
"""
cards.py — where the phone's cards live; the only code that knows it.

A card is one application's item for review and filing. It lives with the application:

    applications/<slug>/cards/<id>.json              the card
    applications/<slug>/cards/<id>.submission.json   the answers his last review decision sent
    applications/<slug>/cards/<slug>-<time>.png      the screenshots its explorations and filings took

next to that application's record (calls.json, explore.json) and resume. Until 2026-09-29
all of it sat in one flat data/queue/.

    cards()                 every card, in id order
    of(slug)                one job's card files, oldest first
    load(id) / save(item)   one card (save puts a new one in its job's folder)
    path(id)                a card's file, or None
    submission(id)          where a review decision's answers go
    folder(slug)            a job's cards/ folder: its screenshots are written there too
    shot(name)              a screenshot's file by its name (no .png), wherever its job is
"""
import glob
import json
import os

from jobpilot.core.paths import APPLICATIONS

SUB = ".submission.json"


def folder(slug):
    return os.path.join(APPLICATIONS, slug, "cards")


def _files(name):
    return glob.glob(os.path.join(APPLICATIONS, "*", "cards", name))


def paths():
    """Every card's file, in id order."""
    return sorted((f for f in _files("*.json") if not f.endswith(SUB)), key=os.path.basename)


def of(slug):
    """One job's card files, in id order (the newest last)."""
    return sorted((f for f in glob.glob(os.path.join(folder(slug), "*.json")) if not f.endswith(SUB)),
                  key=os.path.basename)


def cards():
    for f in paths():
        try:
            yield json.load(open(f))
        except (OSError, ValueError):
            continue


def path(item_id):
    hit = _files(item_id + ".json")
    return hit[0] if hit else None


def load(item_id):
    p = path(item_id)
    if not p:
        raise FileNotFoundError(f"no card {item_id}")
    return json.load(open(p))


def save(item):
    """Write the card; a new one goes into its job's folder. Its path."""
    p = path(item["id"]) or os.path.join(folder(item["company_slug"]), item["id"] + ".json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(item, f, indent=2)
    return p


def submission(item_id):
    p = path(item_id)
    if not p:
        raise FileNotFoundError(f"no card {item_id}")
    return p[:-len(".json")] + SUB


def shot(name):
    hit = _files(os.path.basename(name) + ".png")
    return hit[0] if hit else None
