# Proposed T3 V2 deadline contract (not implemented by this skill)

Council uses Orchestrator V2's existing delegation APIs. Durable execution
deadlines require a separate T3 platform change; this repository has no T3
server implementation. Do not pass the proposed fields to current tools.

## Required behavior

- Advertise durable execution deadlines in live capabilities, including the
  supported request parameter and terminal timeout/error representation.
- Attach an absolute execution deadline (or documented runtime budget) to a
  delegated task, distinct from the parent's `timeoutMs` wait budget. A
  configurable 30-minute task budget matches Council's original default.
- Persist the deadline with task ownership and restore it after a server
  restart. On expiration cancel the task, active descendants and queued later
  child work through existing cancellation semantics.
- Persist a timeout terminal result and parent notification. Notification
  delivery can repeat; task/event identity lets the parent deduplicate.
  Parent disconnection must not disable server-side cancellation. Delivery
  remains pending until the owning parent can receive/resume.
- Resolve completion/deadline races atomically: an already accepted completion
  cannot later become timed out, and late output from a cancelled task cannot
  become accepted current work.
- Where adapters support it, expose typed `rate_limit` versus `usage_exhausted`
  metadata and Retry-After while retaining the original provider error.

## Platform contract tests

1. Task completes before deadline: one successful terminal result; no later
   timeout conversion or spurious cancellation.
2. Task stalls: deadline cancels parent-owned child/descendants and produces a
   durable timeout notification without a polling moderator.
3. Restart T3 before expiration: the deadline still fires correctly.
4. Disconnect/reconnect the parent: cancellation is timely server-side;
   delivery survives disconnection and duplicate delivery is safe.
5. Race completion with deadline and late writes: exactly one authoritative
   terminal outcome; cancelled artifacts never become accepted.
6. Distinguish provider rate limits and exhausted usage with a retained raw
   error; absent metadata remains unknown rather than guessed.

The skill's regression tests cover local task/epoch/artifact acceptance and
reconciliation. They do not demonstrate this absent platform capability.
