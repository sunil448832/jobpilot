#!/usr/bin/env python3
"""keyword_form.py — the Sunday tick-box page: skills the market asked for that are
not on the resume. Ticking adds them to targets.yaml `interest:` (ranking only)."""
import json
import os
import sys

from jobpilot.core.paths import SRC as JOBS_DIR, DATA  # noqa: E402,F401

TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Keywords</title>
<style>
 body{font-family:-apple-system,system-ui,sans-serif;margin:0;padding:16px;background:#f6f7f9;color:#111}
 h1{font-size:20px;margin:0 0 4px} .sub{color:#555;font-size:14px;margin-bottom:14px}
 .card{background:#fff;border-radius:12px;padding:14px;margin:10px 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}
 .row{padding:10px 4px;border-bottom:1px solid #eee} .row:last-child{border-bottom:0}
 .term{font-size:17px;display:flex;align-items:center} .opts{display:flex;gap:6px;margin-top:8px}
 .opts label{flex:1;text-align:center;padding:9px 0;border:1px solid #ccc;border-radius:8px;font-size:15px;background:#fafafa}
 .opts input{display:none} .opts input:checked+span{font-weight:700} .opts label.i:has(input:checked){background:#e3f0ff;border-color:#1a73e8}
 .opts label.d:has(input:checked){background:#e6f7ee;border-color:#1a7f4b} .opts label.s:has(input:checked){background:#f1f1f1;border-color:#888}
 .meta{color:#666;font-size:13px;margin-left:auto;white-space:nowrap}
 .learned{color:#2a6;font-size:14px}
 button{width:100%;padding:14px;font-size:17px;border:0;border-radius:10px;background:#1a73e8;color:#fff;margin-top:12px}
 button.secondary{background:#e5e7eb;color:#111} .note{font-size:13px;color:#777;margin-top:12px}
 .ok{background:#e6f7ee;color:#1a7f4b;padding:10px;border-radius:8px;display:none;margin-top:10px}
</style></head><body>
<h1>Keywords the market asks for</h1>
<div class="sub">From the JDs you applied to. For each one choose:<br>
<b>Interest</b> — I want roles asking for this (ranking only, never a resume claim).<br>
<b>Done</b> — I have actually done this; the resume just never mentions it (counts as true; goes on your write-it-up list).<br>
<b>Skip</b> — not relevant.</div>
<div class="card" id="learned"></div>
<form id="f" class="card"><div id="list"></div>
<button type="submit">Save choices</button>
<div class="ok" id="ok"></div></form>
<div class="note">Interest → <code>targets.yaml → keywords.interest</code>. Done → <code>keywords.confirmed</code> + <code>tracking/resume_todo.md</code>. Delete a line there to undo.</div>
<script>
const D=__DATA__;
const L=document.getElementById("learned");
L.innerHTML = D.learned.length ? "<div class='learned'>Added automatically this week (already true on your resume): <b>"+D.learned.join(", ")+"</b></div>" : "<div class='learned'>Nothing auto-added this week.</div>";
const list=document.getElementById("list");
if(!D.pending.length){ list.innerHTML="<div class='sub'>No gap keywords waiting.</div>"; }
for(const p of D.pending){
  const r=document.createElement("div"); r.className="row";
  r.innerHTML=`<div class="term"><span>${p.term}</span><span class="meta">${p.docs} JDs · ${p.week}</span></div>
   <div class="opts">
     <label class="i"><input type="radio" name="c_${p.term}" value="interest"><span>Interest</span></label>
     <label class="d"><input type="radio" name="c_${p.term}" value="done"><span>Done</span></label>
     <label class="s"><input type="radio" name="c_${p.term}" value="skip"><span>Skip</span></label>
   </div>`;
  list.appendChild(r);
}
async function post(body){
  const r=await fetch("/keywords/save"+location.search,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const j=await r.json(); const ok=document.getElementById("ok"); ok.style.display="block";
  ok.textContent = j.ok ? `Saved. Interest: ${(j.interest||[]).join(", ")||"none"}. Done: ${(j.done||[]).join(", ")||"none"}. Skipped: ${(j.dismissed||[]).join(", ")||"none"}.` : ("Error: "+(j.error||""));
  setTimeout(()=>location.reload(), 1200);
}
document.getElementById("f").addEventListener("submit",e=>{e.preventDefault();
  const body={interest:[],done:[],dismiss:[]};
  for(const p of D.pending){ const v=(document.querySelector(`input[name="c_${p.term}"]:checked`)||{}).value;
    if(v==="interest") body.interest.push(p.term); else if(v==="done") body.done.push(p.term); else if(v==="skip") body.dismiss.push(p.term); }
  post(body);});
</script></body></html>"""


def build():
    from jobpilot.rank import keyword_learn as KL
    st = KL.load_state()
    wk = KL.week()
    learned = [t for t, v in st["promoted"].items() if v.get("week") == wk]
    return TEMPLATE.replace("__DATA__", json.dumps({"pending": KL.pending(), "learned": learned}, ensure_ascii=False))
