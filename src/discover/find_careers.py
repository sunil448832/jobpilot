#!/usr/bin/env python3
"""
find_careers.py — locate a company's careers site, then fingerprint its ATS.

careers.py can identify the ATS behind a careers URL, but only if you already
know the URL. Guessing them by hand went badly (careers.adnoc.ae and
jobs.core42.ai do not exist). So this tries the handful of patterns companies
actually use, in parallel, and hands whatever resolves to careers.py.

    python jobs/find_careers.py --domains tii.ae g42.ai mbzuai.ac.ae
    python jobs/find_careers.py --market gulf        # a curated list
    python jobs/find_careers.py --market gulf --add  # ...and register what it finds
"""
import argparse
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}

PATTERNS = [
    "https://careers.{d}", "https://jobs.{d}", "https://{d}/careers",
    "https://www.{d}/careers", "https://{d}/en/careers", "https://www.{d}/en/careers",
    "https://{d}/careers/jobs", "https://{d}/about/careers", "https://{d}/join-us",
    "https://{d}/company/careers", "https://careers.{d}/en",
]

# AI/ML-relevant employers in Sunil's sponsorship markets, grouped so a sweep can
# target one country at a time. The US is deliberately absent — it is oversupplied
# and H-1B-gated, so discovery effort spent there displaces reachable markets.
MARKETS = {
    "gulf": """tii.ae g42.ai mbzuai.ac.ae core42.ai presight.ai inceptioniai.org
        m42.ae bayanat.ai edgegroup.ae aramco.com stc.com.sa sdaia.gov.sa neom.com
        adnoc.ae mubadala.com tabby.ai tamara.co talabat.com kitopi.com
        propertyfinder.ae bayut.com dubizzle.com noon.com anghami.com swvl.com
        emiratesnbd.com mashreqbank.com bankfab.com adcb.com aldar.com emaar.com
        majidalfuttaim.com chalhoubgroup.com alfuttaim.com landmarkgroup.com
        careem.com sarwa.co baraka.com huspy.com""".split(),
    "netherlands": """booking.com adyen.com mollie.com picnic.app catawiki.com
        backbase.com bynder.com tomtom.com miro.com channable.com optiver.com
        imc.com flowtraders.com asml.com philips.com ing.com rabobank.nl kpn.com
        justeattakeaway.com coolblue.nl bol.com elastic.co nedap.com sendcloud.com
        framer.com bird.com messagebird.com""".split(),
    # Added 2026-09-07. Ireland and Germany join NL at priority 1: Critical
    # Skills and the Blue Card are both no-labour-market-test routes for ML.
    "ireland": """stripe.com intercom.com hubspot.com datadoghq.com workday.com
        udemy.com squarespace.com tines.com wayflyer.com letsgetchecked.com
        fenergo.com flipdish.com genesys.com guidewire.com nuritas.com
        ubotica.com soapboxlabs.com everseen.com transfermate.com""".split(),
    "germany": """helsing.ai aleph-alpha.com blackforestlabs.ai deepl.com
        celonis.com merantix.com parloa.com deepset.ai langdock.com zalando.com
        traderepublic.com personio.com getyourguide.com sennder.com forto.com
        taxfix.de raisin.com konux.com wandelbots.com ada.com doctolib.com
        flix.com quantum-systems.com isaraerospace.com nyonic.ai""".split(),
    "luxembourg": """talkwalker.com luxai.com ses.com arcelormittal.com post.lu
        foyer.lu bil.com quintet.com spuerkeess.lu list.lu uni.lu datathings.com
        motion-s.com salonkee.com clearstream.com luxinnovation.lu encevo.eu
        goodyear.com eurofins.com b-medical-systems.com tarkett.com
        luxairgroup.lu husky.co""".split(),
    # Switzerland is low-volume by design — the Permit B quota means only firms
    # that already run the process will engage. Roche and Novartis are listed
    # because Sunil has shipped production work on Roche projects.
    "switzerland": """roche.com novartis.com latticeflow.ai unique.ch
        deepjudge.ai scandit.com nexthink.com beekeeper.io frontify.com
        ledgy.com yokoy.io climeworks.com on.com sonarsource.com daedalean.ai
        visium.ch logitech.com swisscom.ch temenos.com ecorobotix.com
        sensirion.com avaloq.com""".split(),
    "australia": """canva.com atlassian.com safetyculture.com cultureamp.com
        airwallex.com immutable.com eucalyptus.vc rokt.com linktr.ee
        employmenthero.com rea-group.com seek.com.au xero.com zip.co
        octopus.com deputy.com""".split(),
}


def probe_url(url, timeout=12):
    """Does this URL exist and look like a careers page?"""
    try:
        r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
    except Exception:
        return None
    if r.status_code >= 400 or len(r.text) < 2000:
        return None
    low = r.text.lower()
    if not re.search(r"career|job|vacanc|opening|position|hiring|apply", low):
        return None
    return r.url


def find_for(domain):
    for pat in PATTERNS:
        u = probe_url(pat.format(d=domain))
        if u:
            return domain, u
    return domain, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domains", nargs="*")
    ap.add_argument("--market", choices=sorted(MARKETS))
    ap.add_argument("--add", action="store_true", help="register what resolves")
    ap.add_argument("--workers", type=int, default=10)
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    domains = list(a.domains or [])
    if a.market:
        domains += MARKETS[a.market]
    domains = list(dict.fromkeys(domains))
    if not domains:
        ap.error("give --domains or --market")

    print(f"  probing {len(domains)} domains x {len(PATTERNS)} url patterns ...")
    found = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(find_for, d): d for d in domains}
        for fut in as_completed(futs):
            d, url = fut.result()
            if url:
                found.append((d, url))
                print(f"    {d:<24} {url[:66]}")
    print(f"\n  {len(found)}/{len(domains)} careers sites located")

    if not a.add:
        print("  re-run with --add to fingerprint and register them")
        return

    from jobpilot.discover import careers
    ok = 0
    print("\n  fingerprinting the ATS behind each ...")
    for d, url in found:
        try:
            if careers.add(url):
                ok += 1
        except Exception as e:
            print(f"    {d}: {type(e).__name__}")
    print(f"\n  {ok} registered in boards.yaml")


if __name__ == "__main__":
    main()
