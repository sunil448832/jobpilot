#!/usr/bin/env python3
"""
serve.py — serve the review forms from Sunil's own machine (Phase 3b).

An artifact that declares `db` is organization-internal and always demands a
claude.ai login. Serving locally removes the login entirely, keeps every
application on the machine, and lets answers write straight back into the queue
file — which also matches the plan's "everything stays on the system" shape.

Reach it from the phone:
  - same wifi:      http://<laptop-ip>:8765/?t=<token>
  - anywhere:       Tailscale, then http://<tailscale-name>:8765/?t=<token>

A submitted form updates the card AND keeps its list picks for the employer portal
(applications/_tenants/<tenant>.yaml, learn.py),
so the same question is never asked again.

Usage:
    python jobs/serve.py                 # prints the URLs to open
    python jobs/serve.py --port 8765
    python jobs/serve.py --no-token      # LAN only, skip the token
"""
import argparse
import copy
import datetime as dt
import glob
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, LOGS, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, MEMORY, ENV_FILE, TAILSCALE)
from jobpilot.core.config import cfg  # noqa: E402
from jobpilot.core import cards as CD  # noqa: E402
from jobpilot.review import form as form_mod                                    # noqa: E402

TOKEN = None
LOCK = threading.Lock()
TG_ENV = ENV_FILE


def telegram(text):
    """Confirm a decision on Telegram. A decision you cannot see did not land."""
    try:
        env = {}
        for line in open(TG_ENV):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
        tok, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
        if not (tok and chat):
            return False
        import urllib.request
        import urllib.parse
        data = urllib.parse.urlencode({
            "chat_id": chat, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": "true"}).encode()
        with urllib.request.urlopen(
                f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=15) as r:
            return json.load(r).get("ok", False)
    except Exception as e:
        print(f"  [warn] telegram notify failed: {e}")
        return False


def items():
    return list(CD.cards())


def item_path(i):
    """The card's file ("" when there is no such card)."""
    return CD.path(i) or ""


INDEX_CSS = """
body{background:#F3F6F9;color:#131A23;font-family:"Source Sans 3",system-ui,sans-serif;
margin:0;padding:24px 16px}
@media(prefers-color-scheme:dark){body{background:#0D1218;color:#E6EDF4}
a.card{background:#151D26!important;border-color:#243039!important;color:#E6EDF4!important}
.m{color:#7E90A2!important}}
.wrap{max-width:640px;margin:0 auto;display:flex;flex-direction:column;gap:12px}
h1{font-size:20px;margin:0 0 6px}
a.card{display:block;background:#fff;border:1px solid #DCE3EA;border-radius:10px;
padding:15px 17px;text-decoration:none;color:#131A23}
.r{font-weight:700;font-size:16px}.m{color:#6C7E90;font-size:14px;margin-top:3px}
.b{display:inline-block;font-size:11px;letter-spacing:.08em;text-transform:uppercase;
padding:2px 8px;border-radius:99px;background:#E7EFF8;color:#1D4E89;margin-top:8px}
h2{font-size:13px;letter-spacing:.06em;text-transform:uppercase;margin:14px 0 0;color:#6C7E90}
h2 span{background:#DCE3EA;color:#131A23;border-radius:99px;padding:0 8px;font-size:12px;margin-left:6px}
a.card{border-left:5px solid #DCE3EA}
a.card.amber{border-left-color:#E0A526} .b.amber{background:#FBF1D6;color:#7A5A06} h2.amber{color:#9A6F0B}
a.card.blue{border-left-color:#3B82C4}  .b.blue{background:#E7EFF8;color:#1D4E89}
a.card.green{border-left-color:#2E9E5B} .b.green{background:#DDF3E5;color:#1B6B3A} h2.green{color:#237A46}
a.card.grey{border-left-color:#B9C2CB;opacity:.6} .b.grey{background:#EEF1F4;color:#5A6B7C}
a.card.purple{border-left-color:#7C4DBE} .b.purple{background:#EFE6FA;color:#4E2A86} h2.purple{color:#5E3A9E}
a.card.red{border-left-color:#D2453B} .b.red{background:#FBE3E1;color:#8A2119} h2.red{color:#A32A20}
.tbl{overflow-x:auto;background:#fff;border:1px solid #DCE3EA;border-radius:10px}
table.sub{border-collapse:collapse;width:100%;font-size:14px}
table.sub th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#6C7E90;
padding:8px 10px;border-bottom:1px solid #DCE3EA;white-space:nowrap}
table.sub td{padding:8px 10px;border-bottom:1px solid #EEF1F4;vertical-align:top}
table.sub tr:last-child td{border-bottom:0}
table.sub td.d{white-space:nowrap;color:#6C7E90}
table.sub a{color:inherit;text-decoration:none}
@media(prefers-color-scheme:dark){.tbl{background:#151D26;border-color:#243039}
table.sub th{border-color:#243039} table.sub td{border-color:#1C2630}}
"""



def questions_page(t):
    """/questions — every question a filing asked him (an emailed code, a Workday email
    verification) and every notice it left (a captcha) in the last 7 days: the waiting ones
    first, each with its answer box, then notices, then answered / expired ones."""
    from jobpilot.review import ask as ask_mod
    asked = ask_mod.recent(7)
    box = ('style="font-size:20px;letter-spacing:2px;padding:10px;width:100%;box-sizing:border-box;'
           'border:1px solid #bbb;border-radius:8px;margin-top:8px"')
    btn = ('style="margin-top:8px;padding:12px;width:100%;font-size:17px;border:0;border-radius:8px;'
           'background:#2a7;color:#fff"')

    def card(q):
        st = ask_mod.state(q)
        head = (f'<div class="r">{q.get("about") or "A filing"}</div>'
                f'<div class="m">{q.get("question","")}</div>'
                + (f'<div class="m">{q.get("hint")}</div>' if q.get("hint") else ""))
        when = (q.get("asked_at") or "")[5:16].replace("T", " ")
        if st == "waiting":
            return (f'<div class="card red">{head}'
                    + (f'<div class="m">⏳ waiting until {q.get("until","")[11:16]} — after that nothing is sent</div>'
                       if q.get("kind") != "open" else '<div class="m">⏳ waiting for your answer — nothing is held up</div>')
                    + f'<input class="ans" id="a-{q["key"]}" autocomplete="one-time-code" placeholder="type here" {box}>'
                    f'<button onclick="send(\'{q["key"]}\')" {btn}>Send</button>'
                    f'<div class="m" id="s-{q["key"]}"></div></div>')
        if st == "notice":
            return (f'<div class="card amber">{head}<div class="m">{when}'
                    + (f' · <a href="{q["link"]}">open the application</a>' if q.get("link") else "") + '</div></div>')
        tail = (f'answered {q.get("answered_at","")[11:16]}: <b>{q.get("answer")}</b>' if st == "answered"
                else "no answer in time — not sent; tried again on the next run")
        return f'<div class="card grey">{head}<div class="m">{when} · {tail}</div></div>'

    waiting = [q for q in asked if ask_mod.state(q) == "waiting"]
    rest = [q for q in asked if ask_mod.state(q) != "waiting"]
    return (f"<title>Questions from filing</title><meta name=viewport content=\"width=device-width,initial-scale=1\">"
            f"<style>{INDEX_CSS}</style><div class=\"wrap\"><h1>Questions from filing</h1>"
            f'<p class="m">What a filing needs from you right now, and what it asked before (last 7 days). '
            f'<a href="/{t}">Review list →</a></p>'
            + (f'<h2 class="red">Waiting for you <span>{len(waiting)}</span></h2>' + "".join(card(q) for q in waiting)
               if waiting else '<p class="m">Nothing is waiting on you now.</p>')
            + (f'<h2 class="grey">Earlier <span>{len(rest)}</span></h2>' + "".join(card(q) for q in rest) if rest else "")
            + "</div>"
            + f"<script>async function send(k){{const v=document.getElementById('a-'+k).value.trim();"
              f"if(!v)return;const r=await fetch('/ask/'+k+'/save{t}',{{method:'POST',"
              f"headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{answer:v}})}});"
              f"document.getElementById('s-'+k).textContent=r.ok?'Sent — the filing goes on.':'Failed: '+r.status;}}"
              f"</script>")

def companies_line():
    """One line for the list's Companies card: who is at the limit."""
    from jobpilot.core import quota as Q
    try:
        rows = Q.table()
    except Exception as e:
        return f"table unavailable: {type(e).__name__}"
    full = [r[0] for r in rows if r[4] == 0]
    return (f"{len(rows)} companies, {sum(r[1] for r in rows)} submitted · at the limit: "
            + (", ".join(full) if full else "none"))


def companies_page(t):
    """/companies — one row per company (core/quota.py): what was sent, sent inside its
    window, the limit, room now, when the next slot opens, cards still waiting."""
    from jobpilot.core import quota as Q
    rows = Q.table()
    cell = lambda v: "" if v is None else str(v)
    body = "".join(
        f'<tr{" class=full" if r[4] == 0 else ""}>' + "".join(
            f'<td{" class=d" if k else ""}>{cell(v)}</td>' for k, v in enumerate(r)) + "</tr>"
        for r in rows)
    return (f"<title>Companies</title><meta name=viewport content=\"width=device-width,initial-scale=1\">"
            f"<style>{INDEX_CSS} tr.full td{{background:#FBE3E1}} "
            f"@media(prefers-color-scheme:dark){{tr.full td{{background:#3A1E1B}}}}</style>"
            f'<div class="wrap"><h1>Companies</h1>'
            f'<p class="m">{companies_line()}. At most {cfg("apply.default_quota.max", 5)} applications per company in '
            f'{cfg("apply.default_quota.window_days", 30)} days (OpenAI: its portal\'s own limit). A company at the '
            f'limit gets no new tailoring, and its approved cards wait until the next slot. '
            f'<a href="/{t}">Review list →</a></p>'
            f'<div class="tbl"><table class="sub"><tr>' + "".join(f"<th>{c}</th>" for c in Q.COLUMNS)
            + f"</tr>{body}</table></div></div>")


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        print(f"  {self.address_string()} {fmt % a}")

    def _ok(self, body, ctype="text/html; charset=utf-8"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _err(self, code, msg):
        b = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _authed(self, q):
        return TOKEN is None or (q.get("t") or [""])[0] == TOKEN

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authed(q):
            return self._err(403, "bad or missing token")
        parts = [p for p in u.path.split("/") if p]

        if not parts:
            # Three things made the list hard to read on the phone: every card wore
            # the same blue pill whatever its state, approved items sat mixed in
            # with the rest, and going "back" from a card restored the page from
            # the browser's back-forward cache — so a role tapped Approve a second
            # ago still read "pending". Sections + colour + a reload on pageshow.
            t = f"?t={TOKEN}" if TOKEN else ""
            SECT = [("failed", "Failed — needs a look", "red"),
                    ("unconfirmed", "Sent? — the portal did not confirm: check your email", "amber"),
                    ("exploring", "Re-exploring — back when done", "grey"),
                    ("needs_input", "Review now — need your answers", "amber"),
                    ("manual", "Apply by hand — content ready to copy", "purple"),
                    ("pending", "Review now — ready to approve", "blue"),
                    ("later", "For later review — you kept these", "grey"),
                    ("submitting", "Filing now", "green"),
                    ("approved", "Approved — files on the next run", "green"),
                    ("submitted", "Submitted", "grey")]
            groups = {k: [] for k, _, _ in SECT}
            for i in items():
                st = i.get("status")
                # a role he tapped "keep for later" on waits in its own list, apart
                # from the ones still to look at for the first time
                if st in ("pending", "needs_input") and i.get("deferred_count"):
                    st = "later"
                if st in groups:
                    groups[st].append(i)
            def card(i, colour, key):
                nq = len([x for x in i.get("questions", []) if x.get("status") != "answered"])
                label = {"needs_input": f"{nq} to answer", "pending": "pending", "manual": "🖐 apply by hand",
                         "later": "kept for later" + (f" · {nq} to answer" if nq else ""),
                         "approved": "✅ approved" + (" · last try: " + i["fail_reason"][:40] if i.get("fail_reason") else ""),
                         "unconfirmed": "❓ may have been sent — never pressed again",
                         "submitted": "✋ submitted by hand" if i.get("submitted_via") == "manual" else "submitted",
                         "submitting": "⏳ filing now",
                         "exploring": "🔄 re-exploring",
                         "failed": ("⚠ exploration stopped short" if i.get("reached_end") is False
                                    else f"⚠ attempt {i.get('attempts', 1)} failed")}[key]
                link = (f'<a class="card {colour}" href="/a/{i["id"]}{t}">'
                        f'<div class="r">{i.get("role","?")}</div>'
                        f'<div class="m">{i.get("company","?")} · {i.get("location","")}</div>'
                        f'<div class="b {colour}">{label}</div></a>')
                if key not in ("failed", "unconfirmed"):
                    return link
                # a filing that did not go through: he may have sent it himself
                return (link + f'<label class="m" style="display:block;margin:-6px 0 14px 4px">'
                        f'<input type="checkbox" onchange="byHand(this, \'{i["id"]}\')"> I submitted this by hand</label>')
            counts = " · ".join(f"{len(groups[k])} {title.split(' —')[0].lower()}"
                                for k, title, _ in SECT if groups[k])
            sections = ""
            by_hand_js = (f"<script>async function byHand(box,id){{if(!box.checked)return;"
                          f"if(!confirm('Mark it as submitted by hand? It will never be filed again.')){{box.checked=false;return;}}"
                          f"const r=await fetch('/a/'+id+'/submit{t}',{{method:'POST',headers:{{'Content-Type':'application/json'}},"
                          f"body:JSON.stringify({{item:id,decision:'submitted'}})}});"
                          f"if(r.ok)location.reload();else{{box.checked=false;alert('Failed: '+r.status);}}}}"
                          f"async function after(sel,id){{const r=await fetch('/a/'+id+'/after{t}',{{method:'POST',"
                          f"headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{status:sel.value}})}});"
                          f"if(!r.ok)alert('Not saved: '+r.status);}}</script>")
            def submitted_table(rows):
                """What he applied to, newest first: the date, the company, the role, how, the
                people who could refer him for it (the role's own referral page), and what
                happened after (core/tracker.py: ⏰ once a follow-up is due and nothing is marked)."""
                from urllib.parse import quote
                from jobpilot.core import tracker
                from jobpilot.review import referral_form
                try:
                    people = referral_form.counts()
                except Exception:
                    people = {}
                key = lambda s: " ".join(str(s or "").lower().split())

                def refs(i):
                    n = people.get((key(i.get("company")), key(i.get("role"))), {}).get("people", 0)
                    if not n:
                        return "—"
                    url = f'/referrals{t}{"&" if t else "?"}company={quote(i.get("company",""))}&role={quote(i.get("role",""))}'
                    return f'<a href="{url}">{n} people →</a>'

                def referred(i):
                    c = people.get((key(i.get("company")), key(i.get("role"))))
                    if not c:
                        return "—"
                    if c["referred"]:
                        return "✅ " + ", ".join(c["referred"])
                    if c["asked"]:
                        return f'{c["asked"]} asked' + (f', {c["replied"]} replied' if c["replied"] else "")
                    return "not yet asked"

                def after(i):
                    cur = (i.get("after") or {}).get("status") or ""
                    day = tracker.due_date(i)
                    blank = "⏰ follow up" if not cur and day and day <= dt.date.today() else "—"
                    opts = [("", blank)] + list(tracker.AFTER.items())
                    return (f'<select onchange="after(this, \'{i["id"]}\')">'
                            + "".join(f'<option value="{v}"{" selected" if v == cur else ""}>{lab}</option>'
                                      for v, lab in opts) + "</select>")
                rows = sorted(rows, key=lambda i: i.get("submitted_at") or "", reverse=True)
                return ('<div class="tbl"><table class="sub"><tr><th>Date</th><th>Company</th><th>Role</th><th>How</th>'
                        '<th>Referrals</th><th>Referred?</th><th>After</th></tr>'
                        + "".join(
                            f'<tr><td class="d">{(i.get("submitted_at") or "")[:10]}</td>'
                            f'<td>{i.get("company","?")}</td>'
                            f'<td><a href="/a/{i["id"]}{t}">{i.get("role","?")}</a></td>'
                            f'<td class="d">{"✋ by hand" if i.get("submitted_via") == "manual" else "filed"}</td>'
                            f'<td class="d">{refs(i)}</td><td class="d">{referred(i)}</td>'
                            f'<td class="d">{after(i)}</td></tr>'
                            for i in rows)
                        + "</table></div>")

            def approved_table(rows):
                """What he approved and is not sent yet, newest first: the date, company, role,
                portal, and where its filing stands."""
                from jobpilot.apply.explore_agentic import calls as C

                def stands(i):
                    if not C.load(i.get("company_slug", ""))[1]:
                        return "explore first (no record)"
                    return ("last try: " + i["fail_reason"][:60]) if i.get("fail_reason") else "ready to file"
                rows = sorted(rows, key=lambda i: i.get("decided_at") or i.get("created") or "", reverse=True)
                return ('<div class="tbl"><table class="sub"><tr><th>Approved</th><th>Company</th><th>Role</th>'
                        '<th>Portal</th><th>Filing</th></tr>'
                        + "".join(
                            f'<tr><td class="d">{(i.get("decided_at") or "")[:10]}</td>'
                            f'<td>{i.get("company","?")}</td>'
                            f'<td><a href="/a/{i["id"]}{t}">{i.get("role","?")}</a></td>'
                            f'<td class="d">{i.get("portal") or ""}</td>'
                            f'<td class="m" style="margin:0">{stands(i)}</td></tr>'
                            for i in rows)
                        + "</table></div>")

            tables = {"submitted": submitted_table, "approved": approved_table}
            for k, title, colour in SECT:
                if not groups[k]:
                    continue
                sections += (f'<h2 class="{colour}">{title} <span>{len(groups[k])}</span></h2>'
                             + (tables[k](groups[k]) if k in tables
                                else "".join(card(i, colour, k) for i in groups[k])))
            body = (f"<title>Applications</title><meta name=viewport content=\"width=device-width,initial-scale=1\">"
                    f"<style>{INDEX_CSS}</style>"
                    f'<div class="wrap"><h1>Applications to review</h1>'
                    f'<p class="m">{counts or "Queue is empty."}</p>'
                    + sections + by_hand_js
                    + f'<a class="card" href="/add{t}">'
                      f'<div class="r">Add job links →</div>'
                      f'<div class="m">Paste company-site apply links you found; they go straight to tailoring</div></a>'
                    + f'<a class="card" href="/companies{t}">'
                      f'<div class="r">Companies →</div>'
                      f'<div class="m">{companies_line()}</div></a>'
                    + f'<a class="card" href="/referrals{t}">'
                      f'<div class="r">Referral queue →</div>'
                      f'<div class="m">People to contact, messages ready to copy</div></a>'
                    + "</div>"
                    # bfcache: a page restored by the back button never re-fetched.
                    + "<script>addEventListener('pageshow',e=>{if(e.persisted)location.reload()});</script>")
            return self._ok(body)

        if parts[0] == "a" and len(parts) == 2:
            p = item_path(parts[1])
            if not os.path.isfile(p):
                return self._err(404, "no such application")
            item = json.load(open(p))
            if item.get("manual"):
                from jobpilot.review import manual_form
                return self._ok(manual_form.build(item))
            return self._ok(form_mod.build(item))

        # resume files for an apply-by-hand item: /file/<id>/docx|pdf
        if parts[0] == "file" and len(parts) == 3 and parts[2] in ("docx", "pdf"):
            p = item_path(parts[1])
            if not os.path.isfile(p):
                return self._err(404, "no such application")
            item = json.load(open(p))
            fp = item.get("resume_pdf") if parts[2] == "pdf" else item.get("resume")
            if not fp or not os.path.isfile(fp) or not os.path.abspath(fp).startswith(os.path.abspath(APPLICATIONS)):
                return self._err(404, "file not built")
            ctype = ("application/pdf" if parts[2] == "pdf"
                     else "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_response(200); self.send_header("Content-Type", ctype)
            self.send_header("Content-Disposition", f'attachment; filename="sunil_resume.{parts[2]}"')
            body = open(fp, "rb").read(); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return

        if parts[0] == "referrals":
            from jobpilot.review import referral_form
            co = (q.get("company") or [None])[0]
            return self._ok(referral_form.build(co, (q.get("role") or [None])[0]))

        if parts[0] == "keywords" and len(parts) == 1:
            from jobpilot.review import keyword_form
            return self._ok(keyword_form.build())

        # links Sunil found himself: paste, fetch the JD, straight to tailoring
        if parts[0] == "add" and len(parts) == 1:
            from jobpilot.discover import inbox
            t = f"?t={TOKEN}" if TOKEN else ""
            recent = "".join(
                f'<div class="fld"><dt>{st}</dt><dd>{co} — {ti[:60]}</dd></div>'
                for co, ti, loc, st, seen in inbox.pending()[:12])
            body = (f"<title>Add job links</title><meta name=viewport content=\"width=device-width,initial-scale=1\">"
                    f"<style>{INDEX_CSS} textarea{{width:100%;box-sizing:border-box;min-height:160px;font-size:15px;"
                    f"padding:10px;border:1px solid #bbb;border-radius:8px}} button.go{{margin-top:10px;padding:14px;"
                    f"width:100%;font-size:18px;border:0;border-radius:8px;background:#2a7;color:#fff}} "
                    f".fld{{display:flex;gap:10px;font-size:14px;padding:4px 0}} .fld dt{{color:#6C7E90;min-width:70px}} "
                    f"label.ck{{display:block;margin-top:10px;font-size:15px}} #out{{white-space:pre-wrap;font-size:14px}}</style>"
                    f'<div class="wrap"><h1>Add job links</h1>'
                    f'<p class="m">One company-site apply link per line (Greenhouse, Lever, Ashby, Workday or the '
                    f'employer\'s own page — not LinkedIn). Each is fetched, marked reviewed and queued for tailoring '
                    f'ahead of everything the scanner found.</p>'
                    f'<textarea id="urls" placeholder="https://…"></textarea>'
                    f'<label class="ck"><input type="checkbox" id="now" checked> Start tailoring now (otherwise the next pipeline run picks them up)</label>'
                    f'<button class="go" onclick="go()">Add</button><p id="out" class="m"></p>'
                    f'<h2>Recent</h2>{recent or "<p class=m>none yet</p>"}'
                    f'<a href="/{t}">← Back to the list</a></div>'
                    f"<script>async function go(){{const u=document.getElementById('urls').value.split('\\n').map(s=>s.trim()).filter(Boolean);"
                    f"if(!u.length)return;const o=document.getElementById('out');o.textContent='Fetching…';"
                    f"const r=await fetch('/add/save{t}',{{method:'POST',headers:{{'Content-Type':'application/json'}},"
                    f"body:JSON.stringify({{urls:u,now:document.getElementById('now').checked}})}});"
                    f"const j=await r.json();o.textContent=(j.results||[]).map(x=>x.ok?('✅ '+x.company+' — '+x.title+' ('+x.portal+', '+x.market+')'+(x.note?'  ⚠ '+x.note:'')):('❌ '+x.url.slice(0,60)+': '+x.why)).join('\\n')"
                    f"+(j.started?'\\n\\nTailoring started — items appear in the list as they are ready.':'');}}</script>")
            return self._ok(body)

        if parts == ["questions"]:
            return self._ok(questions_page(f"?t={TOKEN}" if TOKEN else ""))

        if parts == ["companies"]:
            return self._ok(companies_page(f"?t={TOKEN}" if TOKEN else ""))

        # a mid-run question from the submitter (e.g. an emailed verification code)
        if parts[0] == "ask" and len(parts) == 2:
            from jobpilot.review import ask as ask_mod
            qd = ask_mod.load(parts[1])
            if not qd:
                return self._err(404, "no such question")
            t = f"?t={TOKEN}" if TOKEN else ""
            done = qd.get("answer")
            body = (f"<title>Question</title><meta name=viewport content=\"width=device-width,initial-scale=1\">"
                    f"<style>{INDEX_CSS} input.ans{{font-size:22px;letter-spacing:2px;padding:12px;width:100%;"
                    f"box-sizing:border-box;border:1px solid #bbb;border-radius:8px}} button.go{{margin-top:12px;"
                    f"padding:14px;width:100%;font-size:18px;border:0;border-radius:8px;background:#2a7;color:#fff}}</style>"
                    f'<div class="wrap"><h1>One thing needed</h1>'
                    f'<p>{qd.get("question","")}</p><p class="m">{qd.get("hint","")}</p>'
                    + (f'<p class="m">Already answered: <b>{done}</b></p>' if done else "")
                    + f'<input class="ans" id="ans" autocomplete="one-time-code" autofocus placeholder="type here">'
                    f'<button class="go" onclick="save()">Send</button><p id="st" class="m"></p>'
                    f'<a href="/{t}">← Back to the list</a></div>'
                    f"<script>async function save(){{const v=document.getElementById('ans').value.trim();"
                    f"if(!v)return;const r=await fetch('/ask/{parts[1]}/save{t}',{{method:'POST',"
                    f"headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{answer:v}})}});"
                    f"document.getElementById('st').textContent=r.ok?'Sent — the browser will continue.':'Failed: '+r.status;}}"
                    f"</script>")
            return self._ok(body)

        if parts[0] == "shot" and len(parts) == 2:
            p = CD.shot(parts[1]) or ""
            if not os.path.isfile(p):
                return self._err(404, "no screenshot")
            return self._ok(open(p, "rb").read(), "image/png")

        return self._err(404, "not found")

    def _undo(self, item_id, p):
        """The card's Undo: its last decision taken back — status, answers, the
        record's placeholders and what was learned from it. Not once it is being
        filed or was filed."""
        from jobpilot.apply.draft import learn as L
        from jobpilot.apply.explore_agentic import record as R
        with LOCK:
            item = json.load(open(p))
            u = item.get("undo")
            if item.get("submitted_at") or item.get("status") in ("submitted", "submitting", "exploring"):
                return self._err(409, f"cannot undo: the application is {item.get('status')}")
            if not u:
                return self._err(409, "nothing to undo")
            for k in ("status", "questions", "fields", "deferred_count", "decided_at"):
                if u.get(k) is None:
                    item.pop(k, None)
                else:
                    item[k] = u[k]
            item.pop("undo", None)
            json.dump(item, open(p, "w"), indent=2)
            sub = CD.submission(item_id)
            if os.path.isfile(sub):
                os.remove(sub)
        try:
            doc = R.load(item["company_slug"])
            for q, a in (u.get("placeholders") or {}).items():
                if q in (doc.get("placeholders") or {}):
                    doc["placeholders"][q]["answer"] = a
                    if a is None:
                        doc["placeholders"][q].pop("answered_at", None)
            R.save(item["company_slug"], doc)
        except Exception as e:
            print(f"  [warn] undo: record not restored: {e}")
        try:
            if u.get("learned"):
                L.restore(u["learned"])
        except Exception as e:
            print(f"  [warn] undo: learned answers not restored: {e}")
        print(f"  [undo] {item_id} -> {item.get('status')}")
        return self._ok(json.dumps({"ok": True, "status": item.get("status")}), "application/json")

    def do_POST(self):
        u = urlparse(self.path)
        if not self._authed(parse_qs(u.query)):
            return self._err(403, "bad or missing token")
        parts = [p for p in u.path.split("/") if p]

        # keyword ticks -> targets.yaml `interest:` (ranking only)
        if parts[:2] == ["keywords", "save"]:
            n = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._err(400, "bad json")
            try:
                from jobpilot.rank import keyword_learn
                with LOCK:
                    interest = keyword_learn.add_interest((payload.get("interest") or []) + (payload.get("add") or []))
                    done = keyword_learn.add_confirmed(payload.get("done") or [])
                    keyword_learn.dismiss(payload.get("dismiss") or [])
                print(f"  [keywords] interest {interest}, done {done}, dismissed {payload.get('dismiss')}")
                return self._ok(json.dumps({"ok": True, "interest": interest, "done": done,
                                            "dismissed": payload.get("dismiss") or []}), "application/json")
            except Exception as e:
                return self._ok(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}), "application/json")

        if len(parts) == 3 and parts[0] == "ask" and parts[2] == "save":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._err(400, "bad json")
            from jobpilot.review import ask as ask_mod
            with LOCK:
                q = ask_mod.answer(parts[1], payload.get("answer", ""))
                if q.get("learned"):                     # a stored answer in conflict: apply his choice
                    from jobpilot.apply.draft import learn as L
                    print(f"  [learned] {parts[1]}: {L.resolve(q)}")
            print(f"  [ask] {parts[1]} answered")
            return self._ok(json.dumps({"ok": True}), "application/json")

        if parts[:2] == ["add", "save"]:
            n = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._err(400, "bad json")
            from jobpilot.discover import inbox
            results = []
            with LOCK:
                for u in (payload.get("urls") or [])[:30]:
                    try:
                        results.append(inbox.add(u))
                    except Exception as e:
                        results.append({"url": u, "ok": False, "why": f"{type(e).__name__}: {e}"[:120]})
            started = False
            if payload.get("now") and any(r.get("ok") for r in results):
                # Detached: the tailor run outlives this request; results arrive
                # as review items (and Telegram) exactly like a scheduled run.
                try:
                    subprocess.Popen([sys.executable, "-m", "jobpilot.tailor.autotailor", "--inbox",
                                      "--limit", str(len([r for r in results if r.get("ok")]))],
                                     cwd=TOOL, stdout=open(os.path.join(LOGS, "inbox-tailor.log"), "a"),
                                     stderr=subprocess.STDOUT, start_new_session=True)
                    started = True
                except Exception as e:
                    print(f"  [inbox] could not start autotailor: {e}")
            print(f"  [inbox] {len([r for r in results if r.get('ok')])}/{len(results)} added"
                  + (", tailoring started" if started else ""))
            return self._ok(json.dumps({"results": results, "started": started}), "application/json")

        # referral status updates save straight into the referrals table (data/state.db)
        if parts[:2] == ["referrals", "status"]:
            n = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._err(400, "bad json")
            try:
                from jobpilot.outreach import referral_tracker
                with LOCK:
                    referral_tracker.set_status(int(payload["row"]), payload["status"])
                print(f"  [referral] row {payload['row']} -> {payload['status']}")
                return self._ok(json.dumps({"ok": True}), "application/json")
            except Exception as e:
                return self._err(400, f"{type(e).__name__}: {e}")

        # what happened after a submitted application went (core/tracker.py): ends its follow-up
        if len(parts) == 3 and parts[0] == "a" and parts[2] == "after":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._err(400, "bad json")
            if not os.path.isfile(item_path(parts[1])):
                return self._err(404, "no such application")
            from jobpilot.core import tracker
            try:
                with LOCK:
                    tracker.mark(parts[1], payload.get("status") or "")
            except ValueError as e:
                return self._err(400, str(e))
            print(f"  [after] {parts[1]} -> {payload.get('status') or '(cleared)'}")
            return self._ok(json.dumps({"ok": True}), "application/json")

        if not (len(parts) == 3 and parts[0] == "a" and parts[2] == "submit"):
            return self._err(404, "not found")

        n = int(self.headers.get("Content-Length") or 0)
        if n > 512_000:
            return self._err(413, "too large")
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._err(400, "bad json")

        item_id = parts[1]
        p = item_path(item_id)
        if not os.path.isfile(p):
            return self._err(404, "no such application")
        if payload.get("decision") == "undo":
            return self._undo(item_id, p)

        with LOCK:
            item = json.load(open(p))
            decision = payload.get("decision")
            # "submitted" is for apply-by-hand items and for filings that did not go through
            # (failed, unconfirmed): Sunil filed it himself.
            by_hand = item.get("manual") or item.get("status") in ("failed", "unconfirmed")
            allowed = ("approved", "deferred", "rejected") + (("submitted",) if by_hand else ())
            if decision not in allowed:
                return self._err(400, "bad decision")
            # An application that has actually been filed is final. A tap on its
            # card once flipped it back to 'approved', which would have made
            # submit-approved file it a second time — and spend a second OpenAI slot.
            if item.get("status") == "submitted" or item.get("submitted_at"):
                return self._err(409, f"already submitted on {str(item.get('submitted_at',''))[:10]} — no changes accepted")
            if item.get("status") == "submitting":
                return self._err(409, "being filed right now — no changes accepted")
            # what this decision changes, kept so the card's Undo can put it back
            before = {k: copy.deepcopy(item.get(k)) for k in ("status", "questions", "fields", "deferred_count", "decided_at")}
            for a in payload.get("answers", []):
                for qq in item.get("questions", []):
                    if qq["qid"] == a.get("qid") and (a.get("text") or "").strip():
                        qq["selected"] = a["text"]
                        qq["status"] = "answered"
                        qq["answered_via"] = a.get("kind", "form")
                        item.setdefault("fields", {})[qq["label"][:80]] = a["text"]
            # "deferred" goes back to pending on purpose: an unreviewed role must
            # survive to the next session. Only an explicit reject discards.
            item["status"] = {"approved": "approved",
                              # kept for later: still unanswered questions stay asked
                              "deferred": item["status"] if item.get("status") in ("pending", "needs_input") else "pending",
                              "rejected": "rejected", "submitted": "submitted"}[decision]
            # An exploration that stopped short has no route to replay. Approving
            # it (three Workday items, 2026-09-25) only queued a submit that could
            # fail the same way; the tap means "go again with these answers".
            reexplore = decision == "approved" and item.get("reached_end") is False and not item.get("manual")
            if reexplore:
                item["status"] = "exploring"
            if decision == "submitted":
                item["submitted_at"] = dt.datetime.now().isoformat(timespec="seconds")
                item["submitted_via"] = "manual"
                try:                                     # the record says so too: never filed again
                    from jobpilot.apply.explore_agentic import record as R
                    R.note_attempt(item["company_slug"], "submitted", why="submitted by hand (marked on the review list)")
                except Exception as e:
                    print(f"  [warn] record not updated: {e}")
            elif item.get("manual") and decision == "deferred":
                item["status"] = "manual"          # stays in the apply-by-hand section
            item["decided_at"] = dt.datetime.now().isoformat(timespec="seconds")
            if decision == "deferred":
                item["deferred_count"] = (item.get("deferred_count") or 0) + 1
            json.dump(item, open(p, "w"), indent=2)

            sub = CD.submission(item_id)
            # which employer portal the answers were given on (learn.py keeps a question naming
            # the employer, and a stand-in for a stored fact its list lacks, for that portal only)
            from jobpilot.apply import platforms
            tenant = item.get("tenant") or platforms.tenant(item.get("portal"), item.get("url"))
            payload.update(tenant=tenant, company=payload.get("company") or item.get("company"),
                           decided_at=item.get("decided_at"))
            facts_of = {q.get("qid"): q.get("fact") for q in item.get("questions", []) if q.get("fact")}
            lists = {q.get("qid") for q in item.get("questions", []) if q.get("kind") == "select"}
            for a in payload.get("answers", []):
                if facts_of.get(a.get("qid")):
                    a["stand_in_for"] = facts_of[a.get("qid")]
                if a.get("qid") in lists:                    # a pick from the form's list: kept for its portal
                    a["choice"] = True
            json.dump(payload, open(sub, "w"), indent=2)

        # Write his answers into the application's record (explore.json) straight
        # away, so the submit replays the real values, not the placeholders.
        from jobpilot.apply.draft import learn as L
        from jobpilot.apply.explore_agentic import record as R
        try:
            rec_before = {q: v.get("answer") for q, v in
                          (R.load(item["company_slug"]).get("placeholders") or {}).items()}
        except Exception:
            rec_before = {}
        learned_before = L.snapshot(tenant)
        try:
            answered = {q["label"]: q["selected"] for q in item.get("questions", [])
                        if q.get("status") == "answered" and (q.get("selected") or "").strip()}
            n = R.apply_answers(item, answered)
            if answered:
                print(f"  [record] {len(answered)} answer(s) given, {n} placeholder(s) answered")
        except Exception as e:
            print(f"  [warn] replay.json not updated: {e}")

        # Keep his list picks for this portal (learn.py) so its next forms need not ask them.
        try:
            subprocess.run([sys.executable, "-m", "jobpilot.apply.draft.learn", sub],
                           check=False, timeout=30)
        except Exception as e:
            print(f"  [warn] learn.py failed: {e}")

        learned_after = L.snapshot(tenant)
        if decision != "submitted" and not reexplore:
            with LOCK:
                cur = json.load(open(p))
                cur["undo"] = {**before, "placeholders": rec_before,
                               "learned": [[m, learned_before.get(m)] for m, e in learned_after.items()
                                           if learned_before.get(m) != e]}
                json.dump(cur, open(p, "w"), indent=2)

        if reexplore:
            try:
                subprocess.Popen([sys.executable, "-m", "jobpilot.apply.explore_agentic", item["company_slug"]],
                                 cwd=TOOL, stdout=open(os.path.join(LOGS, "reexplore.log"), "a"),
                                 stderr=subprocess.STDOUT, start_new_session=True)
                print(f"  [re-explore] {item_id} — exploration started")
            except Exception as e:
                print(f"  [warn] could not start the re-exploration: {e}")
            return self._ok(json.dumps({"ok": True, "reexplore": True}), "application/json")

        print(f"  [{decision}] {item_id}")

        # no Telegram message for a decision: the card itself says it was saved, and one
        # message per tap buried the review-list link in the chat

        return self._ok(json.dumps({"ok": True}), "application/json")


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def tailscale_host():
    try:
        out = subprocess.run(TAILSCALE + ["status", "--json"],
                             capture_output=True, timeout=5).stdout
        return json.loads(out).get("Self", {}).get("DNSName", "").rstrip(".") or None
    except Exception:
        return None


def main():
    global TOKEN
    # Python block-buffers stdout when redirected, so a backgrounded server would
    # print its URLs nowhere. Same fix as bot.py.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=cfg("server.port", 8765))
    ap.add_argument("--no-token", action="store_true")
    a = ap.parse_args()
    # A token that changes every restart invalidates the link on your phone and
    # every bookmark. Persist it in .env instead.
    if a.no_token:
        TOKEN = None
    else:
        env_tok = None
        if os.path.isfile(TG_ENV):
            for line in open(TG_ENV):
                if line.strip().startswith("FORM_TOKEN="):
                    env_tok = line.split("=", 1)[1].strip()
        if not env_tok:
            env_tok = secrets.token_urlsafe(9)
            fd = os.open(TG_ENV, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as f:
                f.write(f"FORM_TOKEN={env_tok}\n")
            os.chmod(TG_ENV, 0o600)
            print(f"  [token] generated and saved to {TG_ENV}")
        TOKEN = env_tok
    t = f"?t={TOKEN}" if TOKEN else ""

    print(f"\n  Review forms — no login, served from this machine\n")
    print(f"    this machine   http://localhost:{a.port}/{t}")
    print(f"    same wifi      http://{lan_ip()}:{a.port}/{t}")
    ts = tailscale_host()
    if ts:
        print(f"    anywhere       http://{ts}:{a.port}/{t}")
    else:
        print(f"    anywhere       install Tailscale on laptop + phone, then "
              f"http://<tailscale-name>:{a.port}/{t}")
    print(f"\n  {len(items())} application(s) in the queue. Ctrl-C to stop.\n")
    ThreadingHTTPServer(("0.0.0.0", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
