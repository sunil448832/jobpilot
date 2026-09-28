#!/usr/bin/env python3
"""
browser.py — the browser and what surrounds a fill, platform-independent:

    open_browser(pw)          real Chrome on the persistent profile, on its own
                              virtual display (Xvfb) so it never covers the desktop
    resume_path(...)          the resume file built for this application
    dead_posting(page)        a closed / withdrawn posting, checked before anything
    wait_quiet(frame)         until the page stops re-rendering
    enter_verification_code   a portal that emails a code at submit: ask him, type it
    notify_outcome(item)      one Telegram line per submit attempt

Exploring is session.py, filing is replay.py.
"""
import os
import re
import time

from jobpilot.core.config import cfg
from jobpilot.core.paths import TOOL, DATA, RESUME

QUEUE_DIR = os.path.join(DATA, "queue")
PROFILE_DIR = os.path.expanduser("~/.config/jobbot/chrome-profile")


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())).strip()


# ---------------------------------------------------------------- the browser

def _virtual_display():
    """A headed Chrome on an Xvfb display nobody looks at (never headless: portals
    treat that differently). Starts Xvfb once and reuses it. Returns ":N" or None."""
    import shutil
    import subprocess
    if not shutil.which("Xvfb"):
        return None
    n = int(cfg("browser.virtual_display", 99))
    disp, lock = f":{n}", f"/tmp/.X{n}-lock"
    if os.path.exists(lock):
        try:
            subprocess.run(["xdpyinfo", "-display", disp], capture_output=True, timeout=5, check=True)
            return disp
        except Exception:
            try:
                os.remove(lock)                       # stale lock from a crash
            except OSError:
                return None
    try:
        subprocess.Popen(["Xvfb", disp, "-screen", "0", "1400x1800x24", "-nolisten", "tcp"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return None
    for _ in range(20):
        time.sleep(0.25)
        if os.path.exists(lock):
            return disp
    return None


def _profile_busy():
    """PID of a live Chrome holding the persistent profile, or None (a stale lock does not count)."""
    try:
        target = os.readlink(os.path.join(PROFILE_DIR, "SingletonLock"))
        pid = int(target.rsplit("-", 1)[1])
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError, IndexError):
        return None


def _wait_for_profile(max_s=None):
    """One Chrome profile, several processes that may want it: wait for the holder."""
    max_s = max_s or cfg("browser.profile_wait_s", 1800)
    waited = 0
    while waited < max_s:
        pid = _profile_busy()
        if pid is None:
            return
        if waited == 0:
            print(f"  [browser] profile in use by Chrome pid {pid} (another run) — waiting", flush=True)
        time.sleep(5)
        waited += 5
    raise RuntimeError(f"browser profile still in use after {max_s}s")


def open_browser(pw):
    """Real Chrome on the persistent profile. browser.display: `virtual` (Xvfb, the
    default when installed), `offscreen`, or `own` (on the desktop, to watch it)."""
    os.makedirs(PROFILE_DIR, exist_ok=True)
    _wait_for_profile()
    mode = cfg("browser.display", "auto")
    args, env = ["--disable-blink-features=AutomationControlled"], None
    if mode in ("auto", "virtual"):
        disp = _virtual_display()
        if disp:
            env = dict(os.environ, DISPLAY=disp)
        else:
            mode = "offscreen"
    if mode == "offscreen" and env is None:
        args += ["--window-position=-32000,-32000", "--window-size=1280,1600"]
    kw = {"channel": "chrome", "headless": cfg("browser.headless", False),
          "viewport": {"width": 1280, "height": 1600}, "args": args}
    if env:
        kw["env"] = env
    return pw.chromium.launch_persistent_context(PROFILE_DIR, **kw)


# ---------------------------------------------------------------- around the form

def resume_path(answers, company_slug):
    """The resume built for this application (.docx parses best on ATS portals), else the base PDF."""
    files = answers["files"]
    key = "resume_docx_pattern" if files["upload_for_ats_portal"] == "docx" else "resume_pdf_pattern"
    for k in (key, "resume_pdf_pattern"):
        p = os.path.join(TOOL, files[k].format(company=company_slug))
        if os.path.isfile(p):
            return p
    fallback = os.path.join(RESUME, files["default_fallback_pdf"])
    return fallback if os.path.isfile(fallback) else None


def wait_quiet(frame, max_s=12, quiet=3):
    """Until the page has stopped changing for `quiet` quarter-second polls (a resume parse,
    a re-render): `max_s` seconds at most."""
    sig, still = None, 0
    for _ in range(int(max_s * 4)):
        frame.wait_for_timeout(250)
        try:
            cur = frame.evaluate("() => document.querySelectorAll('input,textarea,select,button').length"
                                 " + '/' + document.body.innerText.length")
        except Exception:
            return
        if cur == sig:
            still += 1
            if still >= quiet:
                return
        else:
            sig, still = cur, 0


DEAD_RX = re.compile(r"page you are looking for (doesn.t|does not) exist|has been filled|"
                     r"no longer accepting applications|no longer available|job (is|has been) closed|"
                     r"position (has been|is) (closed|filled)|this job is closed|job (has )?expired|"
                     r"posting (has been )?removed|job (you are looking for|requested) (is|was) not found", re.I)


def dead_posting(page, wait_s=15):
    """The closing phrase when the opened page is a dead posting, else None. Only a
    short page counts: a live JD can mention 'no longer available' in passing."""
    for _ in range(wait_s):
        for f in page.frames:
            try:
                body = f.inner_text("body") or ""
            except Exception:
                continue
            m = DEAD_RX.search(body[:4000])
            if m and len(body) < 3000:
                return m.group(0)
            if len(body) >= 3000:
                return None
        page.wait_for_timeout(1000)
    return None


CODE_INPUTS = ('input[autocomplete="one-time-code"], input[maxlength="1"], '
               'input[name*="code" i], input[id*="code" i], input[aria-label*="code" i]')


CAPTCHA_FRAMES = re.compile(r"hcaptcha|recaptcha|challenges\.cloudflare|arkoselabs|funcaptcha", re.I)


def captcha(page):
    """A captcha challenge showing on the page (a frame of a captcha service, drawn large
    enough to be a puzzle, not its invisible badge): only a person can go on."""
    for fr in page.frames:
        if fr is page.main_frame or not CAPTCHA_FRAMES.search(fr.url or ""):
            continue
        try:
            box = fr.frame_element().bounding_box()
        except Exception:
            continue
        if box and box["width"] > 150 and box["height"] > 150:
            return True
    return False


CODE_RX = re.compile(r"verification code|security code|code (was|has been) sent|enter the .{0,12}code", re.I)


def enter_verification_code(frame, it, press_submit):
    """The portal is waiting on an emailed code (the caller saw CODE_RX in what the page
    newly says): ask him on Telegram, type it, press the same Submit again (`press_submit()`).
    True when a code was entered; False when none came (or there is no box for it)."""
    from jobpilot.review.ask import ask
    code = re.sub(r"\s+", "", ask(f"code-{it['id']}",
                                  f"{it['company']} — {it['role']}: the portal emailed a verification code. "
                                  f"Paste it here.", hint="from the email that just arrived") or "")
    if not code:
        it.setdefault("warnings", []).append("verification code not received in time — not submitted")
        return False
    boxes = frame.locator(CODE_INPUTS)
    if boxes.count() >= len(code):
        boxes.first.click(force=True)
        frame.page.keyboard.type(code, delay=90)
    elif boxes.count():
        boxes.first.fill(code)
    else:
        it.setdefault("warnings", []).append("verification code asked for but no input found")
        return False
    wait_quiet(frame, max_s=8)
    try:
        press_submit()
    except Exception as e:
        it.setdefault("warnings", []).append(f"code entered, Submit not pressed again: {e}")
    it.setdefault("warnings", []).append("verification code entered")
    return True


def notify_outcome(it):
    """One Telegram line per attempt, so nothing needs watching from a terminal."""
    try:
        from jobpilot.core.daily import telegram, form_link
    except Exception:
        return
    link = form_link().replace("/?", f"/a/{it['id']}?")
    head = f"<b>{it.get('company', '?')}</b> · {str(it.get('role', ''))[:60]}"
    st = it.get("status")
    if st == "submitted":
        text = f"✅ <b>Filed</b> — {head}"
    elif st == "needs_input":
        n = len([q for q in it.get("questions", []) if q.get("status") != "answered"])
        text = f"❓ <b>Needs {n} answer{'s' if n != 1 else ''}</b> — {head}\nAnswer + Approve:\n{link}"
    elif st == "expired":
        text = f"🗑 <b>Posting closed</b> — {head}\nThe employer took it down. Nothing was sent."
    elif st == "approved":
        text = f"⏳ <b>Not filed yet</b> — {head}\n{it.get('fail_reason', '')}"
    else:
        text = f"⚠️ <b>Failed</b> — {head}\n{it.get('fail_reason', '')}\nScreenshot + Retry:\n{link}"
    try:
        telegram(text)
    except Exception:
        pass
