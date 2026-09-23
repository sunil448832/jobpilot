#!/usr/bin/env python3
"""manual_form.py — the phone page for an application Sunil fills by hand:
every field copy-ready, the resume files, Claude's drafted answers, and a
"Mark as submitted" button."""
import json
import os

TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Apply by hand</title>
<style>
 body{font-family:-apple-system,system-ui,sans-serif;margin:0;padding:14px;background:#f6f7f9;color:#111}
 h1{font-size:19px;margin:0 0 2px} .sub{color:#555;font-size:14px;margin-bottom:12px}
 .card{background:#fff;border-radius:12px;padding:12px 14px;margin:10px 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}
 .lbl{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#6C7E90;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center}
 textarea{width:100%;box-sizing:border-box;border:1px solid #DCE3EA;border-radius:8px;padding:9px;font:15px/1.4 inherit;background:#fbfcfd;resize:vertical}
 button.cp{border:0;border-radius:8px;background:#1a73e8;color:#fff;padding:7px 12px;font-size:13px}
 button.cp.ok{background:#1B6B3A}
 .opt{border-top:1px solid #eee;padding-top:8px;margin-top:8px}
 a.big{display:block;text-align:center;padding:14px;border-radius:10px;background:#1a73e8;color:#fff;text-decoration:none;font-weight:700;margin:8px 0}
 a.file{display:inline-block;margin:6px 8px 0 0;padding:8px 12px;border-radius:8px;background:#E7EFF8;color:#1D4E89;text-decoration:none;font-weight:600}
 .actions{display:flex;gap:8px;margin-top:14px} .actions button{flex:1;padding:14px;border:0;border-radius:10px;font-size:16px;font-weight:700}
 .done{background:#1B6B3A;color:#fff} .later{background:#e5e7eb} .rej{background:#f3d6d6;color:#7a1f1f}
 .ok{background:#e6f7ee;color:#1a7f4b;padding:10px;border-radius:8px;display:none;margin-top:10px}
 .banner{background:#FBF1D6;color:#7A5A06;padding:10px 12px;border-radius:8px;font-size:14px}
</style></head><body>
<h1 id="role"></h1><div class="sub" id="meta"></div>
<div class="banner" id="why"></div>
<a class="big" id="open" target="_blank" rel="noopener">Open the application page ↗</a>
<div class="card"><div class="lbl">Resume files — download, then attach in the form</div><span id="files"></span></div>
<div id="fields"></div>
<div id="answers"></div>
<div class="card"><div class="actions">
 <button class="rej" id="reject">Reject</button><button class="later" id="later">Later</button>
 <button class="done" id="done">Mark as submitted</button></div><div class="ok" id="ok"></div></div>
<script>
const D=__DATA__, Q=location.search;
document.getElementById("role").textContent=D.role; document.getElementById("meta").textContent=D.company+" · "+(D.location||"")+" · "+(D.market||"");
document.getElementById("why").textContent=D.reason||""; document.getElementById("open").href=D.apply_url||D.url;
document.getElementById("files").innerHTML=`<a class="file" href="/file/${D.id}/docx${Q}">sunil_resume.docx</a><a class="file" href="/file/${D.id}/pdf${Q}">sunil_resume.pdf</a>`;
function copyBtn(getText){const b=document.createElement("button");b.className="cp";b.textContent="Copy";
  b.onclick=()=>{const t=getText();let ok=false;
    try{const ta=document.createElement("textarea");ta.value=t;ta.style.position="fixed";ta.style.opacity="0";document.body.appendChild(ta);ta.select();ok=document.execCommand("copy");document.body.removeChild(ta);}catch(e){}
    if(!ok&&navigator.clipboard){navigator.clipboard.writeText(t).then(()=>{},()=>{});ok=true;}
    b.textContent=ok?"Copied":"Select & copy";b.classList.add("ok");setTimeout(()=>{b.textContent="Copy";b.classList.remove("ok");},1500);};return b;}
function card(label,text,parent){const c=document.createElement("div");c.className="card";const l=document.createElement("div");l.className="lbl";l.textContent=label;
  const ta=document.createElement("textarea");ta.readOnly=true;ta.value=text;ta.rows=Math.min(12,Math.max(1,Math.ceil(text.length/60)));ta.onclick=()=>ta.select();
  l.appendChild(copyBtn(()=>ta.value));c.appendChild(l);c.appendChild(ta);parent.appendChild(c);}
const F=document.getElementById("fields"); for(const [k,v] of Object.entries(D.fields||{})) card(k,v,F);
const A=document.getElementById("answers");
for(const q of (D.questions||[])){const c=document.createElement("div");c.className="card";const l=document.createElement("div");l.className="lbl";l.textContent=q.label;c.appendChild(l);
  if(!(q.options||[]).length){const p=document.createElement("div");p.className="sub";p.textContent="(not drafted yet)";c.appendChild(p);}
  (q.options||[]).forEach((o,i)=>{const d=document.createElement("div");d.className="opt";const h=document.createElement("div");h.className="lbl";h.textContent="Option "+(i+1);
    const ta=document.createElement("textarea");ta.readOnly=true;ta.value=o;ta.rows=Math.min(12,Math.max(2,Math.ceil(o.length/60)));ta.onclick=()=>ta.select();h.appendChild(copyBtn(()=>ta.value));d.appendChild(h);d.appendChild(ta);c.appendChild(d);});
  A.appendChild(c);}
async function decide(decision){if(decision==="rejected"&&!confirm("Reject this role permanently?"))return;
  const r=await fetch("/a/"+D.id+"/submit"+Q,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({decision,answers:[]})});
  const ok=document.getElementById("ok");ok.style.display="block";
  if(r.ok){ok.textContent={submitted:"Recorded as submitted. Thank you — nothing else to do here.",deferred:"Kept in the queue.",rejected:"Dropped."}[decision];
    for(const id of ["reject","later","done"])document.getElementById(id).disabled=true;
    const back=document.createElement("a");back.href="/"+Q+"&_="+Date.now();back.textContent="← Back to the list";back.style.cssText="display:block;margin-top:10px;font-weight:700";ok.appendChild(back);}
  else ok.textContent="Error: "+await r.text();}
document.getElementById("done").onclick=()=>decide("submitted");document.getElementById("later").onclick=()=>decide("deferred");document.getElementById("reject").onclick=()=>decide("rejected");
if(D.status==="submitted"){for(const id of ["reject","later","done"])document.getElementById(id).disabled=true;const ok=document.getElementById("ok");ok.style.display="block";ok.textContent="Submitted on "+String(D.submitted_at||"").slice(0,10)+".";}
</script></body></html>"""


def build(item):
    return TEMPLATE.replace("__DATA__", json.dumps(item, ensure_ascii=False))
