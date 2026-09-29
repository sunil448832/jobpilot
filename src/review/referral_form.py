#!/usr/bin/env python3
"""
referral_form.py — render the referral tracker as one page.

A dozen Telegram messages per application is unusable on a phone. This puts every
candidate for every open application on a single page served over Tailscale:
tap to copy the message, tap to open the profile, tap to set the status — which
writes straight back to tracking/referral-tracker.xlsx.

Usage:
    python jobs/referral_form.py            # write jobs/queue/_referrals.html
    python jobs/referral_form.py --company Openai
"""
import argparse
import html
import json
import os
import sys

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)

TEMPLATE = r"""<title>Referral Queue</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=Source+Sans+3:wght@400;600&family=JetBrains+Mono:wght@400&display=swap">
<style>
:root{
  --ground:#F3F6F9; --surface:#FFFFFF; --sunk:#EDF1F5;
  --ink:#131A23; --ink-2:#3F4E5E; --muted:#6C7E90;
  --line:#DCE3EA; --line-2:#C6D0DA;
  --accent:#1D4E89; --accent-soft:#E7EFF8;
  --warm:#9A5B08; --warm-soft:#FBF0DF;
  --ok:#15803D; --ok-soft:#E6F4EA;
  --shadow:0 1px 2px rgba(19,26,35,.06),0 8px 24px -12px rgba(19,26,35,.18);
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --ground:#0D1218; --surface:#151D26; --sunk:#111820;
  --ink:#E6EDF4; --ink-2:#B0BFCD; --muted:#7E90A2;
  --line:#243039; --line-2:#31404C;
  --accent:#82ABDF; --accent-soft:#182635;
  --warm:#DDA25E; --warm-soft:#251C10;
  --ok:#5FBF80; --ok-soft:#14251A;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6);
}}
:root[data-theme="dark"]{
  --ground:#0D1218; --surface:#151D26; --sunk:#111820;
  --ink:#E6EDF4; --ink-2:#B0BFCD; --muted:#7E90A2;
  --line:#243039; --line-2:#31404C;
  --accent:#82ABDF; --accent-soft:#182635;
  --warm:#DDA25E; --warm-soft:#251C10;
  --ok:#5FBF80; --ok-soft:#14251A;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6);
}
*{box-sizing:border-box}
body{background:var(--ground);color:var(--ink);margin:0;padding:20px 14px 60px;
  font-family:"Source Sans 3",ui-sans-serif,system-ui,sans-serif;font-size:16px;line-height:1.5;
  -webkit-text-size-adjust:100%}
.wrap{max-width:680px;margin:0 auto;display:flex;flex-direction:column;gap:16px}
h1{font-family:"Bricolage Grotesque",sans-serif;font-size:22px;margin:0;letter-spacing:-.015em}
.sub{color:var(--muted);font-size:14px;margin-top:3px}
.grp{display:flex;flex-direction:column;gap:10px}
.grp h2{font-family:"Bricolage Grotesque",sans-serif;font-size:15px;margin:6px 2px 0;
  display:flex;justify-content:space-between;align-items:baseline;gap:10px}
.grp h2 span{font-family:"JetBrains Mono",monospace;font-size:12px;color:var(--muted);font-weight:400}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  box-shadow:var(--shadow);overflow:hidden}
.card.done{border-color:var(--ok)}
.hd{padding:13px 15px 11px;display:flex;gap:10px;align-items:flex-start}
.badge{flex:none;font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;font-weight:700;
  padding:3px 8px;border-radius:99px;margin-top:2px}
.warm{background:var(--warm-soft);color:var(--warm)}
.cold{background:var(--sunk);color:var(--muted)}
.nm{font-weight:700;font-size:15.5px;line-height:1.3}
.ti{color:var(--ink-2);font-size:13.5px;margin-top:2px}
.wy{color:var(--muted);font-size:12.5px;margin-top:3px}
.msg{margin:0 15px 12px;background:var(--sunk);border:1px solid var(--line);border-radius:8px;
  padding:11px 12px;font-size:13.5px;line-height:1.5;white-space:pre-wrap;
  max-height:150px;overflow:auto;color:var(--ink-2)}
.acts{display:flex;flex-wrap:wrap;gap:7px;padding:0 15px 13px}
button,a.btn{font:inherit;font-size:13.5px;font-weight:600;border-radius:8px;padding:9px 13px;
  cursor:pointer;border:1px solid var(--line-2);background:var(--surface);color:var(--ink-2);
  text-decoration:none;display:inline-block}
button.p,a.btn.p{background:var(--accent);color:#fff;border-color:transparent}
button:focus-visible,a.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
button:hover,a.btn:hover{filter:brightness(1.06)}
.st{padding:0 15px 13px;display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.st b{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);
  margin-right:2px;font-weight:700}
.st button{padding:6px 10px;font-size:12.5px}
.st button.on{background:var(--ok-soft);border-color:var(--ok);color:var(--ok)}
.cur{font-family:"JetBrains Mono",monospace;font-size:12px;color:var(--muted)}
.empty{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:18px;
  color:var(--muted);font-size:14.5px}
.toast{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);background:var(--ink);
  color:var(--ground);padding:10px 18px;border-radius:99px;font-size:14px;font-weight:600;
  opacity:0;pointer-events:none;transition:opacity .18s;z-index:50}
.toast.show{opacity:1}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>

<div class="wrap">
  <header>
    <h1>Referral queue</h1>
    <div class="sub" id="sub"></div>
  </header>
  <div id="groups"></div>
</div>
<div class="toast" id="toast"></div>

<script>
const DATA = __DATA__;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const STATUSES = ["Invite Sent","Accepted","Message Sent","Replied","Referred","No Response"];

const groups = {};
for (const p of DATA.people) {
  const k = p.company + " — " + p.role;
  (groups[k] = groups[k] || []).push(p);
}
const open = DATA.people.filter(p => !["Referred","No Response"].includes(p.status)).length;
$("#sub").textContent = `${DATA.people.length} people · ${open} still open · ★ = you already know them`;

$("#groups").innerHTML = Object.entries(groups).map(([k, ppl]) => `
  <section class="grp">
    <h2>${esc(k)}<span>${ppl.length}</span></h2>
    ${ppl.map(p => `
      <article class="card ${["Referred","No Response"].includes(p.status) ? "done" : ""}" id="c${p.row}">
        <div class="hd">
          <span class="badge ${p.warm ? "warm" : "cold"}">${p.warm ? "★ known" : "cold"}</span>
          <div>
            <div class="nm">${esc(p.name)}</div>
            <div class="ti">${esc(p.title)}</div>
            <div class="wy">${esc(p.why)}</div>
          </div>
        </div>
        ${p.message ? `<div class="msg" id="m${p.row}">${esc(p.message)}</div>` : ""}
        <div class="acts">
          ${p.url ? `<a class="btn p" href="${esc(p.url)}" target="_blank" rel="noopener">Open profile →</a>` : ""}
          ${p.message ? `<button onclick="copyMsg(${p.row})">Copy message</button>` : ""}
        </div>
        <div class="st">
          <b>Status</b>
          ${STATUSES.map(s => `<button class="${p.status === s ? "on" : ""}"
              onclick="setStatus(${p.row}, '${s}')">${s}</button>`).join("")}
          <span class="cur" id="s${p.row}">${esc(p.status || "To Contact")}</span>
        </div>
      </article>`).join("")}
  </section>`).join("") || `<div class="empty">Nothing in the referral queue yet.
    It fills automatically when an application is submitted.</div>`;

function toast(t) {
  const el = $("#toast"); el.textContent = t; el.classList.add("show");
  clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("show"), 1600);
}
async function copyMsg(row) {
  const t = $("#m" + row).textContent;
  try { await navigator.clipboard.writeText(t); toast("Copied — paste into LinkedIn"); }
  catch (e) {
    const r = document.createRange(); r.selectNodeContents($("#m" + row));
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
    toast("Selected — long-press to copy");
  }
}
async function setStatus(row, status) {
  try {
    const res = await fetch(location.pathname.replace(/\/$/, "") + "/status" + location.search, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({row, status}),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    $("#s" + row).textContent = status;
    document.querySelectorAll(`#c${row} .st button`).forEach(b =>
      b.classList.toggle("on", b.textContent === status));
    $("#c" + row).classList.toggle("done", ["Referred","No Response"].includes(status));
    toast("Saved: " + status);
  } catch (e) { toast("Could not save"); }
}
</script>
"""


def same(a, b):
    return " ".join(str(a or "").lower().split()) == " ".join(str(b or "").lower().split())


def counts():
    """{(company, role) lower-cased: {"people": n, "referred": [names], "asked": n, "replied": n}}
    — the review list's Referrals and Referred columns."""
    from jobpilot.outreach import referral_tracker as RT
    out = {}
    for d in RT.rows():
        k = (" ".join(str(d.get("Company") or "").lower().split()), " ".join(str(d.get("Role Applied") or "").lower().split()))
        c = out.setdefault(k, {"people": 0, "referred": [], "asked": 0, "replied": 0})
        st = d.get("Status") or "To Contact"
        c["people"] += 1
        if st == "Referred":
            c["referred"].append(str(d.get("Person") or "?"))
        if st in ("Invite Sent", "Accepted", "Message Sent", "Replied", "Referred", "No Response"):
            c["asked"] += 1
        if st in ("Replied", "Referred"):
            c["replied"] += 1
    return out


def build(company=None, role=None):
    """The referral page: every candidate, or one company's, or one role's."""
    from jobpilot.outreach import referral_tracker as RT
    people = []
    for d in RT.rows():
        if company and not same(d.get("Company"), company):
            continue
        if role and not same(d.get("Role Applied"), role):
            continue
        people.append({
            "row": d["_row"],
            "name": d.get("Person") or "",
            "title": d.get("Their Title") or "",
            "company": d.get("Company") or "",
            "role": d.get("Role Applied") or "",
            "warm": str(d.get("Relationship") or "").startswith("1st"),
            "why": d.get("Why Them") or "",
            "url": d.get("Profile URL") or "",
            "status": d.get("Status") or "To Contact",
            "message": d.get("Message Used") or "",
        })
    # open ones first, warm before cold
    people.sort(key=lambda p: (p["status"] in ("Referred", "No Response"), not p["warm"]))
    return TEMPLATE.replace("__DATA__", json.dumps({"people": people}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--company")
    ap.add_argument("-o", "--out",
                    default=os.path.join(DATA, "queue", "_referrals.html"))
    a = ap.parse_args()
    open(a.out, "w", encoding="utf-8").write(build(a.company))
    print(f"  {a.out}")


if __name__ == "__main__":
    main()
