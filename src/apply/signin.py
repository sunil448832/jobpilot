"""signin.py — sign in to a careers site once, by hand, in the form filler's own browser
profile, so its hidden fills find the session (Google careers, a portal's candidate account).

    ./jobpilot signin <url or google>     opens the profile on the desktop at that page;
                                          sign in, then close the window

The password is typed by him in the window; jobpilot never sees or stores it. The profile is
the one fills use (browser.executable: Chrome's data/chrome-profile). A fill running at the
time holds the profile: this waits for it to finish.
"""
import sys

from jobpilot.apply.explore_agentic import browser as B

SHORT = {"google": "https://www.google.com/about/careers/applications/",
         "microsoft": "https://apply.careers.microsoft.com/careers",
         "talentmate": "https://www.talentmate.com/"}


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: ./jobpilot signin <url | google | microsoft | talentmate>")
    url = SHORT.get(sys.argv[1].lower(), sys.argv[1])
    from playwright.sync_api import sync_playwright
    B._wait_for_profile()
    with sync_playwright() as pw:
        kw = {"headless": False, "viewport": None, "args": ["--start-maximized"]}
        if B.BROWSER_EXE:
            kw["executable_path"] = B.BROWSER_EXE
        else:
            kw["channel"] = "chrome"
        ctx = pw.chromium.launch_persistent_context(B.PROFILE_DIR, **kw)   # on his desktop, not Xvfb
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        print(f"  sign in at {url} in the window that opened, then close it.")
        try:
            ctx.wait_for_event("close", timeout=0)
        except Exception:
            pass
        print("  saved — fills will use this sign-in.")


if __name__ == "__main__":
    main()
