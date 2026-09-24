"""A three-step application form served on localhost for the fill tests:
landing page with an Apply button -> page 1 (Next) -> page 2 (Submit)."""
import http.server
import threading

HTML = """<!doctype html><html><body>
<h1 id="h">Machine Learning Engineer</h1>
<div id="landing"><p>Great job.</p><button id="apply-btn" onclick="show('p1')">Apply now</button></div>
<div id="p1" style="display:none"><h2>Step 1 of 2</h2>
  <label for="fn">First Name*</label><input id="fn" required>
  <label for="col">Favourite colour*</label><input id="col" required>
  <button type="button" data-testid="next-1" onclick="if(!document.querySelector('#col').value){alert('x');return} show('p2')">Next</button>
  <button type="button" id="sub-hidden" style="display:none">Submit application</button>
</div>
<div id="p2" style="display:none"><h2>Step 2 of 2</h2>
  <fieldset><legend>Do you own a cat?*</legend>
    <label><input type="radio" name="cat" value="yes" required>Yes</label>
    <label><input type="radio" name="cat" value="no" required>No</label></fieldset>
  <button type="button" id="submit-btn" onclick="document.body.innerHTML='<p>Thank you for applying!</p>'">Submit application</button>
</div>
<script>
function show(id){ for (const x of ['landing','p1','p2']) document.getElementById(x).style.display = x===id?'block':'none';
  document.getElementById('h').innerText = id==='p1' ? 'Step 1' : id==='p2' ? 'Step 2' : 'Machine Learning Engineer'; }
</script></body></html>"""


def serve():
    """Start the fixture server on a free port; returns (server, url)."""
    class H(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            body = HTML.encode()
            self.send_response(200); self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/careers/apply/42"
