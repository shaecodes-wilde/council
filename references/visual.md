# Council report

Build a live report from the run directory, or from optional display annotations
in `data.json` beside its `_moderator/run.json`:

```sh
python3 -I <actual-skill-directory>/scripts/build_visual.py <run> <run>/breakdown.html
python3 -I <actual-skill-directory>/scripts/build_visual.py <run>/data.json <run>/breakdown.html
python3 -I <actual-skill-directory>/scripts/build_visual.py --inline <run> <run>/breakdown-inline.html
```

The renderer imports sibling helpers from its actual script directory, including
under isolated Python (`-I`). It uses only the standard library. Output is a
standalone HTML document: no network requests, external fonts, scripts or CSS.
Exit 0 means the report was written. Exit 2 reports invalid input or an I/O error
to stderr and does not write a new report. An older output file is not deleted
when a new render fails. Output cannot overwrite the input, manifest, raw
outputs, accepted artifacts, frozen sources/evidence, registered tally or
verification references. Existing-file identity checks also reject hard-link
aliases of these inputs. Atomic replacement preserves other links even if an
alias is introduced after validation. Check the command's exit status before publishing.

## Authoritative live inputs

Follow [contracts.md](contracts.md). The renderer reads only explicit manifest
references and accepted stage artifacts; it never discovers a tally, proposal,
ballot, final plan, or transcript by globbing or conventional filenames.

- The **entire final plan** comes from `final_plan = {stage, letter}`, selecting
  an accepted `synthesis` or `repair` artifact. The exact artifact text, including
  its structured footer, is escaped and rendered visibly as prewrapped text.
  This preserves the full handoff without depending on a Markdown package or
  interpreting supplied HTML. A complete lifecycle without an accepted final
  plan or registered final verification is rejected.
- `vote.tally = {path, sha256, epoch}` selects the tally. Its hash, epoch, roster,
  accepted R3 `provenance`, and accepted R2 `proposal_provenance` must match.
  The renderer also recomputes the tally from accepted ballots to reject a
  fabricated or stale result, even if a report annotation claims a winner.
- `accepted_paths` checks accepted artifacts against successful matching tasks,
  current epochs, target identities, dependencies, raw hashes and published
  hashes. Frozen sources and other referenced report artifacts are checked too.
  All references must stay inside the run and cannot traverse symlinks.
- `final_verification` points to the hashed moderator check document. Its
  `final_sha256` must identify the selected plan. Finding ledger provenance and
  every other seat's accepted verification artifact are checked. Resolved and
  rejected findings need matching critic confirmation for that exact version.
  The report merges these dispositions into the original finding descriptions
  and evidence. Missing/stale verification cannot establish current approval.
  New exact-hash critic `remaining_blockers` and non-ship verdicts are evaluated
  by the same `evaluate_final_objections` helper used by the runner. Unassessed
  objections remain visible and unresolved. A bare blocking verdict becomes a
  `verdict` objection. Moderator `verifier_objections` assessments may reject an
  unsupported objection with rationale and hash-checked frozen/accepted
  counterevidence; an unsupported model assertion is not an unconditional veto.
- Seats, lifecycle, readiness, blockers, findings, substitutions, telemetry,
  and voting roster come from the manifest. Report annotations cannot replace
  them. The full accepted plan's last fenced JSON object, when supplied, is
  used for `next_actions`, `conditions`, and `supporting_evidence` summaries.
  User choices come from the exact-version final verification document.

Lifecycle, election and readiness are displayed independently. A complete run
can be tied or blocked. Pending, incomplete and invalid elections never receive
an official Winner label. A unique preference winner can still have blocked
readiness. Missing final verification cannot make readiness ready. Historical
reports never establish readiness or an official live winner.
Unresolved `severity: "should"` findings alone do not block. Unresolved
`severity: "must"` (the legacy default) or `hard_constraint: true` findings do.
Resolved/rejected findings still require current-version confirmation, and a
disposition cannot downgrade the original finding's severity or constraint.

## Display annotations

Write prose in plain language. Annotations can explain the debate; acceptance
and readiness still come from the manifest. Every field is optional. A compact
historical example is [visual-report.json](../tests/fixtures/visual-report.json).

```json
{
  "title": "Council: recoverable importer",
  "stats": "Three remaining panelists",
  "proposals_title": "How the proposals changed",
  "proposals": [
    {"letter": "A", "headline": "A recovery journal", "r1": "Journal each batch", "final": "Journal bounded batches"}
  ],
  "consensus": [
    {"text": "Retain the API", "stage": "r2", "roster": ["A", "B", "C"], "agree": ["A", "B", "C"]}
  ],
  "disputes": [
    {"id": "D1", "question": "Rewrite the API?", "positions": {
      "B": {"label": "Retain API", "changed": true, "before": "Rewrite", "after": "Retain API", "reason": "The compatibility fixture fails under a rewrite."}
    }, "outcome": "Retain the API", "status": "settled"}
  ],
  "factcheck": [
    {"claim": "An interrupted write is recoverable", "by": "C", "result": "false", "evidence": "E1 reproduces a lost batch"}
  ],
  "score_chart": {
    "title": "Peer scores", "note": "Current proposal averages; self-scores excluded.",
    "dimensions": ["Correctness", "Simplicity"], "min": 1, "max": 10,
    "series": [{"label": "Plan A", "letter": "A", "values": [9, 8.5]}]
  }
}
```

Agreement intersects `agree` with the relevant roster, so a dropped seat cannot
increase the numerator. A supplied roster of A/B/C with historical seat D gives
“3/3 remaining; 1 dropped.” Use `stage_rosters` in the manifest, or an explicit
`consensus[].roster`, for earlier stages. The manifest's current R3 roster takes
precedence over annotations. If a named earlier stage has no recorded roster,
the count is unavailable rather than inferred from today's participants.
Substitutions retain their original `from`, `to`, reason and epoch metadata.

The report leads with the full plan, actions, conditions, separate readiness,
blockers, user choices, and evidence. Finding dispositions and changes of mind
precede proposal history and score/vote tables. Accepted transcripts and
provider/model/task diagnostics come later. Proposal badges are not used to
invent a winner; election status governs the official outcome.

## Scores, colors and accessibility

All supplied data must be valid JSON. Duplicate keys, nonfinite numbers, wrong
container/scalar types, boolean scores, invalid numeric ranges, and malformed
score arrays are rejected with an error. Scores and dimension averages are
finite numbers from 1 to 10; totals are bounded by the rubric dimension count.
A chart range must increase and stay within 0..10, with all non-null values
within both the range and 1..10. Each series needs exactly one value per
dimension. Null means unavailable. Peer ranges need two ordered finite bounds.
Borda and first-place counts are bounded integers for the relevant roster.

Historical seat colors, if supplied, are integer indices 1..6 (booleans are
invalid). Live seats receive bounded palette indices. The CSS uses T3
`--chart-1` through `--chart-6` with standalone defaults. Legacy series colors
are accepted only as six-digit hex strings, and are unnecessary for numeric
tables. Free-form CSS, URLs and custom property expressions are rejected.

Accepted tally dimensions always receive their own numeric table. An optional
`score_chart` is displayed separately as a supplied comparison; it cannot
replace the accepted scorecard. Scores are static numeric HTML tables, usable without JavaScript or hovering.
Table captions, row/column headers, focusable scroll regions, touch-size
buttons and native expandable changed-position reasons support keyboard and
mobile use. Optional tooltips assign **only `textContent`** from escaped data
attributes. Escape dismisses them. Every dynamic value is HTML-escaped; no
report data is inserted into executable JavaScript, CSS or inline JSON. There
is no tooltip `innerHTML` or dynamic JSON embedding to terminate with
`</script>`. The visible page title and viewport metadata are always present.

Private CSS variables reference T3 theme variables with light/dark defaults;
they do not redefine T3's variables. Tables can scroll horizontally on narrow
screens. The full plan and transcripts wrap instead of clipping.
Use `--inline` when publishing through T3: it removes outer `main` padding and
uses a transparent body to align with the reply text. Standalone output keeps
its default 20px padding (12px on mobile) and light/dark theme defaults.

## Telemetry

Wall-clock elapsed seconds are derived from timezone-aware ISO timestamps:
`created_at` to `completed_at` for complete runs, or current UTC for running
runs. Missing, invalid, timezone-free or reversed timestamps show unavailable.
This measurement does not substitute supplied provider timing or token data.

Telemetry is optional. A supplied telemetry object requires `coverage` equal to
`partial` or `complete`, plus a nonempty `source`. `elapsed_seconds` and `cost`
are finite nonnegative numbers. `calls`, `retries`, `replacements` and `tokens`
are nonnegative integers. A supplied cost also needs a three-letter uppercase
`currency`. Live telemetry comes from the manifest. Missing measurements read
“Unavailable; not zero.” Do not invent cost or tokens from task counts.
Visible measurements keep tokens and cost unavailable unless supplied. A
per-seat table counts stored task attempts, attempts with delegated task IDs,
and attempts numbered greater than one, including previous actors and epochs.
Diagnostics separately show task IDs, retry attempts, replacement records and
original task snapshots; these do not measure every underlying provider call.

## Historical compatibility

Existing `data.json` files remain readable through an explicit command:

```sh
python3 -I <actual-skill-directory>/scripts/build_visual.py --historical <old-data.json> <out.html>
```

Historical mode does not read a manifest, discover `r3/tally.json`, or infer
resumable state. It marks the report **Historical · unverified**, suppresses
live Winner labels, and cannot establish ready status. An explicitly embedded
`tally` can render diagnostic score tables. Old tallies without `status` are
shown as unverified/incomplete. To show a historical plan, supply `final_plan`
as its full plain-text string (or `{ "content": "full text" }`). It is labelled
by the historical notice and never treated as an accepted live artifact.
Historical `seats` retain `letter`, `name`, `short`, `color`, `active`, and
`dropped` fields; old rounds, proposals, consensus, disputes, fact-checks,
red-team notes and user choices remain displayable after validation.

Before publishing, preview a normal, tied, blocked and incomplete report at
mobile and desktop widths in both themes. Expand details and check overflow,
keyboard focus, touch behavior, and visible numeric tables. Mechanical tests:

```sh
python3 -I -m unittest discover -s tests -p test_visual.py -v
```
