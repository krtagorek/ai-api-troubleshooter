# Phase 13.7: prepared real-model runner, awaiting approval

The original five-case plan and completed human label-review worksheet remain
unchanged. `evaluation.v2_execution.prepare()` verifies all five decisions,
identities, rationales, timestamps and alternatives, freezes their content/file
hashes, and binds dataset, rubric, plan and relevant source-code hashes into a
new approval manifest. Preparing this manifest makes no provider calls.

The shared `evaluation.runner.run_cases` now accepts an optional scoring adapter.
V1 retains its default scoring path. V2 uses `ReviewedAdapter(V2Adapter(...))`,
which joins labels and approved alternatives only AFTER inference. Graph inputs
are sanitized incident dictionaries only. Canonical scalar/provenance validation
is unchanged. No external diagnostic tools or automatic fixes are configured.

Node wrappers journal actual starts, completions and failures. Coverage counts
come from those events, not inferred state fields. Each case records its sanitized
returned state and a versioned output hash. V2 classification, abstention,
workflow failure, coverage and validation applicability are separate; causal and
resolution semantic accuracy remain null until independent human review.
Approved alternatives are shown to post-execution graders, never automatically
rejected because they differ from the injected reference cause. They are not
silently treated as causally proven either.

`reports/phase137-v2/post-execution-review-template.json` prepares blank review
entries for the selected cases. Actual output hashes and sanitized outputs are
filled from real returned states only after a future run. Post-output reviewer
identity, ratings and rationale remain blank even though label review is done.
Export only `HumanReview` fields for `bind_review`, setting status to `COMPLETED`
after the human finishes; keep the associated reference/output context separate.
Skipped or failed workflows are not successful validations or wrong diagnoses.

## Future execution gate

`run_approved(manifest, explicitly_approved_digest, new_output_directory)` is the
integration entry point; **it has not been invoked for this plan**. An approval
digest is a technical binding, not consent: automation must wait for explicit
human paid authorization. The function rechecks the complete manifest, private
configuration, single-use global V2 dispatch marker, cooperative process lock,
and intervening evaluation reports before dispatch. An interrupted run leaves a
marker; never delete it to retry or reset spend. Reconcile all reservations and
obtain a new reviewed plan/approval before continuing. Existing historical scripts
do not participate in this cooperative lock; do not run them concurrently.

The run initializes the existing budget at $0.0170944 carried spend, original
aggregate limit $2, at most 26 NEW calls including one connectivity preflight,
no retries, and stop on first failed case. At the frozen $0.40/$1.60 per million
token rates, maximum new reservations total $0.4046848. Conservative aggregate
reservations would be $0.4217792. These are operational estimates, not invoice
guarantees. Reverify current published prices before dispatch and stop/replan if
they differ; account/model availability is established by the approved live
preflight, never assumed. Unknown usage retains a full reservation and halts.

Reports preserve token usage, estimated new and aggregate costs, latency, events,
structured outputs and human-review context. A failed real call never falls back
to mocked responses. Only offline tests inject explicitly labeled CI doubles.

Current readiness is GO to request scoped paid approval, not permission to run.
Five human label acceptances do not approve paid calls or establish independent
diagnostic quality. Human semantic grading and synthetic-family limitations
remain after execution. No remaining benchmark cases are authorized.
