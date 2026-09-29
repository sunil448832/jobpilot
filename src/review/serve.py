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

A submitted form updates the queue item AND folds its answers into learned.yaml,
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

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)
from jobpilot.core.config import cfg  # noqa: E402
QUEUE_DIR = os.path.join(DATA, "queue")
from jobpilot.review import form as form_mod                                    # noqa: E402

TOKEN = None
LOCK = threading.Lock()
TG_ENV = os.path.expanduser("~/.config/jobbot/env")


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
    out = []
    for p in sorted(glob.glob(os.path.join(QUEUE_DIR, "*.json"))):
        if os.path.basename(p).startswith("_"):
            continue
        try:
            out.append(json.load(open(p)))
        except json.JSONDecodeError:
            pass
    return out


def item_path(i):
    return os.path.join(QUEUE_DIR, i + ".json")


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
"""


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
                         "submitted": "submitted", "submitting": "⏳ filing now",
                         "exploring": "🔄 re-exploring",
                         "failed": ("⚠ exploration stopped short" if i.get("reached_end") is False
                                    else f"⚠ attempt {i.get('attempts', 1)} failed")}[key]
                return (f'<a class="card {colour}" href="/a/{i["id"]}{t}">'
                        f'<div class="r">{i.get("role","?")}</div>'
                        f'<div class="m">{i.get("company","?")} · {i.get("location","")}</div>'
                        f'<div class="b {colour}">{label}</div></a>')
            counts = " · ".join(f"{len(groups[k])} {title.split(' —')[0].lower()}"
                                for k, title, _ in SECT if groups[k])
            # what a filing is waiting on right now (an emailed code, a verification): answered
            # here, on the list, without opening another page
            from jobpilot.review import ask as ask_mod
            now_asks = ask_mod.open_asks()
            sections = ""
            if now_asks:
                sections += (f'<h2 class="red" id="now">Needs you now — a filing is waiting <span>{len(now_asks)}</span></h2>'
                             + "".join(
                                 f'<div class="card red"><div class="r">{q.get("about") or "A filing"}</div>'
                                 f'<div class="m">{q.get("question","")}</div>'
                                 + (f'<div class="m">{q.get("hint")}</div>' if q.get("hint") else "")
                                 + f'<div class="m">⏳ waiting until {q.get("until","")[11:16]} — after that nothing is sent</div>'
                                 f'<input class="ans" id="a-{q["key"]}" autocomplete="one-time-code" placeholder="type here" '
                                 f'style="font-size:20px;letter-spacing:2px;padding:10px;width:100%;box-sizing:border-box;'
                                 f'border:1px solid #bbb;border-radius:8px;margin-top:8px">'
                                 f'<button onclick="send(\'{q["key"]}\')" style="margin-top:8px;padding:12px;width:100%;'
                                 f'font-size:17px;border:0;border-radius:8px;background:#2a7;color:#fff">Send</button>'
                                 f'<div class="m" id="s-{q["key"]}"></div></div>' for q in now_asks)
                             + f"<script>async function send(k){{const v=document.getElementById('a-'+k).value.trim();"
                               f"if(!v)return;const r=await fetch('/ask/'+k+'/save{t}',{{method:'POST',"
                               f"headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{answer:v}})}});"
                               f"document.getElementById('s-'+k).textContent=r.ok?'Sent — the filing goes on.':'Failed: '+r.status;}}"
                               f"</script>")
            for k, title, colour in SECT:
                if not groups[k]:
                    continue
                sections += (f'<h2 class="{colour}">{title} <span>{len(groups[k])}</span></h2>'
                             + "".join(card(i, colour, k) for i in groups[k]))
            body = (f"<title>Applications</title><meta name=viewport content=\"width=device-width,initial-scale=1\">"
                    f"<style>{INDEX_CSS}</style>"
                    f'<div class="wrap"><h1>Applications to review</h1>'
                    f'<p class="m">{counts or "Queue is empty."}</p>'
                    + sections
                    + f'<a class="card" href="/add{t}">'
                      f'<div class="r">Add job links →</div>'
                      f'<div class="m">Paste company-site apply links you found; they go straight to tailoring</div></a>'
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
            return self._ok(referral_form.build(co))

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
            p = os.path.join(QUEUE_DIR, parts[1] + ".png")
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
            sub = os.path.join(QUEUE_DIR, f"_submission-{item_id}.json")
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
            print(f"  [warn] undo: learned.yaml not restored: {e}")
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
                ask_mod.answer(parts[1], payload.get("answer", ""))
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
                                     cwd=TOOL, stdout=open(os.path.join(DATA, "inbox-tailor.log"), "a"),
                                     stderr=subprocess.STDOUT, start_new_session=True)
                    started = True
                except Exception as e:
                    print(f"  [inbox] could not start autotailor: {e}")
            print(f"  [inbox] {len([r for r in results if r.get('ok')])}/{len(results)} added"
                  + (", tailoring started" if started else ""))
            return self._ok(json.dumps({"results": results, "started": started}), "application/json")

        # referral status updates write straight into the tracker sheet
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
            # "submitted" is only for apply-by-hand items: Sunil filed it himself.
            allowed = ("approved", "deferred", "rejected") + (("submitted",) if item.get("manual") else ())
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
            elif item.get("manual") and decision == "deferred":
                item["status"] = "manual"          # stays in the apply-by-hand section
            item["decided_at"] = dt.datetime.now().isoformat(timespec="seconds")
            if decision == "deferred":
                item["deferred_count"] = (item.get("deferred_count") or 0) + 1
            json.dump(item, open(p, "w"), indent=2)

            sub = os.path.join(QUEUE_DIR, f"_submission-{item_id}.json")
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
        learned_before = L.snapshot()
        try:
            answered = {q["label"]: q["selected"] for q in item.get("questions", [])
                        if q.get("status") == "answered" and (q.get("selected") or "").strip()}
            n = R.apply_answers(item, answered)
            if answered:
                print(f"  [record] {len(answered)} answer(s) given, {n} placeholder(s) answered")
        except Exception as e:
            print(f"  [warn] replay.json not updated: {e}")

        # Fold the answers into learned.yaml so they are never asked again.
        try:
            subprocess.run([sys.executable, "-m", "jobpilot.apply.draft.learn", sub],
                           check=False, timeout=30)
        except Exception as e:
            print(f"  [warn] learn.py failed: {e}")

        learned_after = L.snapshot()
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
                                 cwd=TOOL, stdout=open(os.path.join(DATA, "reexplore.log"), "a"),
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
        out = subprocess.run(["tailscale", "status", "--json"],
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
    # every bookmark. Persist it in ~/.config/jobbot/env instead.
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
            os.makedirs(os.path.dirname(TG_ENV), exist_ok=True)
            with open(TG_ENV, "a") as f:
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
