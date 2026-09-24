"""Fill hooks for ONE application — applications/<slug>/hooks.py.

Copied here from src/fill/hooks_base.py when this employer had no hooks yet (or
from another application's hooks.py for the same employer), then refined by the
exploration's Claude session — or by hand — only where the form needs it.

Order the engine asks in: this file > the employer's hooks > the platform module
(src/fill/platforms/<platform>.py, generic, never edited here) > the generic walker.

Describe the FORM's structure only — never answers: values always come from the
tool's resolver (answers.yaml, learned.yaml, what he approved on the phone).

Everything below is commented out, so this file changes nothing until a hook is
uncommented. Define only what this form needs; an attribute defined here hides the
platform module's (e.g. SUBMIT = None would hide the platform's Submit button).
"""
import re  # noqa: F401  (for hooks that match labels)

# ---------------------------------------------------------------- buttons & frame
# Button descriptors: text plus any stable attribute (id / name / auto / testid).

# FRAME_PATTERNS = ("company-careers.example.com/apply",)   # the iframe holding the form
# START = {"text": "Apply now"}                             # opens the form from the posting
# NEXT = [{"text": "Continue"}, {"text": "Next"}]           # per page, in order
# SUBMIT = {"text": "Submit application"}                   # seen on pass 1, pressed on pass 2

# def form_url(url):
#     """The form's URL, when the posting URL is not the form."""
#     return url.rstrip("/") + "/apply"


# ---------------------------------------------------------------- per step / page
# `flow` is the walker driving the form: flow.answers, flow.resolve(label),
# flow.warnings, flow.filled, flow.rep (the replay record), flow.page.
# Workday widgets for a Workday form:
#   from jobpilot.fill.platforms.workday import pick_listbox, type_prompt, set_date_any, _vis, _click

# def after_fill(page, step, flow):
#     """Runs after the generic fill of a step: a field it missed, a panel to
#     close, this employer's own wording for a question."""
#     if step.startswith("my information"):
#         from jobpilot.fill.platforms.workday import pick_listbox
#         sel = 'button[name="source"]'
#         got = pick_listbox(page, sel, ["Internet"], flow.warnings, "How did you hear")   # LinkedIn -> their "Internet"
#         if got:
#             flow.filled["How did you hear about us?"] = got

# def fill_step(page, step, flow):
#     """Take a whole step over. Return True when this step is done, None to let
#     the generic fill run instead."""
#     return None


# ---------------------------------------------------------------- whole driver (rare)
# def explore(page, ctx, answers, resolve, rep): ...     # return a walker-shaped dict, or None
# def submit(page, ctx, answers, resolve, rep, it): ...  # return a walker-shaped dict, or None
