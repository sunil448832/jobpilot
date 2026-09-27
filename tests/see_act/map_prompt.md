Map one page of a job-application form. Below: the page as a screen reader reads
it, and the applicant's stored facts.

PAGE: {step}
{snapshot}

FACTS
{facts}

One entry per control that takes an answer: [name, kind, fact]
  name  copied exactly from the snapshot, character for character.
        For radios or Yes/No buttons: the name of the `group` they sit in.
        For a file: the visible button that opens the file picker ("Attach").
  kind  text | search-and-pick | dropdown | native-select | radio-group |
        yes-no-buttons | checkbox | file
        search-and-pick: a combobox you type into. dropdown: a button showing
        "Select One" or a value. native-select: a combobox whose options are listed.
        radio-group: radios in a group. yes-no-buttons: buttons in a group.
  fact  the key of the fact that answers the question as asked, else null.
        A key, never a value. Do not stretch a fact to fit.

Skip navigation (Next, Save and Continue, Back, Sign In, Create Account), Submit,
hidden file inputs, and menu options.

Reply with only the JSON array, no prose:
[["<name>", "<kind>", "<fact or null>"], ...]
