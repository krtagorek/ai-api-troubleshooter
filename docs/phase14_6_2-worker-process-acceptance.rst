Phase 14.6.2: live worker process crash acceptance
================================================

Test-only architecture
----------------------

The manual executable tests/phase14_6_2_acceptance.py creates a unique
phase8_*_test PostgreSQL database using the existing disposable fixture.
Only that database is migrated through 0008. FastAPI, local mock API, Vite,
Chromium and each worker run in independent OS processes. Synthetic RSA
identities and Ed25519 signing keys are generated in memory. Child worker
configuration is supplied over stdin; private signing keys are never given
to workers, written to disk, or used in production.

The normal feature flag remains false. Worker composition explicitly enables
trusted semantics only for these synthetic test contracts. The normal worker,
application routes, prompts, evaluator, schema, locks, and timeout rules are
unchanged. Test-only queue/integration subclasses expose bounded barriers;
no application entrypoint imports or enables them. Configuration validation
rejects primary/remote databases, mismatched checkpoint databases, unknown
roles/crash points, and output paths outside the acceptance directory.

The five real agent implementations use injected deterministic FakeLLM
responses. No provider SDK is initialized. Child Python sockets are restricted
to the local database and test ports; browser requests are restricted to the
local application. This is application-level isolation, not an OS sandbox.

Crash boundaries and semantics
------------------------------

The controller waits for an atomically written barrier, verifies its PID,
and SIGKILLs only the worker it created. It then starts a fresh independent
worker with fresh pools, identity authentication, and semantic authorization.
The two-second lease is within existing supported configuration. No lease
rows or clocks are edited; PostgreSQL determines expiration and reassignment.

* Claimed: claim committed, before the worker starts its first graph node.
* Investigator checkpoint: synchronous checkpoint and progress event committed.
* Semantic committed: analyst-entry semantic authorization committed, before
  the analyst function runs. Recovery must reacquire a new single-use permit.
* Published: queue.finish has committed report, job terminal state, and event,
  before the worker returns. There is no separate broker acknowledgment.
  Consequently this case must remain terminal with one attempt and cannot
  legitimately be reassigned. The replacement process must claim nothing.

Three additional crashes revoke the contract after the investigator checkpoint,
the endpoint after the resolution checkpoint, or the producer grant after the
classifier checkpoint. Resumed protected work must fail closed and publish no
report. The producer-grant alternative satisfies the requested key-or-grant
revocation scenario; a live key-revocation crash is not claimed.

Attempts versus effects
-----------------------

Recovered nonterminal jobs have two attempts and two distinct lease IDs.
Previously committed graph checkpoints skip completed agents. An authorization
consumption before an uncommitted graph checkpoint may legitimately leave an
additional immutable consumption record; it does not authorize resumed work.
Every resumed protected node reacquires authorization. Old lease heartbeat
attempts are rejected. Each job must have exactly one terminal event and at
most one report. Ordered SSE is replayed over real HTTP using Last-Event-ID.

Browser fault isolation
-----------------------

The first worker-only run preserves the uninterrupted API/SSE connection.
An additional run restarts the disposable frontend proxy while the job is
paused and Chromium temporarily offline. Active streams must reconnect with
a nonempty cursor and render authoritative completion or a safe hold.
Explicit refresh reauthenticates, as this frontend deliberately stores bearer
credentials only in memory.

Early proxy runs exposed a test confound: Vite HMR reloaded the page and erased
the memory-only identity. These failed assertions and screenshots are retained.
Disabling HMR alone still left the development client able to reload. The final
test server serves the compiled production React build through a real preview
proxy, without a development reload client. It preserves the application API
and streaming code. This separates socket recovery from
a page reload; no application authentication persistence is weakened.

Evidence and limits
-------------------

Sanitized run directories contain actual process IDs, SIGKILL return codes,
lease IDs/expiry and attempt events, checkpoint digests, permit references,
authorization audits, real HTTP codes, browser replay cursors and screenshots.
Each run removes its disposable database and terminates only its own children.
Private .env content is never loaded by test children or changed. Frozen
benchmark, human-review and historical audit hashes are rechecked.

No report claims PASSED validation, no proposed actions execute, and every
published diagnostic plan requires human approval. NOT_TESTED remains explicit.
Production contract/ingestion administration interfaces and real identity,
ownership, security-review, key-management and revocation configuration remain
separate work. No production activation, paid dispatch, migration or deployment
is authorized by this acceptance executable.

Observed browser acceptance failure
-----------------------------------

The final run passes worker recovery, authenticated cursor replay, report/hold
rendering and explicit refresh. Visual review nevertheless fails complete
agent-timeline recovery. frontend/src/components/timeline.tsx filters events
to the maximum attempt before calculating completed nodes. Nodes restored from
an earlier committed checkpoint therefore appear pending, even though Run
activity shows their completion and the report is persisted. The investigator
and semantic crash screenshots reproduce this; refresh does not correct it.

A denied semantic wrapper can also emit node_completed without invoking its
LLM-backed agent. Agent-call artifacts distinguish that wrapper completion
from actual model execution; timeline labels do not make this distinction.
No protected diagnosis or report is published in the revocation cases.

No timeline, authorization, routing or other application correction was made
in this milestone. Overall browser acceptance remains FAIL/NO-GO pending a
separately authorized observability correction and reacceptance. The existing
regression suites pass but do not cover this cross-attempt display defect.
