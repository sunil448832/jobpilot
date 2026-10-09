"""custom.py — shared helpers for employers that run their own careers site (Amazon, Google,
Microsoft ...): what intake asks them for, and how their HTML becomes plain JD text.

Each such site is a platform module with:
    search(queries, markets) -> iterable of {company, title, location, url, jd}   (intake)
    fetch_jd(url) -> {title, company, location, text, apply_url}                 (scaffold)
and an entry under `custom:` in boards.yaml ({slug: amazon, site: amazon}).
"""
import html as _html
import re

from jobpilot.core.config import cfg

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0 Safari/537.36"}
# ISO 3166 alpha-3 of each target market, for sites that filter by country code
ISO3 = {"netherlands": "NLD", "ireland": "IRL", "germany": "DEU", "uae": "ARE", "australia": "AUS",
        "switzerland": "CHE", "luxembourg": "LUX", "usa": "USA"}


def queries():
    """The few searches each careers site gets per scan; intake's own title filter does the rest."""
    return cfg("discovery.custom_queries", ["machine learning", "applied scientist", "AI engineer",
                                             "data scientist", "LLM", "research engineer"])


def text(fragment):
    """HTML fragment -> plain text with line breaks kept."""
    t = re.sub(r"<(br|/p|/li|/h\d|/div)\s*/?>", "\n", fragment or "", flags=re.I)
    t = re.sub(r"<li[^>]*>", "- ", t, flags=re.I)
    t = _html.unescape(re.sub(r"<[^>]+>", "", t))
    return re.sub(r"\n\s*\n+", "\n\n", t).strip()
