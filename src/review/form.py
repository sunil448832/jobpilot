#!/usr/bin/env python3
"""
form.py — render a queue item as a single review page (Phase 3b).

The card-by-card Telegram flow was too slow: one tap per question, four
round-trips per application. This renders the WHOLE application as one page —
job header, every question with its drafted options, the values that will be
submitted, and approve/skip — so it can be read and filled in one sitting.

Telegram then carries just the link. Answers are written to the artifact's db
and read back with the Artifact tool's read_db.

Usage:
    python jobs/form.py <queue-id> [-o out.html]
    python jobs/form.py --latest
"""
import argparse
import glob
import html
import json
import os
import sys

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)
QUEUE_DIR = os.path.join(DATA, "queue")

TEMPLATE = r"""<title>Application Review</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=Source+Sans+3:ital,wght@0,400;0,600;1,400&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
:root{
  --ground:#F3F6F9; --surface:#FFFFFF; --sunk:#EDF1F5;
  --ink:#131A23; --ink-2:#3F4E5E; --muted:#6C7E90;
  --line:#DCE3EA; --line-2:#C6D0DA;
  --accent:#1D4E89; --accent-soft:#E7EFF8; --accent-line:#A8C4E4;
  --ok:#15803D; --ok-soft:#E6F4EA;
  --warn:#9A5B08; --warn-soft:#FBF0DF;
  --shadow:0 1px 2px rgba(19,26,35,.06),0 8px 24px -12px rgba(19,26,35,.18);
  --r:10px;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --ground:#0D1218; --surface:#151D26; --sunk:#111820;
  --ink:#E6EDF4; --ink-2:#B0BFCD; --muted:#7E90A2;
  --line:#243039; --line-2:#31404C;
  --accent:#82ABDF; --accent-soft:#182635; --accent-line:#2E4864;
  --ok:#5FBF80; --ok-soft:#14251A;
  --warn:#DDA25E; --warn-soft:#251C10;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6);
}}
:root[data-theme="dark"]{
  --ground:#0D1218; --surface:#151D26; --sunk:#111820;
  --ink:#E6EDF4; --ink-2:#B0BFCD; --muted:#7E90A2;
  --line:#243039; --line-2:#31404C;
  --accent:#82ABDF; --accent-soft:#182635; --accent-line:#2E4864;
  --ok:#5FBF80; --ok-soft:#14251A;
  --warn:#DDA25E; --warn-soft:#251C10;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6);
}
*{box-sizing:border-box}
body{
  background:var(--ground); color:var(--ink);
  font-family:"Source Sans 3",ui-sans-serif,system-ui,sans-serif;
  font-size:16px; line-height:1.55; margin:0;
  padding:20px 16px 132px; -webkit-text-size-adjust:100%;
}
.wrap{max-width:660px;margin:0 auto;display:flex;flex-direction:column;gap:18px}
h1,h2,h3{font-family:"Bricolage Grotesque","Source Sans 3",sans-serif;margin:0;text-wrap:balance;letter-spacing:-.015em}
.eyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);font-weight:600}
.mono{font-family:"JetBrains Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums}

/* header ------------------------------------------------------------- */
.head{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:20px;box-shadow:var(--shadow)}
.head h1{font-size:23px;font-weight:700;line-height:1.22}
.head .co{color:var(--ink-2);font-size:15px;margin-top:4px}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-top:16px}
.meta div{background:var(--surface);padding:10px 12px}
.meta dt{font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-weight:600}
.meta dd{margin:3px 0 0;font-size:14px;font-weight:600;font-family:"JetBrains Mono",monospace;font-variant-numeric:tabular-nums}
.jd{display:inline-block;margin-top:14px;font-size:14px;color:var(--accent);text-decoration:none;border-bottom:1px solid var(--accent-line);padding-bottom:1px}

/* flags --------------------------------------------------------------- */
.flags{display:flex;flex-direction:column;gap:8px}
.flag{display:flex;gap:9px;background:var(--warn-soft);border-left:3px solid var(--warn);border-radius:0 7px 7px 0;padding:10px 13px;font-size:14px;color:var(--ink-2)}
.flag b{color:var(--warn);font-weight:600}

/* questions ----------------------------------------------------------- */
.sec-title{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:0 2px}
.sec-title h2{font-size:15px;font-weight:700}
.count{font-size:12.5px;color:var(--muted);font-family:"JetBrains Mono",monospace}
.q{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);overflow:hidden;box-shadow:var(--shadow)}
.q.done{border-color:var(--ok)}
.q-head{padding:15px 17px 13px;border-bottom:1px solid var(--line);display:flex;gap:11px;align-items:flex-start}
.q-n{flex:none;width:22px;height:22px;border-radius:50%;background:var(--sunk);color:var(--muted);font-size:11.5px;font-weight:700;display:grid;place-items:center;font-family:"JetBrains Mono",monospace;margin-top:1px}
.q.done .q-n{background:var(--ok);color:#fff}
.q-label{font-size:14.5px;font-weight:600;line-height:1.42}
.req{color:var(--warn);font-weight:700}
.opts{display:flex;flex-direction:column}
.opt{display:flex;gap:11px;align-items:flex-start;padding:13px 17px;cursor:pointer;border-bottom:1px solid var(--line);transition:background .12s}
.opt:last-child{border-bottom:none}
.opt:hover{background:var(--sunk)}
.opt input{position:absolute;opacity:0;pointer-events:none}
.dot{flex:none;width:19px;height:19px;border-radius:50%;border:1.5px solid var(--line-2);margin-top:2px;position:relative;transition:border-color .12s}
.opt input:checked~.dot{border-color:var(--accent);border-width:6px}
.opt input:focus-visible~.dot{outline:2px solid var(--accent);outline-offset:2px}
.opt-body{font-size:14.5px;line-height:1.5;color:var(--ink-2)}
.opt input:checked~.opt-body{color:var(--ink)}
.opt.own .opt-body{color:var(--accent);font-weight:600}
.own-box{padding:0 17px 15px;display:none}
.own-box.show{display:block}
textarea{width:100%;min-height:96px;padding:11px 13px;border:1px solid var(--line-2);border-radius:8px;background:var(--sunk);color:var(--ink);font:inherit;font-size:14.5px;line-height:1.5;resize:vertical}
textarea:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}

/* fields -------------------------------------------------------------- */
details{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow)}
summary{padding:15px 17px;cursor:pointer;font-weight:700;font-size:15px;font-family:"Bricolage Grotesque",sans-serif;list-style:none;display:flex;justify-content:space-between;align-items:center;gap:10px}
summary::-webkit-details-marker{display:none}
summary::after{content:"▾";color:var(--muted);font-size:13px;transition:transform .15s}
details[open] summary::after{transform:rotate(180deg)}
.fields{border-top:1px solid var(--line)}
.fld{display:grid;grid-template-columns:minmax(0,148px) minmax(0,1fr);gap:12px;padding:9px 17px;border-bottom:1px solid var(--line);font-size:13.5px}
.fld:last-child{border-bottom:none}
.fld dt{color:var(--muted);overflow-wrap:anywhere}
.fld dd{margin:0;font-family:"JetBrains Mono",monospace;font-size:12.5px;overflow-wrap:anywhere;color:var(--ink-2)}

/* actions ------------------------------------------------------------- */
.bar{position:fixed;left:0;right:0;bottom:0;background:var(--surface);border-top:1px solid var(--line);padding:12px 16px calc(12px + env(safe-area-inset-bottom));z-index:20}
.bar-in{max-width:660px;margin:0 auto;display:flex;gap:10px;align-items:center}
button{font:inherit;font-weight:700;font-family:"Bricolage Grotesque",sans-serif;border-radius:9px;padding:13px 18px;cursor:pointer;border:1px solid transparent;font-size:15px;transition:filter .12s}
button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.approve{flex:1;background:var(--accent);color:#fff}
.approve:disabled{background:var(--sunk);color:var(--muted);border-color:var(--line);cursor:not-allowed}
.skip{background:transparent;color:var(--ink-2);border-color:var(--line-2)}
.reject{background:transparent;color:var(--warn);border-color:var(--warn)}
button:not(:disabled):hover{filter:brightness(1.08)}
.hint{font-size:12.5px;color:var(--muted);text-align:center;margin-top:8px;min-height:16px}
.hint.bad{color:var(--warn)}
.hint.good{color:var(--ok)}

/* result -------------------------------------------------------------- */
.result{display:none;background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--ok);border-radius:var(--r);padding:18px;box-shadow:var(--shadow)}
.result.show{display:block}
.empty{background:var(--ok-soft);border:1px solid var(--line);border-left:3px solid var(--ok);border-radius:var(--r);padding:15px 17px;font-size:14.5px;color:var(--ink-2)}
.result h2{font-size:17px;margin-bottom:5px}
.result p{margin:0;color:var(--ink-2);font-size:14.5px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>

<div class="wrap">
  <header class="head">
    <div class="eyebrow" id="eyebrow">Application review</div>
    <h1 id="role"></h1>
    <div class="co" id="co"></div>
    <dl class="meta" id="meta"></dl>
    <a class="jd" id="jd" target="_blank" rel="noopener">Read the full posting →</a>
  </header>

  <div class="flags" id="flags"></div>

  <div class="sec-title"><h2>Questions autofill couldn't answer</h2><span class="count" id="count"></span></div>
  <div id="questions" style="display:flex;flex-direction:column;gap:14px"></div>

  <details>
    <summary><span>Values that will be submitted</span><span class="count" id="nfields"></span></summary>
    <dl class="fields" id="fields"></dl>
  </details>

  <div class="result" id="result"><h2 id="rtitle"></h2><p id="rbody"></p></div>
</div>

<div class="bar"><div class="bar-in">
  <button class="reject" id="reject">Reject</button>
  <button class="skip" id="skip">Later</button>
  <button class="approve" id="approve" disabled>Approve &amp; submit</button>
</div><div class="hint" id="hint"></div></div>

<script>
const DATA = __DATA__;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const answers = {};
let db = null, decided = (DATA.status === "submitted" || DATA.status === "approved" || DATA.status === "rejected" || !!DATA.submitted_at);

/* header ------------------------------------------------------------- */
$("#role").textContent = DATA.role || "(role)";
$("#co").textContent = [DATA.company, DATA.location].filter(Boolean).join(" · ");
$("#eyebrow").textContent = (DATA.portal || "application") + " · " + (DATA.market || "");
const score = typeof DATA.score === "number" ? DATA.score.toFixed(0) + "%" : "n/a";
$("#meta").innerHTML = [
  ["ATS match", score],
  ["Asking", DATA.salary_quoted || "—"],
  ["Resume", (DATA.resume || "").split("/").pop() || "—"],
].map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("");
if (DATA.url) $("#jd").href = DATA.url; else $("#jd").style.display = "none";

/* flags -------------------------------------------------------------- */
$("#flags").innerHTML = (DATA.flags || []).map(f =>
  `<div class="flag"><b>${esc(f.tag)}</b><span>${esc(f.text)}</span></div>`).join("");
/* a submit attempt that did not go through: say why, show the page, offer Retry */
if (DATA.fail) {
  const f = DATA.fail, t = location.search;
  const shot = f.shot ? ` <a href="${f.shot}${t}" target="_blank" rel="noopener">screenshot →</a>` : "";
  $("#flags").innerHTML = `<div class="flag" style="border-left-color:#D2453B"><b>Submit attempt ${esc(f.attempts)}</b>` +
    `<span>${esc(f.reason || "did not go through")}.${shot}` +
    (f.status === "failed" ? " Fix what it needs, then Retry." : "") + `</span></div>` + $("#flags").innerHTML;
  if (f.status === "failed") $("#approve").textContent = "Retry with these answers";
}

/* questions ---------------------------------------------------------- */
const qs = DATA.questions || [];
$("#count").textContent = qs.length ? `0 / ${qs.length}` : "none";
if (!qs.length) {
  $("#questions").innerHTML =
    `<div class="empty">Nothing to ask — every field was answered from your saved profile
     and previous answers. Review the values below and approve.</div>`;
}
$("#questions").innerHTML += qs.map((q, i) => `
  <section class="q" id="q${i}">
    <div class="q-head">
      <span class="q-n">${i + 1}</span>
      <span class="q-label">${esc(q.label)}${q.required ? ' <span class="req">*</span>' : ""}</span>
    </div>
    <div class="opts">
      ${(q.options || []).map((o, j) => `
        <label class="opt">
          <input type="radio" name="q${i}" value="${j}">
          <span class="dot"></span><span class="opt-body">${esc(o)}</span>
        </label>`).join("")}
      <label class="opt own">
        <input type="radio" name="q${i}" value="own">
        <span class="dot"></span><span class="opt-body">Write my own answer</span>
      </label>
    </div>
    <div class="own-box" id="own${i}">
      <textarea id="ta${i}" placeholder="Type your answer — I'll use it verbatim and redraft the options from it."></textarea>
    </div>
  </section>`).join("");

qs.forEach((q, i) => {
  document.getElementsByName("q" + i).forEach(r => r.addEventListener("change", () => {
    const own = r.value === "own";
    $("#own" + i).classList.toggle("show", own);
    if (own) { $("#ta" + i).focus(); answers[i] = { kind: "own", text: $("#ta" + i).value }; }
    else answers[i] = { kind: "picked", index: +r.value, text: q.options[+r.value] };
    refresh();
  }));
  const ta = $("#ta" + i);
  if (ta) ta.addEventListener("input", () => {
    if (answers[i] && answers[i].kind === "own") { answers[i].text = ta.value; refresh(); }
  });
});

function answered(i) {
  const a = answers[i];
  return !!a && (a.kind === "picked" || (a.text || "").trim().length > 2);
}
function refresh() {
  let done = 0;
  qs.forEach((q, i) => { const ok = answered(i); if (ok) done++; $("#q" + i).classList.toggle("done", ok); });
  $("#count").textContent = qs.length ? `${done} / ${qs.length}` : "none";
  const missing = qs.filter((q, i) => q.required && !answered(i)).length;
  $("#approve").disabled = missing > 0 || decided;
  if (DATA.status === "submitted" || DATA.submitted_at) {
    $("#skip").disabled = true; $("#reject").disabled = true;
    $("#result").classList.add("show");
    $("#rtitle").textContent = "Already submitted";
    $("#rbody").textContent = "This application was filed on " + String(DATA.submitted_at || "").slice(0, 10) + ". Nothing here can be changed.";
  }
  const h = $("#hint");
  h.className = "hint" + (missing ? " bad" : "");
  h.textContent = missing ? `${missing} required question${missing > 1 ? "s" : ""} left` : "";
}

/* fields ------------------------------------------------------------- */
const fe = Object.entries(DATA.fields || {});
$("#nfields").textContent = fe.length;
$("#fields").innerHTML = fe.map(([k, v]) =>
  `<div class="fld"><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("");

/* submit ------------------------------------------------------------- */
const LOCAL = !window.claude;           // served from the local machine
if (!LOCAL) {
  claude.use("db").then(d => { db = d; }).catch(() => {});
}

async function send(decision) {
  if (decided) return;
  const payload = {
    item: DATA.id, decision, decidedAt: new Date().toISOString(),
    company: DATA.company || "", role: DATA.role || "",
    answers: qs.map((q, i) => ({
      qid: q.qid, label: q.label,
      kind: answers[i] ? answers[i].kind : "unanswered",
      text: answers[i] ? answers[i].text : "",
    })),
  };
  const h = $("#hint");
  h.className = "hint"; h.textContent = "Saving…";
  try {
    if (LOCAL) {
      const url = location.pathname.replace(/\/$/, "") + "/submit" + location.search;
      const res = await fetch(url, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
    } else {
      if (!db) { h.className = "hint bad"; h.textContent = "Still connecting — try again in a moment."; return; }
      await db.doc("submissions/" + DATA.id).set(payload);
    }
    decided = true;
    $("#approve").disabled = true; $("#skip").disabled = true;
    $("#result").classList.add("show");
    $("#rtitle").textContent = {approved: "Approved", deferred: "Kept for later",
                                rejected: "Rejected"}[decision];
    $("#rbody").textContent = {
      approved: "Saved. Your machine will fill the form again with these answers and submit.",
      deferred: "Kept in the queue. It will still be there next time — nothing was discarded.",
      rejected: "Dropped from the queue. It will not be shown again.",
    }[decision];
    h.className = "hint good"; h.textContent = "Saved";
    // Back to the list by a fresh GET (cache-busting param), never by history —
    // the back button restored a stale list on the phone.
    const back = document.createElement("a");
    back.href = "/" + location.search + (location.search ? "&" : "?") + "_=" + Date.now();
    back.textContent = "← Back to the list";
    back.style.cssText = "display:inline-block;margin-top:12px;font-weight:700";
    $("#result").appendChild(back);
    $("#result").scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (e) {
    h.className = "hint bad";
    h.textContent = e && e.code === "rate_limited" ? "Too many saves — wait a moment." : "Couldn't save. Try again.";
  }
}
$("#approve").addEventListener("click", () => send("approved"));
// "Later" keeps it in the queue — nothing is discarded unless Reject is pressed.
$("#skip").addEventListener("click", () => send("deferred"));
$("#reject").addEventListener("click", () => {
  if (confirm("Reject this role permanently? It will be dropped from the queue.")) send("rejected");
});
refresh();
</script>
"""


def build(item):
    flags = []
    for w in item.get("warnings", []):
        first = w.splitlines()[0]
        if "question" in first.lower():
            continue
        flags.append({"tag": "Check", "text": first[:200]})
    for note in item.get("manual_flags", []):
        flags.append({"tag": note.get("tag", "Note"), "text": note.get("text", "")})

    fail = None
    if item.get("attempts") and item.get("status") in ("failed", "needs_input", "approved"):
        shot = item.get("submit_screenshot") or ""
        fail = {"attempts": item.get("attempts"), "reason": item.get("fail_reason", ""),
                "status": item.get("status"),
                "shot": ("/shot/" + os.path.basename(shot)[:-4]) if shot.endswith(".png") else None}
    data = {
        "fail": fail,
        "id": item["id"], "company": item.get("company"), "role": item.get("role"),
        "location": item.get("location"), "url": item.get("url"),
        "portal": item.get("portal"), "market": item.get("market"),
        "score": item.get("score"), "salary_quoted": item.get("salary_quoted"),
        "resume": item.get("resume"), "fields": item.get("fields", {}),
        "questions": [
            {"qid": q["qid"], "label": q["label"], "required": q.get("required", False),
             "options": q.get("options", [])}
            for q in item.get("questions", []) if q.get("status") != "answered"
        ],
        "flags": flags,
    }
    return TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("item_id", nargs="?")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()

    if a.latest or not a.item_id:
        files = [f for f in sorted(glob.glob(os.path.join(QUEUE_DIR, "*.json")))
                 if not os.path.basename(f).startswith("_")]
        if not files:
            sys.exit("queue is empty")
        path = files[-1]
    else:
        path = os.path.join(QUEUE_DIR, a.item_id + ".json")
        if not os.path.isfile(path):
            sys.exit(f"no queue item {a.item_id}")

    item = json.load(open(path))
    out = a.out or os.path.join(QUEUE_DIR, item["id"] + ".html")
    open(out, "w", encoding="utf-8").write(build(item))
    print(f"  {out}")
    print(f"  {item['company']} — {item['role']}")
    print(f"  {len([q for q in item.get('questions',[]) if q.get('status')!='answered'])} open questions, "
          f"{len(item.get('fields',{}))} fields")


if __name__ == "__main__":
    main()
