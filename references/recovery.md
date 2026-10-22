# V2 delegation, limits and recovery

## Fallback receipt format

`panel.py candidates CATALOG --active SEATS` lists available distinct candidates
without making model calls. `panel.py fallback CATALOG --active SEATS --checks
CHECKS` selects only candidates with matching receipts checked within 24 hours:

```json
{
  "opencode/glm-5.3-flash-free": {
    "free": true,
    "free_source": "https://opencode.ai/v2/docs/console/models/#free-models",
    "smoke_passed": true,
    "data_policy_compatible": true,
    "model_identity": "glm-5.3-flash",
    "options": {"variant": "high", "agent": "build"},
    "checked_at": "2026-10-22T00:00:00+00:00"
  }
}
```

This is a schema example, not an actual successful smoke receipt. Record the
observed source, target/options and current timestamp after real checks. The
helper validates the receipt's fields/freshness, not the truth of external
observations. Pass reserved or failed model IDs as repeatable `--used` arguments;
only the moderator may reserve/update the shared fallback selection.

## Ownership and request identity

Use T3 Code Orchestrator V2 `orchestrator_capabilities`, `delegate_task`,
`task_status` and `task_cancel`, with the live catalog's exact provider/model
IDs and supported options. Native provider CLIs and ordinary top-level threads
are not substitutes. A documented ACP MCP bridge transports these same calls.

The moderator records launch intent before delegation and the returned task
ID afterwards. Each request identifies run, stage, epoch, seat and actual
attempt. Transport uncertainty reuses the same request ID; failed work gets
a new attempt. Every new round is a new `delegate_task`, carrying its original
brief plus prior findings, responses and unresolved objections. Do not send
new review rounds to `childThreadId`; that ID is backing storage.

Delegate with `mode: "async"` and `runtimeMode: "inherit"`. Finish the parent
turn while waiting. Automatic completion delivery resumes the parent. Read
status when needed to reconcile a completion or resume, never as a polling
loop. A terminal successful task with `workState: "result_available"` and no
relevant pending child runs is eligible for acceptance; merely existing output
or a failed/cancelled terminal task is not.

## Limits and free-model replacement

Preferred seats and a versioned evidence-qualified fallback pool live in
`config/panel.json`. Run `panel.py --help`. The registry currently includes
GLM-5.3-Flash, MiniMax-M3.1-Flash and DeepSeek V4.1 Flash free endpoints, with
benchmark sources, model identities and checked dates. Pool membership is
evidence for suitability, not a guarantee of live endpoint behavior.

1. Normalize provider errors using structured metadata when available. Retain
   the raw error. Exhausted account/usage quota switches immediately.
2. An explicit temporary Retry-After of at most 60 seconds may be honored once.
   Wait once, then retry; a second limit switches. If absent or longer, switch
   without retrying early. Neither a generic exception nor authentication
   failure proves a rate limit. Generic failed/truncated output gets one repair
   attempt; after another failure drop that seat if a valid panel remains.
3. Refresh the catalog, verify a candidate is still free from its official
   endpoint pricing, and check source/data restrictions. Smoke-check exact
   model/options for required context, read-only tools and valid structured
   output. Record these receipts with dates, actual identity and errors.
   Availability, model mappings and pricing may change after installation.
4. Choose the first compatible qualified candidate in the versioned per-mode
   preference order, excluding active underlying model identities and already
   failed candidates. Benchmark scores from a different checkpoint, reasoning
   setting or harness are not endpoint-equivalent. Anonymous/unknown models
   cannot satisfy a distinct-model requirement.
5. Reserve replacements centrally before launching to avoid two concurrent
   failures selecting the same model. Exhaust a finite pool once; do not cycle
   indefinitely. Record preferred and actual model, why it changed, affected
   stages, provider diversity and the source of qualification.

At least three distinct known usable models must remain. Do not duplicate a
model under another alias or reasoning level to fill seats. Generic provider
defaults can be displayed honestly as unspecified; they cannot count as a
known distinct model until the provider reports their underlying identity.

### Which stages restart?

For post-vote selection, build the active-model list from effective
`stage_actors` overrides and the actual synthesis/repair author, not only the
historical election seats. Keep historical voter identities unchanged.

- A new proposal-author or voter model first completes a blind R1 against the
  original frozen brief. Preserve unaffected valid R1 work, then repeat affected
  debate and voting under a new epoch. The replacement cannot inherit a ballot.
- If a voter drops without replacement, candidates and voters both shrink to
  the same surviving roster. Retain its historical ideas, invalidate the old
  election and collect every surviving ballot again. Stop if fewer than three
  distinct known models remain.
- After voting, a new critic/editor may assess the existing accepted direction
  as an explicitly recorded actor; it cannot impersonate a historical voter.
  If the winner's author is replaced, record the acting editor and preserve
  proposal provenance. A material change to direction reopens debate/voting.
- Invalidate dependent synthesis, critiques, fixes or verification when their
  input plan/source hashes change. Historical artifacts remain immutable and
  distinguishable from current accepted work.

## Resume

`/council resume <run-id-or-path>` must run in the owning T3 thread. Read the
manifest and inspect the helper's status/reconciliation output. Verify source
hashes and accepted artifact dependencies, then reconcile existing task IDs
before launching new attempts. A running child stays running; a terminal
successful child can be validated and accepted. Superseded or cancelled
attempts never become current merely because a late callback arrives.

Repeat delivery is idempotent. Reuse launch request IDs after an uncertain
transport response, not after a known work failure. Do not infer live state
from old folders. Legacy reports can render using explicit historical mode,
but do not have enough provenance for automatic resume.

If task ownership differs, explain which original thread to resume. Saved
plans and reports remain readable anywhere. A schema/source/config change
does not silently migrate an in-flight run; preserve it or explicitly start
a new run with the changed inputs.

## Durable deadlines: platform prerequisite

The currently exposed V2 `delegate_task.timeoutMs` limits a **parent wait**;
it does not cancel the child. An async completion wakes the moderator only
when an event actually occurs. This skill cannot promise unattended 30-minute
stall cancellation using that field or a prose instruction.

Read [v2-deadlines.md](v2-deadlines.md) for the proposed T3 platform contract.
Use automatic execution deadlines only when the live catalog explicitly
advertises and documents the implemented capability. Record whether deadlines
are available and the actual configuration. Until then, normal completion and
same-thread resume work; overdue tasks can be checked/cancelled on the next
genuine wakeup or manual resume. Report this limitation in run diagnostics.
Do not invent request fields, start polling watchers, or create recurring
schedules to pretend the missing capability exists.

## Completion and bounded failure

Record every must-fix disposition and verification against the exact final
artifact. Lifecycle completeness, election result and readiness are separate.
A complete blocked plan is useful; an incomplete election has no ordinary
winner. Preserve a checkpoint report with errors, accepted work, remaining
tasks and resume instructions when bounded retries cannot finish the run.
