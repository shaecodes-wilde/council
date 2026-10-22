# Council run contract (schema 1)

All helpers use standard-library Python and run with `python3 -I scripts/NAME.py`.
`run.py` is the sole manifest writer. It never invokes models, T3, timers, or
polling loops. The moderator records observed T3 results; Python cannot attest
that an external result is truthful. Shared workspace staging is procedural
privacy, not enforced filesystem isolation.

## Files and consumer API

A run is a uniquely created `<date>-<topic>-<random-id>/` directory. Its manifest
is `_moderator/run.json`; raw attempts belong in `_moderator/raw/`. Published
scrubbed copies live outside `_moderator/`. All recorded paths are run-relative,
regular files without symlink components. Hashes are SHA-256 of exact bytes.
Atomic replacement and a Linux `flock` serialize CLI manifest writes. A failed
write retains the prior manifest; callers must never modify `run.json` directly.
Raw bytes are hashed when successful completion is recorded and checked on every
consumption. Raw and accepted copies must remain immutable; hashes detect later
changes rather than claiming filesystem access control.

`contracts.py` exports these stable functions:

- `read_json(path)`: reject duplicate keys and nonfinite JSON constants.
- `last_json(path)`: parse the last fenced JSON object, with a useful ValueError.
- `atomic_json(path, value)`: encode strictly, fsync and replace atomically.
- `load_manifest(root)`: validate schema 1 and manifest structure.
- `accepted_paths(root, manifest, stage)`: map seat letters to checked Paths;
  validate successful task, raw/published hashes, target, dependencies and epoch.
- `validate_ballot(ballot, voter, roster, dimensions)`: exact ranking except self;
  exact scores for all candidates including self and all dimensions; finite
  numbers excluding booleans, 1–10 in half-point increments.
- `safe_path(root, relative, *, exists=True)`, `sha256(path)`,
  `successful(task)`, `settled(task)`, `eligible_task(root, manifest, request_id)`,
  `validate_roster(manifest, roster=None)`, `validate_actors(manifest)` support helper consumers.
- `finding_blocks(finding)`: true only for an unresolved finding whose severity
  is `must` (the legacy default) or whose `hard_constraint` is true.
- `evaluate_final_objections(verifications, assessments=None)`: pure shared
  normalization and blocking policy; detailed schema below. Callers verify
  exact final hashes, accepted provenance and evidence references first.

`load_manifest` permits an incomplete panel for reporting. `validate_roster`
requires at least three active seats with distinct explicit `model_identity`
strings. Aliases, providers and reasoning options do not establish diversity;
the moderator must verify identities against the live catalog.

Manifest required fields are `schema_version:1`, `run_id`, `owner_thread_id`,
`created_at`, `topic`, `stage`, `status`, `rubric`, `seats`, `sources`, `vote`,
`tasks`, `accepted`, and `blockers`. `readiness` is independent of lifecycle.
Stages: prepare, r1, r2, r3, synthesis, redteam, repair, verify, complete.
Lifecycle: running, incomplete, complete. Readiness: pending, ready, blocked,
needs-user-decision, incomplete. A complete report may be blocked or tied.
`completed_at` records the UTC ISO timestamp of successful finalization, even
when readiness is blocked. Reopening removes it; historical schema 1 manifests
without it remain valid. Reports can measure elapsed time using it instead of
the current clock.
Rubric entries require name, definition, low/mid/high anchors, direction higher,
and integer weight 1. Seat entries require letter A..Z, providerInstanceId,
model, options object, active boolean and a verified model_identity for voting.

`vote` holds positive `epoch`, symmetric candidate/voter `roster`, and status
pending/complete/tied/incomplete/invalid. Task entries are keyed by stable
clientRequestId and hold nullable task_id, stage, letter, positive attempt,
epoch, status, output_path, target, hasPendingChildRuns, workState. The CLI also
records model_identity, frozen dependencies, history, and raw_sha256.
Successful means status completed, no pending children, workState
result_available. Failed, cancelled, timed_out, superseded and nonterminal
attempts cannot be accepted. Transport retries reuse the stored intent key;
new actual attempts increment attempt. A task ID belongs to exactly one intent.
`interrupted` is also a supported terminal catalog status. `settled(task)`
requires no pending children and either successful completion or a non-completed
terminal status. Completed tasks without available results remain unsettled.
All unsettled tasks remain reconcilable, including historical superseded work;
replacement, invalidation and a new attempt require reconciliation/cancellation
first. A pending-child terminal observation can be updated when children finish
or cancellation is confirmed; a settled terminal observation is immutable.

`accepted[stage][letter] = {path, sha256, request_id, epoch}`. Consumers never
glob files to infer accepted answers. R2 and later must match the current epoch;
R1 can survive roster changes when target and dependencies still match.
Source records are frozen `{path, sha256}` objects prepared explicitly by the
moderator; the helper does not scan or snapshot original source trees.

Authoritative report pointers:

- `vote.tally = {path, sha256, epoch}` is the registered tally. Tally JSON must
  carry `epoch`, `roster`, `provenance` equal to the accepted R3 map, and
  `proposal_provenance` equal to accepted R2. `record-tally` recomputes it using
  `tally.tally(root)` and requires complete equality before recording status.
- `final_plan = {stage: "repair" | "synthesis", letter: "A"}` resolves only
  through `accepted_paths`. `final_verification = {path, sha256}` points to
  moderator section checks and finding dispositions for the exact final hash.

## CLI workflow

Every mutation and resume requires `--owner-thread THREAD`; status is read-only
and readable from any thread. Examples omit the common flag for brevity after
initialization; it is still required.

1. `run.py init PARENT --owner-thread THREAD --topic TOPIC --seats seats.json
   --rubric rubric.json` returns the new run path. Prepare source snapshots
   inside it, then `freeze-sources RUN --artifact sources.json` records a list
   of path/hash records. Sources can be empty for a question without files.
2. `advance RUN` begins R1. `intent RUN --stage r1 --letter A [--attempt 1]
   [--output _moderator/raw/NAME.md]` persists a request ID before delegation.
   Invoke T3 outside Python with that key and returned target/output path.
3. `record-task RUN REQUEST_ID --task-id TASK --status running --work-state
   pending [--snapshot event.json]` retains observed status/errors. Completion:
   `--status completed --work-state result_available` after the raw file exists.
   Use `--pending-children` whenever T3 reports outstanding child work; it
   prevents acceptance. Record failures with their exact original snapshot.
4. Scrub selected successful raw tasks using scrub.py; review flags. Then
   `accept RUN REQUEST_ID --published r1/A-HASH.md` records the separate copy.
   Identical repeated delivery is idempotent; changed raw or published bytes,
   mismatched tasks and superseded outputs are rejected. R1 publication should
   be withheld from panelists until the all-seat barrier.
5. `advance RUN` checks each stage barrier. Repeat intents/results/acceptance
   for R2 and R3. Generate tally with tally.py, then `record-tally RUN --artifact
   r3/tally.json`. Incomplete/invalid elections cannot advance. A tie requires
   `draw-author RUN --seed RECORDED_SEED`; the draw selects a tied author only.
   Winner, tied alternatives and readiness remain separate.
6. After synthesis, accept redteam artifacts from every other seat.
   `record-findings RUN --artifact findings.json` registers the evidence ledger
   (including `[]` if empty), then advance to repair. Accept the author's one
   repair stage output, or `no-repair RUN` when there are no findings. Advance
   sets final_plan and enters verify.
7. Accept every other seat's verification artifact for the exact final hash.
   `record-final RUN --artifact final-check.json` registers moderator section
   checks and dispositions. `finalize RUN` reruns checks and sets lifecycle
   complete with readiness ready, blocked, or needs-user-decision.

Finding ledger array entries require unique `id`, nonempty `description`,
`hard_constraint` boolean and nonempty `evidence` array of `{path, sha256}`.
Optional `severity` is exactly `must` or `should`, matching the model briefs;
absence means `must` for existing artifacts. Final dispositions cannot change
the ledger's severity or hard-constraint status. Unresolved `should` findings
remain in the ledger and final dispositions but do not alone block readiness.
An unresolved demonstrated hard constraint blocks even with severity `should`.
Evidence must identify frozen source or accepted output bytes. Assertions
without evidence remain investigation material rather than proven vetoes.
`findings_provenance` freezes the ledger artifact and accepted redteam map.

Each accepted verify artifact's last fenced JSON object has:

```json
{"final_sha256":"HEX", "findings":{"F1":{"disposition":"resolved", "verified":true}}, "verdict":"ship", "remaining_blockers":[]}
```

Final moderator check JSON requires `final_sha256`, `sections` object with
nonempty location/evidence strings for direction, evidence, contributions,
strongest_objection, conditions, actions, acceptance_checks, user_decisions;
`user_decisions` list; and `findings` array with one entry per finding:
`{id, disposition: resolved|rejected|unresolved, evidence:[{path,sha256}]}`.
For resolved/rejected dispositions every critic must confirm that disposition
and verified true against the exact final hash; otherwise record unresolved.
Unresolved blocking findings produce blocked readiness and remain visible in blockers.
A tie additionally requires `tied_alternatives` equal to vote.tied and a
nonempty user_decisions list. Section locations are moderator attestations,
not a machine assessment of prose quality; code-specific handoff completeness
is checked by the moderator against the synthesis brief.

### New final-verifier objections (compatible schema 1 addition)

Verifier `verdict` accepts `ship`, `ship-after-fixes`, or `block`.
`accept` validates verification metadata before making the artifact immutable;
a malformed verdict, blocker entry or findings map gets a precise repair error.
Retry that unaccepted task with a new attempt key under the same voting epoch.
`remaining_blockers` is an array of `{id, issue, severity?, hard_constraint?, ...}`;
`description` is accepted as an alias for `issue`. IDs must be unique per critic;
severity defaults to `must`, hard_constraint to false. Other model metadata such
as textual evidence, fix and requirements stays in the immutable verifier output.
Legacy artifacts omitting both fields remain valid. A non-ship verdict with no
listed objection creates a synthetic must-fix objection with ID `verdict`.
Listed objections are evaluated even if verdict is `ship`.

The moderator's final-check JSON may add `verifier_objections`, an array of
`{critic, id, disposition, evidence, rationale}`. These assess new objections,
not the original ledger. Disposition uses exactly `resolved`, `rejected`, or
`unresolved`. No duplicate or unknown `(critic,id)` assessments are allowed.
Unassessed objections default to unresolved. A resolved/rejected objection needs
a nonempty rationale and nonempty evidence references to frozen/accepted bytes;
the runner checks registration and hashes. This permits evidence-based rejection
of unsupported objections without making a model verdict an automatic veto.
No additional repair pass is created: a complete decision document can finish
blocked with unresolved objections attributed to the critic.

`evaluate_final_objections(verifications, assessments=None)` takes a dict from
critic letter to full parsed verification JSON, and the optional moderator array.
It returns a list of objects with exactly these keys:
`critic`, `id`, `description`, `severity`, `hard_constraint`, `disposition`,
`evidence` (moderator references, default `[]`), `rationale` (default `""`),
`blocking` (boolean). It does not mutate inputs or read files. Callers must check
accepted verification hashes/final_sha256 and validate evidence registration
and bytes. Blocking means unresolved and (`must` or hard_constraint). Assessments
cannot override critic severity/hard_constraint via extra metadata. The original
ledger continues to use `finding_blocks` with ledger metadata preserved.

## Recovery and invalidation

`status RUN` exports stage, election, accepted artifacts, integrity errors and
pending reconciliation actions without writing. `resume RUN --owner-thread
THREAD` exports the same only for the owner. Reconcile task IDs through T3
before any new launch. A missing launch response retains task_id null and the
same clientRequestId; it is never permission to create a new request key.
Exports identify completed output still awaiting acceptance too.

`mark-incomplete RUN --reason TEXT` preserves resumable state. `set-roster RUN
--roster A B C --reason TEXT` changes the symmetric roster and starts a new
epoch; affected debate, ballots and later stages are invalidated. A panel below
three distinct models remains incomplete and cannot advance. `replace-seat RUN
--letter A --seat replacement.json --reason TEXT` records both identities,
starts the replacement at blind R1, preserves unaffected R1, and invalidates
all later work. Qualification against live T3 and the free-model registry is
the moderator's prerequisite; Python cannot verify live endpoint capabilities.
For a replacement used only as critic/editor after the election, use
`replace-actor RUN --stage redteam --letter B --seat actor.json --reason TEXT`.
It preserves the election and original voter identity, records a new actor in
`stage_actors[stage][letter]`, and supersedes that actor's affected attempts.
Redteam replacements also own their later verify stage; synthesis replacements
also own repair. It is only allowed for the current post-vote stage. Material
direction changes require `reopen`, not an actor substitution. Qualification
remains a moderator prerequisite for both types of substitution.
Identity checks compare synthesis and repair authors plus all effective redteam
and verify peer actors, including accepted task identities. An actual author
cannot review its plan under another letter; a peer's earlier role identity
cannot be reused by a different peer. Election seats and ballots stay unchanged.

`reopen RUN --stage STAGE --reason TEXT` explicitly archives invalidated accepted
pointers, marks old tasks superseded, and reopens affected dependencies. Raw
files and old histories remain. Changed sources require a new run. Late
callbacks for settled superseded tasks cannot mutate authoritative state.
Legacy superseded tasks still awaiting child work can record reconciliation
snapshots until settled, but can never be accepted. A frozen
run is never silently migrated or reassigned to another owning thread.

Durable execution deadlines are an unmet T3 platform prerequisite; timeoutMs
is a parent wait budget. Neither helper exports nor resume implement a polling
watcher or claim automatic stalled-child cancellation.
