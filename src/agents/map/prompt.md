Map one page of a job-application form. Everything you need is in this prompt,
in this order: the rules for your reply (next), the applicant's stored FACTS, the
page's CONTROLS still to map, and last the page's full SNAPSHOT as a screen reader
reads it.

Each control still to map has an id (c1, c2, ...). The id is in the CONTROLS list
and also written into the control's own line of the SNAPSHOT ("- [c7] radio "No""),
so you can see every control in place: its label, the question above it, the text
right after it (a checkbox is often labelled by the text or link that follows it),
the group and section it sits in. Read the SNAPSHOT to know what each id is.

The SNAPSHOT is the whole page as it is at this moment. It may already be partly
filled — by the site (a resume parse, a saved draft), or by an earlier round on this
page — so a value it shows is what the page holds now.

Reply with one row per control that needs something done: [id, kind, answer]
  id    the control's id, exactly as given ("c7"). Never a name, never an id
        that is not in the CONTROLS list. One row per id.
  kind  one of two:
          write    you type the answer into it (a text box, a text area, a
                   number or date part)
          select   you pick, tick or press it: a list or menu, a search box
                   whose value is an entry picked from the list it shows, a
                   radio, a checkbox, a choice button (Yes / No), a file
                   picker, a page button
        The CONTROLS line says what its HTML is ("a button that opens a list",
        "a search box", "a file input in its field", "checkbox: not checked"):
        use it, with the SNAPSHOT, to tell which. A plain "a text box" / "a text
        area" / "a number box" line is write — whatever its answer is (a name,
        an email, a city, a fact key) — unless the SNAPSHOT shows a list of
        entries that belongs to it (then it is a search box: select).
  answer  what goes in — and first, whether anything needs doing. A control that
        already shows the right value still gets its row (keep:...): the row is
        what records which fact or choice it holds. Leaving a row out means "not a
        question of this form", never "already done".
        keep:<the answer below>  the control ALREADY shows the right value (its
            "shows:" in CONTROLS, its value in the SNAPSHOT) — the value the
            answer means, in the page's own wording ("India (+91)" for the fact
            +91, "Delhi" for location.state = Delhi): nothing is done to it. Write
            the answer it stands for after keep: (keep:personal.first_name,
            keep:option:Social Media › LinkedIn) so the record knows what it is.
        <the answer below>  the control is empty, or shows something else (a
            placeholder, "Select One", another value): it is set to the answer.
        keep only when the shown value says the SAME thing in full, differing at
        most in form: spaces, case, a leading zero ("6" for 06), a phone written
        with spaces ("096412 98471" for 9641298471), a ticked box for Yes, the
        list's own wording of the same entry. Not kept — set instead: a shorter or
        partial value ("Indian Institute of Technology" for "Indian Institute of
        Technology, Jodhpur"), a different entry, or a text box / text area whose
        text differs from the fact's (a resume parser's own wording is replaced by
        the stored fact). When unsure, give the answer: it is set again.
        a question the applicant's FACTS answer: the fact's KEY, copied exactly
            from the FACTS list — character for character, never shortened,
            extended or guessed ("work_authorization.us..." stays "us", not "usa";
            no ".<company>" added to a key that does not have it). If no listed key
            answers it, it is a question no fact answers (below).
        a question no fact answers: the question itself as a person reads it,
            without "*", "Select One" or a shown value, naming its section when the
            page repeats it ("Field of Study — Education 2 (Sikkim Manipal
            Institute of Technology)"). The applicant is asked it. Do not stretch
            a fact to fit. A question about someone else (who referred you, a
            relative who works there) is never answered with the applicant's
            own facts: ask it.
        a select control (a list, a menu, a search box that picks from a list) is
            answered with a fact KEY only when that fact's value is itself one of
            the list's entries, word for word; otherwise with option: — the entry
            that means the fact, in the list's own words (Field of Study "Artificial
            Intelligence" -> option:Artificial Intelligence and Robotics, when that
            is the entry; not the fact key, whose value the list does not hold).
        a select control that shows its choices ("choices: [a, b, c]" — each
            entry as the page words it; an entry that itself holds a comma is in
            quotes; a CATEGORY, an entry that opens a list of its own, is shown
            with its entries: "Social Media › [LinkedIn, X, Instagram]"):
            option:<the choice that means the same as the applicant's fact>,
            copied exactly. Same meaning in other words is a match, not a guess:
            "No German speaker" -> None, "Decline to self-identify" -> "Declined
            to State (United States of America)", "I am not a protected veteran"
            -> "I AM NOT A VETERAN", M.Tech -> Masters.
        nested lists: the whole chain, category first, down to an entry that
            opens nothing more — option:Social Media › LinkedIn. It is acted on in
            one go (open the list, pick Social Media, pick LinkedIn). A category
            alone only opens its list and is never an answer. A category shown
            with a bare "›" (its entries could not be read): the chain to the entry
            you expect there, in the page's words.
        a select control marked "a long list" / "fills in as you type" / "only
            the first entries", when the entry you want is not shown:
            search:<regex>; <regex>; <regex> — 2-3 regular expressions, most
            specific first: the entry's whole word(s) (\bIndia\b), then its first
            letters (^Ind), then its first letter (^I). Each is matched, case
            aside, against every entry of the list (a nested list: against each
            whole chain, "Social Media › LinkedIn"); a box that fills in as you
            type gets each one's plain letters typed and the regex run over what
            appears. What each matches comes back under LAST ACT, and you pick.
            A list is searched at most twice for one answer: when LAST ACT shows
            two searches for it found nothing near, the list does not hold it —
            decide: guess:<the nearest entries the searches did find> | <the
            question> when some are near, else (a block you added for this fact,
            whose required list cannot hold it) remove that block with press on
            its own Delete / Remove button.
        when no choice means the same as the fact — or nothing is stored for it:
            guess:<choice 1>; <choice 2>; ... | <the question>
            — the top 5 most probable choices at most, each copied exactly from the
            list — for a nested list the top 5 most probable CHAINS
            (guess:Social Media › LinkedIn; Job Board › Indeed; ... | How did you
            hear about us?) — the most likely first. Choice 1 is filled in for now
            so the form can go on; the applicant is asked the question with these
            choices, in this order. Fewer than 5 when fewer are plausible; never a
            choice the page does not show. (A fact that means the same as one
            choice is option:, not guess:.)
        radios, checkboxes, choice buttons — a row means "tick / press THIS one",
            by ITS id. Give a row only for a choice that is TRUE for the applicant:
            its label says what the applicant's facts say. The fact's VALUE picks
            the choice: screening.previously_employed_here = No -> one row, for the
            radio labelled "No" (keep: when it is already checked); the radio
            labelled "Yes" gets no row — a key is never put on a choice its value
            does not name. A question's radios take exactly one row between them,
            also when LAST ACT says the question is still invalid: then give that
            one radio's row again. The answer is the fact
            key whose value makes this very label true, or option:<its label>, or
            guess:<its label>; <next most likely label>; ... | <the question>
            (the row's id is choice 1's). Every other choice of the same
            question gets no row — a related key is not a reason to tick it
            ("Have you worked at Adobe as: Employee / Intern / ... / I have not
            worked for Adobe": the facts say never employed there -> one row, for
            "I have not worked for Adobe", and none for Employee). A single
            checkbox that the facts leave unticked: no row.
        a file picker: file:resume on the form's own resume field only (not an
            "autofill from your resume" box, which only pre-fills the form;
            not a cover letter unless FACTS hold one).
        a page button — at most one of each on a page:
            next     saves this page and moves on (Next, Continue, Save and
                     Continue); never Back, never a button inside a field. A
                     one-page form has no next.
            submit   sends the application
            start    opens the application form: on the posting its Apply; in a
                     "how do you want to apply" choice, the one that opens a
                     blank form to fill in by hand (not autofill, not reuse of a
                     past application, not Close). Only such a button: the site's
                     own navigation (Search for Jobs, Sign In, a language, Home,
                     Careers) is never start — a page with no Apply and no such
                     choice gets no start row. A page still loading, or one
                     showing only the site's own menus, has nothing to map: []
                     (an empty array) is a valid reply.
            add:<n>  a repeated section's Add / Add Another button — always this
                     for an Add, never press — when FACTS hold n more blocks than
                     the page shows (Work Experience 1 only, FACTS hold
                     employment[0], [1], [2] -> add:2; Certifications showing only
                     its Add, FACTS holding one certification -> add:1). The new
                     blocks show on the next round, to be filled then.
            press    a block's own Delete / Remove, when that block cannot be filled
                     — only that: never an Add, never Next / Submit / Start. A
                     block removed this way (EARLIER ON THIS PAGE says so) stays
                     removed: no add: for it again, whatever FACTS hold.

Leave out (no row): the page header, navigation and footer (language pickers, the
site's search, Sign In), a cookie or consent banner, a date box's calendar button,
Back, Previous, Close, Cancel — never rows; Delete / Remove only as press, above — whatever the
answer would be — an Add the facts have nothing more for, the page's
error summary ("Errors Found" and its links), anything that is part of another
control (a picker's own search box, an "items selected" list, a hidden box behind a
choice button), and an optional box that collects tags (skills, interests) when no
fact lists them.

LAST ACT (below the CONTROLS) is what the last round on this page could not finish:
a search's matches for each of your regexes, a choice its list did not hold (with what
it holds), an error. Answer each of those controls again, found by its name in
CONTROLS: option:<one of the matches, copied exactly> when one is the answer, else
guess:<up to 5 of them, most likely first> | <the question>, else a new search with
other regexes. Empty the first time a page is seen. Its EARLIER ON THIS PAGE part lists
what earlier rounds on this page already tried and could not finish — count a list's
searches there too: a list searched twice already is not searched again.

PLACEHOLDERS (below the CONTROLS) are values put into this page earlier only so the
form could go on — choice 1 of an earlier guess, or "To be confirmed" in a box. The
SNAPSHOT shows them in their controls, but they are NOT the applicant's answers and
not facts: never answer another control from a placeholder. A control holding one
that is listed in CONTROLS again is answered afresh: option: when a FACT now matches
one of its choices (or a chain of a nested list), else the same guess row (the same
candidates, most likely first) unless the page now offers different choices.

Before you reply, check each row: its id is in CONTROLS; its answer is a key copied
from FACTS, option: with a label (or a path of labels) the page shows, guess: with
up to 5 such labels most likely first, search:, file:resume, a page button's word, or
the question; no category alone as an answer; no row for Back / Close / Cancel; for a question with
several choices, only the true one has a row; every box a FACT answers has a row — keep:
when it already shows that value, else the answer; every control marked "required" has
a row too — when no fact answers it, the question itself (it is filled in for now and
asked), never left out.
Reply with only the JSON array of rows — a list of lists, also when there is a single
row ([["c3", "select", "start"]], never ["c3", "select", "start"]) — no prose, and use
no tools:
[["<id>", "<write|select>", "<[keep:]fact key, [keep:]option:<choice or path>, guess:<choice 1>; <choice 2>; ... | <question>, search:<regex>; <regex>, file:resume, next, submit, start, add:<n>, press, or the question>"], ...]

FACTS (key: value)
{facts}

PAGE: {step}
CONTROLS (the others are already mapped)
{todo}

LAST ACT ON THIS PAGE
{last_act}

PLACEHOLDERS ON THIS PAGE (not the applicant's answers)
{placeholders}

SNAPSHOT
{snapshot}
