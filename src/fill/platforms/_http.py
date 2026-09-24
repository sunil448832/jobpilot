"""Shared by the platform JD fetchers: one GET, one HTML-to-text."""
import html
import re

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TIMEOUT = 30


def get(url, as_json=False):
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "*/*"}, timeout=TIMEOUT)
    if r.status_code in (404, 410):
        raise SystemExit(f"EXPIRED: HTTP {r.status_code} — {url}")
    r.raise_for_status()
    return r.json() if as_json else r.text


def html_to_text(markup):
    """HTML (possibly entity-encoded) -> readable plain text."""
    soup = BeautifulSoup(html.unescape(markup or ""), "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()
