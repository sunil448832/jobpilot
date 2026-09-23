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
            SECT = [("needs_input", "Need your answers", "amber"),
                    ("pending", "Ready to review", "blue"),
                    ("approved", "Approved — waiting for submit", "green"),
                    ("submitted", "Submitted", "grey")]
            groups = {k: [] for k, _, _ in SECT}
            for i in items():
                st = i.get("status")
                if st in groups:
                    groups[st].append(i)
            def card(i, colour):
                nq = len([x for x in i.get("questions", []) if x.get("status") != "answered"])
                label = {"needs_input": f"{nq} to answer", "pending": "pending",
                         "approved": "✅ approved", "submitted": "submitted"}[i["status"]]
                return (f'<a class="card {colour}" href="/a/{i["id"]}{t}">'
                        f'<div class="r">{i.get("role","?")}</div>'
                        f'<div class="m">{i.get("company","?")} · {i.get("location","")}</div>'
                        f'<div class="b {colour}">{label}</div></a>')
            counts = " · ".join(f"{len(groups[k])} {title.split(' —')[0].lower()}"
                                for k, title, _ in SECT if groups[k])
            sections = ""
            for k, title, colour in SECT:
                if not groups[k]:
                    continue
                sections += (f'<h2 class="{colour}">{title} <span>{len(groups[k])}</span></h2>'
                             + "".join(card(i, colour) for i in groups[k]))
            body = (f"<title>Applications</title><meta name=viewport content=\"width=device-width,initial-scale=1\">"
                    f"<style>{INDEX_CSS}</style>"
                    f'<div class="wrap"><h1>Applications to review</h1>'
                    f'<p class="m">{counts or "Queue is empty."}</p>'
                    + sections
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
            return self._ok(form_mod.build(json.load(open(p))))

        if parts[0] == "referrals":
            from jobpilot.review import referral_form
            co = (q.get("company") or [None])[0]
            return self._ok(referral_form.build(co))

        if parts[0] == "keywords" and len(parts) == 1:
            from jobpilot.review import keyword_form
            return self._ok(keyword_form.build())

        if parts[0] == "shot" and len(parts) == 2:
            p = os.path.join(QUEUE_DIR, parts[1] + ".png")
            if not os.path.isfile(p):
                return self._err(404, "no screenshot")
            return self._ok(open(p, "rb").read(), "image/png")

        return self._err(404, "not found")

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

        with LOCK:
            item = json.load(open(p))
            decision = payload.get("decision")
            if decision not in ("approved", "deferred", "rejected"):
                return self._err(400, "bad decision")
            for a in payload.get("answers", []):
                for qq in item.get("questions", []):
                    if qq["qid"] == a.get("qid") and (a.get("text") or "").strip():
                        qq["selected"] = a["text"]
                        qq["status"] = "answered"
                        qq["answered_via"] = a.get("kind", "form")
                        item.setdefault("fields", {})[qq["label"][:80]] = a["text"]
            # "deferred" goes back to pending on purpose: an unreviewed role must
            # survive to the next session. Only an explicit reject discards.
            item["status"] = {"approved": "approved", "deferred": "pending",
                              "rejected": "rejected"}[decision]
            item["decided_at"] = dt.datetime.now().isoformat(timespec="seconds")
            if decision == "deferred":
                item["deferred_count"] = (item.get("deferred_count") or 0) + 1
            json.dump(item, open(p, "w"), indent=2)

            sub = os.path.join(QUEUE_DIR, f"_submission-{item_id}.json")
            json.dump(payload, open(sub, "w"), indent=2)

        # Fold the answers into learned.yaml so they are never asked again.
        try:
            subprocess.run([sys.executable, "-m", "jobpilot.fill.learn", sub],
                           check=False, timeout=30)
        except Exception as e:
            print(f"  [warn] learn.py failed: {e}")

        print(f"  [{decision}] {item_id}")

        n_ans = len([a for a in payload.get("answers", []) if (a.get("text") or "").strip()])
        icon = {"approved": "✅", "deferred": "🕒", "rejected": "🚫"}[decision]
        word = {"approved": "APPROVED", "deferred": "KEPT FOR LATER",
                "rejected": "REJECTED"}[decision]
        body = (f"{icon} <b>{word}</b>\n\n"
                f"<b>{item.get('role','?')}</b>\n"
                f"{item.get('company','?')} · {item.get('location','')}\n\n"
                f"asking {item.get('salary_quoted','n/a')}\n"
                f"{len(item.get('fields', {}))} fields"
                + (f" · {n_ans} answers saved" if n_ans else "")
                + f"\n\n<i>{item_id}</i>\n")
        body += {"approved": "Nothing is sent yet — run submit-approved to file it.",
                 "deferred": "Still in the queue. It will be waiting next time.",
                 "rejected": "Dropped. It will not be shown again."}[decision]
        threading.Thread(target=telegram, args=(body,), daemon=True).start()

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
