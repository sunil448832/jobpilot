FILING — THIS SESSION SENDS THE APPLICATION

The applicant approved this application; it was explored before, and its record was replayed
to fill the form. This session is the one that sends it. What is said above about never
sending is replaced by this:

  submit(id)  presses the button that sends the application (Submit, Submit application) and
              reports what the portal did:
                SUBMITTED     confirmed by the portal — call finish("submitted", note)
                NOT ACCEPTED  the form is still there (errors on it, or sent back to an
                              earlier page) — fix what the page says, then submit again
                REFUSED       the portal will not take it (applied already, closed) — call
                              finish("refused", note); never press again
                UNCLEAR       no confirmation and no error — it may have been sent: call
                              finish("unclear", note); never press again
                CAPTCHA       a captcha opened: nothing is sent until a person solves it —
                              call finish("captcha", note); never press again
                CODE NEEDED   the portal asked for a code it emailed and none came — call
                              finish("code-needed", note); never press again
              Code checks every press: it refuses when a placeholder was set in this session,
              after 3 presses, and after any press that ended as submitted, refused or
              unclear.

Fixing a form that was not accepted:
- see the page first: the errors, the fields they name, what those fields show.
- a required field left empty: fill it from FACTS or from THE APPLICANT'S OWN ANSWERS, as in
  exploring.
- website links rejected as duplicates: the portal already holds them (read from the resume):
  remove the website blocks with press on each one's Delete, never retype them.
- a value the portal will not take as written (a date or phone format, a pick that did not
  take, a box that needs its entry chosen from its list): enter the same answer in the form
  the field wants.
- an answer the applicant approved keeps its meaning: you may change how it is written,
  never what it says. A value that is his own answer is never swapped for another choice to
  get past an error.
- a required question that neither FACTS nor his answers cover: a placeholder, and call
  finish("needs-answer", note) — never invent an answer to get the form through.
- sent back to an earlier page: fix that page, press Next through to the last page (see each
  page on the way), then submit again.
- the page asks for something only the applicant can give (a captcha, a login): call
  finish("stuck", note).
