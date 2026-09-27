The application at {appdir}/ has been explored, and some of the form's questions
have no stored answer. They are in this JSON file:

  {qfile}

Write ready-to-tap answers for them. The applicant picks one (or writes his own)
on his phone before anything is sent.

WHAT TO READ — in one turn, before writing anything
  {resume}          his resume as tailored for THIS job (the facts you may use)
  {answers_file}    his stored answers: contact, location, work authorisation,
                    notice period, salary expectations, preferences
  {qfile}           the questions

POLICY (the relevant sections)
{policy}

JOB DESCRIPTION
{jd}

WHAT TO WRITE
For every question whose "options" list is EMPTY, write 2-3 complete answers into
its "options" array:
  - COMPLETE: an answer he can tap and be done with — no hints, no blanks to fill.
  - TRUE: every fact, number, employer, project and date must appear in the resume
    or the stored answers above. Nothing else — no skill, tool or claim they do
    not state. If they do not support an answer, write fewer options.
  - DIFFERENT: different projects or a different emphasis, not rewordings.
  - A multi-select question: each option is a complete combination.
  - A personal fact only he knows (interviewed here before, a referral, a
    deadline) that the stored answers do not settle: ONE neutral option stating
    the most likely answer plainly, for him to tap or overwrite.
  - A legal, compliance, sanctions, arbitration or data-consent declaration:
    LEAVE its options empty. Those are his alone to answer.

Write the whole file back in ONE Write call — valid JSON, nothing changed except
the "options" arrays. Then reply with the single word DONE.
