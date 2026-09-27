Screen a batch of job postings for {name} before any time is spent
tailoring. For each role decide KEEP or REJECT, and score FIT 0-100.
Everything you need is below; do not read other files.

HIS RESUME
{resume}

MARKETS AND SPONSORSHIP (from his operating policy)
{policy}

Citizenship: {citizenship}. Lives and works in {country}: anywhere else he needs
visa sponsorship, or a fully-remote role that hires from {country}.
Experience: {years} years.

KEEP/REJECT is about ELIGIBILITY. When in doubt, KEEP: a wrong keep costs one
tailoring session, a wrong reject loses the role for good.

REJECT only when one of these holds
- It will not sponsor, or requires existing work authorisation where he has none
  ("must be authorized to work in the US"). Silence on sponsorship is NOT a reject.
- Remote, but locked to a country he cannot work from ("Remote - US only").
- It is a different job: people manager, sales/account executive, recruiter, pure
  BI analyst, or a Spark/Databricks-platform, frontend or infra-only SRE role.
- It requires {max_years}+ years of experience, or principal/staff scope. Fewer
  required years than that is fine.
- A DOMAIN the JD states as mandatory ("must have a life-sciences PhD", "required:
  security clearance / cybersecurity background") that the resume does not show.
  A domain that is only preferred, or described as the team's area, is not a
  reject: when the technical requirements match, a missing domain only lowers FIT.
- Internship, new-grad, or contract-to-hire.

KEEP every other IC ML / AI / research / data-science role, including customer-facing
ones: forward-deployed, applied AI, solutions or "GTM" engineering at an AI company
are engineering roles, not sales.

Judgement calls, settled from past comparisons
- "Large-scale LLM training": fine-tuning, RL post-training or distributed training
  on the resume is adjacent — keep unless the role is pretraining-at-scale only.
- A Data Scientist title is a reject only for pure product/BI analytics.

FIT — score it strictly and separately from keep/reject; "when in doubt, keep" does
not apply here. Silently (never in your reply) list the role's MUST-HAVES (required skills, experience, domain,
"at least one of A, B, C" counts as one), then check each against the resume:
direct evidence counts fully, adjacent work counts half, a skill merely listed
without use counts little. Nice-to-haves move the score by a few points at most.
  90-100  every must-have shown directly, in production, at the level asked
  75-89   every must-have met, one or two only adjacently
  55-74   one must-have missing or weak
  35-54   several must-haves missing; the core overlaps
  0-34    a different profile
A keep with fit 30 is normal. Judge as a hiring manager reads it, not by
counting keywords.

Reply with ONLY the JSON array — no reasoning, no lists, no prose, no code fence:
[{"id": "<the id given>", "verdict": "keep"|"reject", "fit": <0-100>, "reason": "<12 words max>"}]

Roles:
