"""Two imitation application forms, served on localhost, so the see/act demo runs
offline and never touches a real portal.

WORKDAY-STYLE  a multi-step wizard, one page at a time:
  Sign In  ->  (unknown email) Create Account  ->  My Information  ->
  Application Questions  ->  Review  ->  Submit -> "Application submitted"
  The widgets that make real Workday hard:
    - a progress bar whose ACTIVE step is the only way to tell where you are
    - "How Did You Hear About Us?": a search-and-pick box. Typing only searches;
      the value is a picked entry shown as a pill. Some entries sit inside a
      category ("Social Media" > "LinkedIn").
    - dropdowns that are <button>s showing "Select One", with a popup list
    - a Yes/No radio question whose only "required" mark is the * in its text
    - Save and Continue refuses the page and lists the fields still missing

GREENHOUSE-STYLE  one page:
    - "Resume/CV*": the <input type=file> is hidden behind an "Attach" button
    - "Country*": a searchable menu (type to filter, click an option)
    - "Are you legally authorized to work in the country?*": a native <select>
    - "Will you require sponsorship?*": Yes / No rendered as two buttons
    - "Submit application" -> "Thank you for applying"
"""
import http.server
import threading

WORKDAY = r"""<!doctype html><html><head><meta charset="utf-8"><title>Acme Careers</title>
<style>
 body{font-family:sans-serif;max-width:720px;margin:20px auto}
 .field{margin:14px 0} .pill{display:inline-block;background:#eef;padding:2px 8px;margin-right:4px;border-radius:10px}
 [role=listbox]{border:1px solid #999;max-width:320px} [role=option]{padding:4px;cursor:pointer}
 ol.bar li{display:inline-block;margin-right:14px;color:#888} ol.bar li[aria-current=step]{font-weight:700;color:#000}
 .err{color:#b00}
</style></head><body>
<h1>Senior Machine Learning Engineer — Acme</h1>
<ol class="bar" aria-label="Application progress">
  <li>Sign In</li><li>My Information</li><li>Application Questions</li><li>Review</li>
</ol>
<main id="main"></main>
<script>
const accounts = JSON.parse(localStorage.getItem('accounts') || '{}');
const data = {};                     // what the applicant has entered, per field
let step = 'Sign In';
const HEAR = {'Social Media': ['LinkedIn', 'Facebook'], 'Job Board': ['Indeed', 'Glassdoor'],
              'Employee Referral': null, 'Company Website': null};
const $ = s => document.querySelector(s);

function bar() {                     // only the active step carries aria-current="step"
  for (const li of document.querySelectorAll('ol.bar li')) {
    li.removeAttribute('aria-current');
    if (li.textContent === step) li.setAttribute('aria-current', 'step');
  }
}

function go(s) { step = s; bar(); render(); }

function field(label, inner, id) {
  return `<div class="field" id="f-${id}"><label id="l-${id}" for="${id}">${label}</label><div>${inner}</div></div>`;
}
function dropdown(id, label, options) {
  // Workday's dropdown: a button showing "Select One" (or the value) that opens a list
  const v = data[id] || 'Select One';
  return field(label, `<button type="button" id="${id}" aria-haspopup="listbox" aria-labelledby="l-${id} ${id}"
      onclick="openList('${id}', ${JSON.stringify(options).replace(/"/g, '&quot;')})">${v}</button><div id="pop-${id}"></div>`, id);
}
function openList(id, options) {
  $('#pop-' + id).innerHTML = `<div role="listbox" aria-label="${id} options">` +
    options.map(o => `<div role="option" onclick="data['${id}']='${o}'; render()">${o}</div>`).join('') + '</div>';
}

function render() {
  const m = $('#main');
  if (step === 'Sign In') {
    m.innerHTML = `<h2>Sign In</h2><div class="err" role="alert" id="alert"></div>` +
      field('Email Address*', '<input id="email" type="email">', 'email') +
      field('Password*', '<input id="password" type="password">', 'password') +
      `<button type="button" onclick="signIn()">Sign In</button>
       <p>Don't have an account? <button type="button" onclick="step='Create Account'; render()">Create Account</button></p>`;
  } else if (step === 'Create Account') {
    m.innerHTML = `<h2>Create Account</h2><div class="err" role="alert" id="alert"></div>` +
      field('Email Address*', '<input id="email" type="email">', 'email') +
      field('Password*', '<input id="password" type="password">', 'password') +
      field('Verify New Password*', '<input id="verify" type="password">', 'verify') +
      `<label><input type="checkbox" id="terms"> I agree to the Candidate Privacy Terms</label><br>
       <button type="button" onclick="createAccount()">Create Account</button>`;
  } else if (step === 'My Information') {
    const pills = (data.hear || []).map(p => `<li class="pill">${p}</li>`).join('');
    m.innerHTML = `<h2>My Information</h2><div class="err" role="alert" id="alert"></div>` +
      field('How Did You Hear About Us?*', `<ul aria-label="Selected sources">${pills}</ul>
         <input id="hear" type="text" role="combobox" aria-expanded="false" aria-controls="hear-list"
                aria-labelledby="l-hear" placeholder="Search" onkeydown="hearKey(event)" onclick="hearOpen()">
         <div id="hear-list"></div>`, 'hear') +
      field('Given Name(s)*', `<input id="given" value="${data.given || ''}" oninput="data.given=this.value">`, 'given') +
      field('Family Name*', `<input id="family" value="${data.family || ''}" oninput="data.family=this.value">`, 'family') +
      dropdown('country', 'Country*', ['India', 'Netherlands', 'Germany']) +
      dropdown('device', 'Phone Device Type*', ['Mobile', 'Home', 'Work']) +
      `<fieldset class="field"><legend>Have you been employed by Acme in the past?*</legend>
         <label><input type="radio" name="prev" value="Yes" ${data.prev === 'Yes' ? 'checked' : ''} onchange="data.prev='Yes'">Yes</label>
         <label><input type="radio" name="prev" value="No" ${data.prev === 'No' ? 'checked' : ''} onchange="data.prev='No'">No</label>
       </fieldset>` + nav();
  } else if (step === 'Application Questions') {
    m.innerHTML = `<h2>Application Questions</h2><div class="err" role="alert" id="alert"></div>` +
      dropdown('sponsor', 'Will you now or in the future require visa sponsorship?*', ['Yes', 'No']) +
      field('Why do you want to work at Acme?*', `<textarea id="why" oninput="data.why=this.value">${data.why || ''}</textarea>`, 'why') + nav();
  } else if (step === 'Review') {
    m.innerHTML = `<h2>Review</h2><pre>${JSON.stringify(data, null, 1)}</pre>
      <button type="button" onclick="go('My Information')">Back</button>
      <button type="button" onclick="$('#main').innerHTML='<h2>Application submitted</h2><p>Thank you for applying.</p>'">Submit</button>`;
  }
}
function nav() { return `<button type="button" onclick="saveAndContinue()">Save and Continue</button>`; }

function signIn() {
  const e = $('#email').value, p = $('#password').value;
  if (!(e in accounts)) { $('#alert').textContent = 'No account exists for this email. Create an account.'; return; }
  if (accounts[e] !== p) { $('#alert').textContent = 'Wrong email address or password.'; return; }
  go('My Information');
}
function createAccount() {
  const e = $('#email').value, p = $('#password').value;
  if (!e || !p || p !== $('#verify').value || !$('#terms').checked) { $('#alert').textContent = 'Please complete every field.'; return; }
  accounts[e] = p; localStorage.setItem('accounts', JSON.stringify(accounts)); go('My Information');
}

// the search-and-pick box: typing + Enter searches; clicking opens the top level
function hearOpen() { list(Object.keys(HEAR)); }
function hearKey(ev) {
  if (ev.key !== 'Enter') return;
  const q = ev.target.value.toLowerCase();
  // this tenant searches the top level only: "LinkedIn" is found by opening "Social Media"
  list(Object.keys(HEAR).filter(x => x.toLowerCase().includes(q)), true);
}
function list(items, searched) {
  $('#hear').setAttribute('aria-expanded', 'true');
  $('#hear-list').innerHTML = `<div role="listbox" id="hear-lb" aria-label="How Did You Hear About Us? options">` +
    // a category is marked the way real menus mark a submenu: aria-haspopup + a chevron
    (items.length ? items.map(i => `<div role="option" ${HEAR[i] ? 'aria-haspopup="true"' : ''}
                                      onclick="hearPick('${i}')">${i}${HEAR[i] ? ' ›' : ''}</div>`).join('')
                  : '<div>No Items.</div>') + '</div>';
}
function hearPick(i) {
  if (HEAR[i]) { list(HEAR[i]); return; }            // a category opens its own list
  data.hear = [i]; render();                           // a leaf becomes the pill
}

function saveAndContinue() {
  const need = step === 'My Information'
    ? [['hear', 'How Did You Hear About Us?'], ['given', 'Given Name(s)'], ['family', 'Family Name'],
       ['country', 'Country'], ['device', 'Phone Device Type'], ['prev', 'Have you been employed by Acme in the past?']]
    : [['sponsor', 'Will you now or in the future require visa sponsorship?'], ['why', 'Why do you want to work at Acme?']];
  const missing = need.filter(([k]) => !data[k] || (Array.isArray(data[k]) && !data[k].length)).map(([, l]) => l);
  if (missing.length) { $('#alert').textContent = 'Errors Found: ' + missing.join('; '); return; }
  go(step === 'My Information' ? 'Application Questions' : 'Review');
}
// Escape closes any open menu, as on the real portal
document.addEventListener('keydown', e => { if (e.key !== 'Escape') return;
  document.querySelectorAll('[role=listbox]').forEach(l => l.remove());
  const h = $('#hear'); if (h) h.setAttribute('aria-expanded', 'false'); });
bar(); render();
</script></body></html>"""


GREENHOUSE = r"""<!doctype html><html><head><meta charset="utf-8"><title>Acme — Apply</title>
<style>body{font-family:sans-serif;max-width:720px;margin:20px auto} .field{margin:14px 0}
 .visually-hidden{position:absolute;left:-9999px} [role=option]{padding:4px;cursor:pointer}
 button[aria-pressed=true]{background:#335;color:#fff}</style></head><body>
<h1>Applied AI Engineer — Acme</h1>
<form id="form" onsubmit="return false">
 <div class="field"><label for="first_name">First Name*</label><input id="first_name" aria-required="true"></div>
 <div class="field"><label for="last_name">Last Name*</label><input id="last_name" aria-required="true"></div>
 <div class="field"><label for="email">Email*</label><input id="email" type="email" aria-required="true"></div>
 <div class="field" role="group" aria-labelledby="upload-label-resume" aria-required="true">
   <div id="upload-label-resume">Resume/CV*</div>
   <button type="button" onclick="document.getElementById('resume').click()">Attach</button>
   <input id="resume" type="file" class="visually-hidden" aria-label="Attach resume"
          onchange="setTimeout(() => document.getElementById('resume-name').textContent = this.files[0].name, 800)">
   <span id="resume-name"></span></div>
 <div class="field"><label id="country-label" for="country">Country*</label>
   <div class="select__control"><span id="country-value"></span>
   <input id="country" role="combobox" aria-labelledby="country-label" aria-expanded="false" aria-required="true"
          oninput="filter(this.value)"></div><div id="country-menu"></div></div>
 <div class="field"><label for="auth">Are you legally authorized to work in the country?*</label>
   <select id="auth" aria-required="true"><option value="">Select...</option><option>Yes</option><option>No</option></select></div>
 <div class="field" role="group" aria-labelledby="sp-q"><div id="sp-q">Will you require sponsorship?*</div>
   <button type="button" aria-pressed="false" onclick="press(this)">Yes</button>
   <button type="button" aria-pressed="false" onclick="press(this)">No</button></div>
 <div id="err" role="alert"></div>
 <button type="button" onclick="submitApp()">Submit application</button>
</form>
<script>
const COUNTRIES = ['India', 'Indonesia', 'Netherlands', 'Germany'];
function filter(q) {
  document.getElementById('country').setAttribute('aria-expanded', 'true');
  document.getElementById('country-menu').innerHTML = '<div role="listbox" aria-label="Country options">' +
    COUNTRIES.filter(c => c.toLowerCase().startsWith(q.toLowerCase()))
      .map(c => `<div role="option" onclick="pickCountry('${c}')">${c}</div>`).join('') + '</div>';
}
function pickCountry(c) { document.getElementById('country-value').textContent = c;
  document.getElementById('country').value = ''; document.getElementById('country-menu').innerHTML = '';
  document.getElementById('country').setAttribute('aria-expanded', 'false'); }
function press(b) { for (const x of b.parentElement.querySelectorAll('button')) x.setAttribute('aria-pressed', x === b); }
function submitApp() {
  const miss = [];
  for (const id of ['first_name', 'last_name', 'email']) if (!document.getElementById(id).value) miss.push(id);
  if (!document.getElementById('resume-name').textContent) miss.push('Resume/CV');
  if (!document.getElementById('country-value').textContent) miss.push('Country');
  if (!document.getElementById('auth').value) miss.push('authorized');
  if (!document.querySelector('[aria-pressed=true]')) miss.push('sponsorship');
  if (miss.length) { document.getElementById('err').textContent = 'Please fill: ' + miss.join(', '); return; }
  document.body.innerHTML = '<h1>Thank you for applying</h1><p>Your application has been received.</p>';
}
</script></body></html>"""


def serve():
    """Serve both forms on a free localhost port. Returns (server, base_url)."""
    pages = {"/workday": WORKDAY, "/greenhouse": GREENHOUSE}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = pages.get(self.path.split("?")[0], "not found").encode()
            self.send_response(200 if self.path.split("?")[0] in pages else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
