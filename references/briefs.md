# Round briefs

Copy a template into `brief-rN.md` and fill in the `{…}` placeholders. Every brief
starts with the **common header**. Each answer must end with exactly one
fenced ```json block in the format given. The scripts parse the **last** such block
in the file.

## Common header (every round)

~~~markdown
# Council: {title} · Round {N}: {round name}

You are **panelist {LETTER}** on a council of {K} expert panelists. Each panelist is a
different AI model, and all are anonymous. Other panelists are referred to only by letter.
Do not reveal or guess which model you or any other panelist is.

**Goal:** {one-sentence goal}
**Dimensions** (score each 1-10, half points allowed):
{- name: one-line definition}

## Read first
- {abs}/dossier.md: goal, constraints, verified facts, known unknowns. Authoritative.
{- other paths}
{- previous rounds: {abs}/r1/*.md, {abs}/factcheck-r1.md, ...}

## Rules
- Only create or modify your answer file, {abs}/r{N}/{LETTER}.md. Never open {abs}/_moderator/.
- You may read anything and run read-only commands and tests to back up claims
  (cap parallel workers at 2). Do not edit the project, commit, push, install packages
  or contact external services.
- Respect every hard constraint in the dossier. If you think one is wrong, say so
  explicitly, but still give an answer that respects it.
- Be concrete: file and line, exact wording (OLD -> NEW), numbers with the arithmetic shown.
  Separate what you verified from what you are assuming.
- Known unknowns: don't guess. Give the fallback and what changes if the answer arrives.
- Word limit: {limit} words, not counting the JSON block.
~~~

## R1: Independent proposals (limit ~1500 words)

~~~markdown
## Your task
Without seeing the other panelists' work, give your best answer to the goal.

1. **Big idea**: one sentence.
2. **Proposal**: {mode-specific deliverable; see below}.
3. **Why**: the reasoning and evidence. Mark which claims you verified yourself.
4. **Risks and what you'd cut**.
5. **Questions for the user or client**, each with your fallback.
6. **Scores**: "current" (the state today, if anything exists) and "predicted" (if your
   proposal is done well), per dimension, each with one line of justification.

End with:
```json
{"big_idea": "...", "scores_current": {"<dim>": 7, ...} | null,
 "scores_predicted": {"<dim>": 9, ...}, "top_points": ["...", "...", "..."]}
```
~~~

**Mode-specific deliverables:**
- **code:** the approach and why. The affected files and functions. A sketch of the key
  changes (diff-level where it matters). The test plan. Rollout and migration. Rejected
  alternatives, each with its reason.
- **strategy:** the recommendation. Options considered, with tradeoffs. The decision
  criteria. What would change your mind. First 3 concrete steps.
- **redesign:** the concept. The format and scope. Structure (outline, storyboard or
  architecture). What you'd keep from today and what you'd drop. Effort. How you'd
  validate it.
- **artifact:** findings ranked by severity, each tied to a location (scene, timestamp,
  section or element), with a concrete fix. What already works and must be kept.

## R2: Debate (limit ~1200 words)

The moderator adds to the brief:
- the consensus list;
- the numbered disputes, each with a neutral one-line summary of every side, attributed by letter;
- `factcheck-r1.md`.

~~~markdown
## Your task
Read every other panelist's R1 answer ({abs}/r1/*.md) and {abs}/factcheck-r1.md.

1. **Corrections accepted**: any fact-check items about your R1 answer, and what you changed.
2. **Critique**: the strongest objection to each other panelist's proposal, and the best
   idea in each that you would take.
3. **Disputes**: for every D-number, your final position and the reason. Say plainly if you
   changed your mind, and what changed it.
4. **Revised proposal**: your full updated proposal. It must stand on its own; don't write
   "same as R1".
5. **Revised predicted scores**.

End with:
```json
{"disputes": {"D1": {"position": "short label of the side you take", "changed": false,
  "why": "one line"}, ...},
 "adopted_from": {"<letter>": ["idea", ...]},
 "scores_predicted": {"<dim>": 9, ...}}
```
~~~

## R3: Score and rank (limit ~900 words)

~~~markdown
## Your task
Read every panelist's **R2** answer ({abs}/r2/*.md) and {abs}/factcheck-r2.md.

1. **Score every revised proposal, including your own**, on every dimension (1-10, half
   points allowed). Score what's on the page, not what it could become.
2. **Rank the other panelists' proposals** from best to worst. Do NOT include your own
   letter.
3. **Merge list**: the top 5 ideas from other proposals that the winner should take on.
4. **Remaining disagreements** that the user must decide. Put each as a question with the
   options, neutrally.

End with:
```json
{"scores": {"A": {"<dim>": 8.5, ...}, "B": {...}, ...},
 "ranking": ["<best other letter>", "<next>", ...],
 "merge": ["idea (from X)", ...],
 "user_calls": [{"question": "...", "options": ["...", "..."]}]}
```
~~~

`scripts/tally.py` rejects ballots that rank the voter's own letter or leave out
other letters. If that happens, ask the panelist to fix it once (retry1).

## R4a: Merge (winner only; limit ~2000 words)

~~~markdown
## Your task
Your proposal won the ranked vote. Write the **final merged plan**:
- your R2 proposal as the base;
- the merge list from every R3 ballot (in {abs}/r3/*.md), adopting what fits and
  listing what you rejected, each with a reason;
- every settled dispute applied as settled;
- every unsettled one kept as a clearly marked option for the user.

It must be complete enough to execute without reading earlier rounds.

End with:
```json
{"adopted": ["..."], "rejected": [{"idea": "...", "why": "..."}],
 "open_options": ["..."], "scores_predicted": {"<dim>": 9, ...}}
```
~~~

## R4b: Red team (everyone except the winner; limit ~900 words)

~~~markdown
## Your task
Attack the merged plan at {abs}/r4/{WINNER}.md. Find what is wrong, missing, unverified, or
will fail in practice. Check its claims yourself where you can.

1. **Verdict**: `ship`, `ship-after-fixes` or `block`.
2. **Must-fix**: issues that would make it fail, each with a concrete fix.
3. **Should-fix**: anything else worth fixing.
4. **Final scores** for the merged plan, assuming your must-fixes are applied.

End with:
```json
{"verdict": "ship-after-fixes",
 "issues": [{"issue": "...", "fix": "...", "severity": "must|should"}],
 "scores": {"<dim>": 9, ...}}
```
~~~

## Dossier extras by mode

- **code:** repo path and branch; the commit SHA you froze on; how to run the tests;
  architecture notes; relevant files; a "do not touch" list.
- **strategy:** the decision owner; deadline and budget; what's already been tried; success
  criteria.
- **redesign:** what exists today and its known problems; what must survive the redesign;
  production constraints.
- **artifact:** the audience; the artifact path; per-section descriptions plus
  `materials/` frames or excerpts; facts you verified at full resolution; the brand and
  claim rules.
