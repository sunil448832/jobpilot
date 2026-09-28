RESUMING — A FORMER SESSION'S RECORD EXISTS

A former session worked on this application and its steps were recorded (calls.json). You
need not start from scratch:

  replay()  redoes the record from the page shown now: each page's recorded steps, a check
            that every field holds what it held, then the page's Next — page after page,
            until a page differs, a page the record does not know, a Next that does not
            move on, or the page where the record ends. It reports where it stopped and why,
            and, on a page that differs, what the record did there.

Start with replay. Where it stops, see the page and carry on as usual: fix what differs, then
call replay again to go on with the record (a page already redone is not redone, only its
Next is pressed), or fill the rest yourself. Pages replay reports as redone are filled as the
record has them. Where the record ends, the application goes on as in any session.
