# Phase 13.9: prepared single-case verification

The new manifest in `reports/phase139-single/approval-manifest.json` is scoped to
V2-02-01 only. It binds current evaluation/agent/model/graph/tool code, the frozen
V2 dataset and rubric, the reviewed labels and worksheet, the original source
plan, the latest failed-run ledger and its historical dispatch marker. The source
five-case plan remains unchanged. The subset adapter restricts inference, scoring
and coverage to one case; it does not silently execute other planned cases.

`evaluation.v2_single.prepare()` performs offline integrity and carry-forward
checks. `prepared_inputs()` returns sanitized incident input only. The catalog
snapshot is deterministic; classifier/investigator integration and strict
provenance/scalar grounding are tested through actual agents with explicit
offline test doubles. That is not real-model verification.

The cost policy is at most six NEW calls: one connectivity preflight plus up to
five agent calls, zero retries, stop on first failure, no external tools. At saved
$0.40/$1.60 per million input/output tokens and existing 32,768/1,536 token
reservation caps, maximum new reservations total $0.0933888. Prior estimated
spend is $0.0175852, yielding a conservative aggregate ceiling of $0.1109740
against the original $2 budget. These are operational estimates, not invoices.
Reverify published pricing and model/account access before/at future dispatch.

Node counts in the offline preflight are zero because no workflow was executed.
Future events from the existing runner count actual node starts/completions/
failures; skipped agents must not be reported as successful. The prepared human
review template carries the approved alternatives as human-only grading context,
with new semantic-review identity, timestamp, ratings and output hash unfilled.
No classification/causal accuracy is claimed from preparation.

`run_approved_single()` is a dormant integration entry point requiring a new
explicitly approved manifest digest. It rechecks bindings, acquires the existing
cooperative budget lock, checks intervening ledgers, and creates a NEW single-use
dispatch record linked to the old ledger. It never deletes/reuses the historical
dispatch marker. Interrupted or completed single-case dispatch blocks subsequent
dispatch until reconciliation and a new reviewed continuation implementation.
Do not run historical scripts concurrently. No retry/fallback model is available.

No new approval has been granted, no real preflight has run, and the entry point
has not been called outside isolated offline tests. Existing five-case approval
does not authorize this new single-case manifest. GO means ready to request
scoped approval; it does not authorize paid execution or establish live quality.
