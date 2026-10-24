Phase 14.6.2a: cross-attempt timeline correction
===============================================

Reproduction
------------

The preserved Phase 14.6.2 investigator events reproduce two incorrectly pending
nodes. The old component computed completion only from events in the maximum
attempt. Checkpoint recovery correctly skipped earlier agents, so they never
received a second completion receipt. The original recovered/refresh screenshots
and historical event/audit artifacts remain unchanged.

Correction and observability contract
-------------------------------------

frontend/src/lib/timeline.ts reconstructs wrapper completion from persisted
receipts across all attempts. It sorts by global job sequence, deduplicates
receipts, retains each node's attempt numbers and warns on gaps/conflicts.
Conflicting receipts do not authorize a completion display; mixed-job input is
not combined. Missing records remain unknown. Neither a final report nor
terminal job status fills in missing node history. Later attempts cannot erase
an earlier recorded completion.

Timeline labels now explicitly say Workflow wrapper completed, followed by the
attempt numbers. Every node states Model invocation: not recorded. Existing
node_completed events describe wrapper completion, including an authorization
wrapper that denied entry. They do not prove model invocation, authorization
denial, skip, interruption, validation success or diagnostic truth. Unsupported
states are explicitly indistinguishable rather than invented. The current event
contract cannot provide exact agent invocation or skip reasons; that would
require a separately designed additive receipt contract.

No new SSE type or field, schema migration, worker behavior, agent prompt,
authorization path or diagnostic routing is introduced. Existing global replay,
strict cursor ordering, deduplication, cancellation and terminal-state
reconciliation remain unchanged. Run activity retains persisted attempt and
timestamp history. Lease identifiers remain backend evidence, not authority
for the browser.

Verification
------------

Thirteen Vitest tests use a copied projection of preserved synthetic live events,
including all seven crash/revocation cases. They cover overlapping attempts,
duplicate/out-of-order input, missing events, conflicting receipts, mixed jobs,
invalid identifiers and terminal success with a partial timeline. The older UI
regression now uses a valid UUID job cursor and the more precise wrapper label;
its assertion that completion is not validation success remains intact.

Two Chromium fixture regressions cover cross-attempt replay and refresh with
successful and revoked investigations. The isolated live executable is::

    .venv-py311/bin/python -m tests.phase14_6_2a_acceptance

It repeats all seven real OS-process crash/revocation scenarios in a disposable
PostgreSQL database, using the compiled React build, real HTTP proxy, synthetic
identities/keys and injected deterministic responses through actual agents.
Expected wrapper receipts come from the persisted event rows, not a final report
or test assumption. Chromium checks every node and unknown model-invocation
label both after automatic SSE reconnect and after explicit refresh. The test
captures before/after screenshots, attempts, leases, permits and protected-output
holds. No OpenAI provider call occurs.

Boundaries
----------

This corrects the timeline acceptance blocker. Public diagnostic-contract and
signed-ingestion administration, production identity/ownership/review/key
providers, and OS-level egress isolation remain separate work. No production
activation or paid evaluation is implied. Normal feature flag remains off.
The unchanged primary database is checked with read-only schema/count
fingerprints; this is not a byte-for-byte backup comparison. History is not
rewritten. Budget remains 0.0550552 USD against the original 2 USD.
