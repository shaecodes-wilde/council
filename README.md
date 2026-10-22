# Council

A Markdown skill for Claude Code and Codex that runs an anonymous multi-model
debate through **T3 Code Orchestrator V2** and produces a complete decision
document with an implementation handoff. Python helpers manage local state,
validation and reports; all model execution belongs to T3.

## Default workflow

1. Clarify consequential unknowns; freeze sources, requirements and an anchored rubric.
2. Independent proposals, privately staged until the round completes.
3. Fact-check, debate material disagreements, and write complete revised proposals.
4. Validate every score and ranked ballot; tally Borda **panel preference**.
5. The winning panelist synthesizes a standalone plan with useful peer ideas.
6. Other panelists red-team that plan; the author makes one repair pass.
7. Verify the final version, every finding's disposition and required handoff sections.

The moderator stays neutral. A tie produces shared actions and explicit
alternatives for the user; a seeded draw chooses an author without breaking
the election tie. A proven unresolved requirement violation keeps the plan
blocked even when most panelists prefer it. Lifecycle completeness, election
outcome and implementation readiness remain separate.

The report puts the actual full plan and next actions first, followed by
readiness, evidence, objections, user choices, debate history, accessible score
tables and diagnostics. Predicted quality is distinct from measured results.

## Panel and fallback

Preferred seats remain **Opus 5.5 High, GPT-6.1 Sol High, DeepSeek V4.1 Flash
High and Devin SWE-2 Max**. Exact provider/model/options must exist in T3's live
catalog. Configuration is versioned in [config/panel.json](config/panel.json).
A generic provider `default` is never silently identified as SWE-2 or counted
as a known distinct model. Missing preferred models are reported; a usable
panel requires at least three distinct known underlying models.

Exhausted usage switches immediately to a qualified **free OpenCode model**.
A temporary rate limit gets at most one explicit Retry-After of 60 seconds or
less before switching. Different replacement seats use different underlying
models; reasoning options and aliases do not establish diversity. A finite
pool and a three-model minimum prevent endless retries or a disguised
single-model council.

The initial pool contains GLM-5.3-Flash, MiniMax-M3.1-Flash and DeepSeek V4.1
Flash. Each entry cites exact-version model-level benchmarks and official
free availability. Their order is a maintained per-mode preference, not a
claim that unrelated benchmark scores are interchangeable. Before use,
confirm current free pricing, data-policy compatibility and an exact-target
smoke test. Benchmark evidence alone does not prove endpoint behavior.
See [recovery policy](references/recovery.md).

## Requirements

- Claude Code or Codex in an owning T3 thread exposing Orchestrator V2:
  `orchestrator_capabilities`, `delegate_task`, `task_status`, `task_cancel`;
  plus `html_preview` and `html_render` for presentation.
- At least three distinct configured, usable underlying models.
- Python 3.12+ on Linux/macOS; helpers use only the standard library.

**Deadline limitation:** the exposed V2 `timeoutMs` is a parent wait budget,
not a child execution timeout. Async completion and same-thread resume are
supported; guaranteed unattended stalled-child cancellation requires the
separate [V2 durable-deadline prerequisite](references/v2-deadlines.md). The
skill reports unavailable deadline support and does not invent API fields or
polling watchers. Shared-workspace staging is procedural privacy, not enforced
filesystem isolation.

## Install globally

One shared checkout serves both hosts. Resolve its actual location instead of
assuming a `/portable` directory. The commands below create new links; if a
destination already exists, inspect and preserve it rather than overwriting it.

```sh
git clone https://github.com/shaecodes-wilde/council "$HOME/.local/share/agent-skills/council"
mkdir -p "$HOME/.claude/skills" "$HOME/.agents/skills"
ln -s "$HOME/.local/share/agent-skills/council" "$HOME/.claude/skills/council"
ln -s "$HOME/.local/share/agent-skills/council" "$HOME/.agents/skills/council"
```

Existing installations can update their clean shared checkout with an
ordinary fast-forward pull. For a PR installation, explicitly check out that
review branch instead. Both symlinks then expose the same version to new
skill invocations; in-flight tasks retain their existing briefs.

## Usage

```text
/council Design restart-safe background jobs --mode code --context src/ tests/
/council Review this product strategy --without devin --add fable
/council Compare these drafts --mode artifact --score "Clarity, Accuracy, Usefulness"
/council resume 2026-10-22-background-jobs-<unique-id>
```

The old `--redteam` option is accepted and redundant: synthesis, red-team,
bounded repair and verification are now the default. Existing `--mode`,
`--score`, `--context`, `--add`, `--without` and plain-language variants remain.
No quick/deep profile split is needed to obtain the thorough workflow.

## Artifacts and helpers

Each run gets `debates/<date>-<slug>-<unique-id>/`. The private
`_moderator/run.json` records source/rubric hashes, actual targets, tasks,
attempts, epochs and accepted artifacts. Raw output is immutable; published
anonymized copies are separate. Consumers verify accepted paths/hashes and
never discover voters or final plans by scanning filenames.

```sh
python3 -I scripts/run.py --help
python3 -I scripts/panel.py --help
python3 -I scripts/tally.py --help
python3 -I scripts/scrub.py --help
python3 -I scripts/build_visual.py --help
```

The moderator uses `run.py` to initialize/freeze, record launch intent and
observed task results, accept published output, advance barriers, register the
tally/findings/final verification, and finalize. All mutations and resume
require the owning thread ID. Read-only status and saved reports work anywhere.
[The contract](references/contracts.md) documents actual CLI examples and
schemas; [briefs](references/briefs.md) contain stage templates.

`panel.py resolve catalog.json` prints shuffled, catalog-validated seats JSON
and reports unavailable preferred seats on stderr. `panel.py candidates
catalog.json --active seats.json` lists evidence-qualified distinct candidates.
`fallback` additionally requires fresh moderator receipts via `--checks`.
This helper never contacts a provider or proves a receipt itself.

Live reports resolve the registered tally and accepted final-plan pointer
from the manifest. Old saved reports remain readable using explicit
`build_visual.py --historical`; old folders without provenance are not
automatically resumable. See [report schema](references/visual.md) for arguments.

## Validation

```sh
python3 -I -m unittest discover -s tests -v
git diff --check
```

Tests cover ballot/roster failures, valid ties and Borda arithmetic, immutable
scrubbing and source preservation, request/attempt reconciliation, stale
output/dependencies, path/hash checks, blocked/tied finalization, fallback
policy, and inert report rendering. Helpers do not need model access for tests.
Any concurrent tests/builds should use at most two workers.

[Evaluation cases](evaluations/cases.json) and [evaluation guidance](evaluations/README.md)
support a separate blind comparison of one strong model, independent proposals
plus synthesis, and full Council. Unit tests do not establish decision quality
or demonstrate a live four-provider run. Report elapsed time and usage only
when actually observed; unavailable telemetry is not zero cost.
