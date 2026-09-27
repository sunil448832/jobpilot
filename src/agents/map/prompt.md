Map one page of a job-application form. Below: the page as a screen reader reads
it, the controls to map (each with what its HTML says about it), and the
applicant's stored facts.

PAGE: {step}
{snapshot}

MAP THESE CONTROLS (the others are already mapped)
{todo}

FACTS (key: value)
{facts}

One entry per control above that matters: [name, kind, answer]
  name  the quoted name from the list above, exactly, without the role word
        before it — a "#2", "#3" there is part of it (the page has several
        controls of that name). For radios or buttons in a group: the group's
        name. For a file: the visible button that opens the file picker. For a
        dropdown whose name also carries what it shows ("State Select One
        Required"): only its label part ("State"), so the name still fits after
        a pick. A list button listed above as "<section> › <its name>": the
        section, " › ", then only its label part, without what it shows
        ("Voluntary Self-Identification › Please select your gender. Select One
        Required" -> "Voluntary Self-Identification › Please select your gender.");
        when its name is only what it shows ("<question> › Select One
        Required"), that whole name, as listed. Leave out an "Error" prefix the page adds to a field it flags
        ("Error To (Actual or Expected)" -> "To (Actual or Expected)").
  kind  follows the control's role in the list: a textbox is text, a spinbutton
        number, a combobox search-and-pick (native-select only when its HTML is a
        <select>), a group of radios radio-group, a group of buttons yes-no-buttons.
        dropdown is only ever a button. A question:
          text             type into it
          number           a spinbutton (a date part)
          search-and-pick  you type, then pick from the list that appears (the
                           value is the picked entry, not the typed text) — often
                           a textbox with a list or icon in its field
          dropdown         a button that opens a list
          native-select    a <select>
          radio-group      radios in a group (pick one)
          yes-no-buttons   buttons in a group
          checkbox-group   several checkboxes under one question: the answer is
                           option:<the box to tick>
          checkbox         a single box: the answer is the fact whose Yes / No sets
                           it ("I currently work here" in Work Experience 2 ->
                           that job's own current fact), or the question
          file             the form's own resume field — not an "autofill from
                           your resume" box, which only pre-fills the form
        a button of the page itself — at most ONE of each on a page:
          next             saves this page and moves on to the form's next page
                           (Next, Continue, Save and Continue) — never Back /
                           Previous, and never a button inside a field (Upload,
                           Autofill, Attach, Locate me). A one-page form has no next.
          submit           sends the application (Submit, Submit application)
          start            opens the application form from the job posting: on the
                           posting its Apply button; in a "how do you want to apply"
                           choice, the one that opens a blank form to fill in by
                           hand (not autofill from a resume, not reuse of a past
                           application, not Close)
  answer  for a question: EITHER the key of the fact that answers it as asked (a
        key from FACTS, never a value) — OR, when no fact answers it, the question
        itself as a person reads it, without "*", "Select One" or a shown value,
        naming its section when the page repeats it ("Field of Study — Education
        2 (Sikkim Manipal Institute of Technology)"); the applicant is then asked it. Do not stretch a fact to fit. A question
        about someone else (who referred you, a relative who works there) is never
        answered with the applicant's own facts: ask it.
        A control that offers choices (a menu, a group) is only ever answered with
        one of its choices — never a value typed in:
          its choices are listed ("choices: [...]", a group holding ...):
              option:<the choice that means the same as the applicant's fact>,
              copied exactly (a category is written "<category> › <choice>").
              Same meaning in other words is a match, not a guess: "No German
              speaker" -> None, "Decline to self-identify" -> "Declined to State
              (United States of America)", "I am not a protected veteran" -> "I AM
              NOT A VETERAN", M.Tech -> Masters;
              guess:<the nearest choice> | <the question> when none does — it is
              filled in for now and the applicant confirms it.
          "a long list" / "fills in as you type": search:<term>; <term>; <term> —
              2-3 short search terms for the entry you want, most specific first
              (India; Ind; +91 — Artificial Intelligence; Artificial; AI). The list
              is searched with each and what it holds comes back for you to pick.
        For next / submit / start: null.

Leave out (they need no entry): controls in the page header, navigation or footer
(language pickers, the site's search, "Search for Jobs", Sign In), a cookie or
consent banner (Accept, Deny, Cookie settings), a date box's own calendar button, Back, Close, Cancel, Add, menu
options, the page's error summary ("Errors Found" and its links), anything
that is part of another control (a picker's own search box, an "items selected"
box), and an optional box that collects tags from a list (skills, interests)
when no fact lists them — the resume already carries them.

Buttons: one next (the one that moves forward), no Back. A question no fact answers
gets the question itself — never an invented key.
Reply with only the JSON array, no prose:
[["<name>", "<kind>", "<fact key, option:<choice>, search:<terms>, the question, or null>"], ...]
