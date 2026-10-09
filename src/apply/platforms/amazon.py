"""amazon.py — amazon.jobs, Amazon's own careers site: its public search JSON (intake), the
job page for one posting (scaffold falls back to the HTML page). Applying goes through an
amazon.jobs account: sign in once in the tool's Chrome (./jobpilot signin amazon)."""
import requests

from jobpilot.apply.platforms._custom import ISO3, UA, queries, text

HOSTS = ("amazon.jobs",)
ROUTE = "review"
SEARCH = "https://www.amazon.jobs/en/search.json"


def search(markets, known=(), want=lambda title: True):
    seen = set()
    for m in markets:
        cc = ISO3.get(m)
        if not cc:
            continue
        for q in queries():
            for offset in (0, 100):
                try:
                    d = requests.get(SEARCH, params={"base_query": q, "country": cc, "result_limit": 100,
                                                     "offset": offset}, headers=UA, timeout=30).json()
                except Exception:
                    break
                jobs = d.get("jobs") or []
                for j in jobs:
                    url = "https://www.amazon.jobs" + (j.get("job_path") or "")
                    if url in seen or not j.get("job_path"):
                        continue
                    seen.add(url)
                    jd = "\n\n".join(x for x in (text(j.get("description")),
                                                 "BASIC QUALIFICATIONS\n" + text(j.get("basic_qualifications")),
                                                 "PREFERRED QUALIFICATIONS\n" + text(j.get("preferred_qualifications"))) if x)
                    yield {"company": j.get("company_name") or "Amazon", "title": j.get("title") or "",
                           "location": j.get("normalized_location") or j.get("location") or "", "url": url, "jd": jd[:12000]}
                if len(jobs) < 100:
                    break
