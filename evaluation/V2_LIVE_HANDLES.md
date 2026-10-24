# Phase 13.11: prepared single-case live handle verification

Only V2-02-01 is selected. Preparation neither calls a provider nor grants approval.
`evaluation.v2_handles_verification.prepare()` binds the latest $0.0188840 ledger,
its historical single-case dispatch marker, frozen dataset, rubric, human-reviewed
label worksheet, source plan, current code and generated handle catalog/snapshot.
The source five-case plan and older manifests/reports remain untouched.

## Actual provider contract, verified offline

The local MockTransport test invokes the real ChatOpenAI SDK serializer through
MeteredOpenAI and the classifier/investigator. It intercepts HTTP locally and
returns explicitly labeled CI fixtures; it never contacts OpenAI. The saved
provider-request-audit.json contains those serialized bodies and fixture metering,
not real evaluation results. No headers or credentials are saved.

The system prompt requests E01-style handles. The live response schema enumerates
only supplied handles, requires the exact contract version and snapshot, and
forbids investigator legacy selections/items. Persisted Pydantic parsers retain
legacy fields to read historical reports; provider-bound schema is deliberately
narrower. Python regenerates mapping definitions, rejects unknown/duplicate/stale
or cross-incident handles, and derives observations and provenance. Downstream
agents operate on trusted internal IDs in state, see short projections at the
model boundary, and have references translated back before state validation.

## Replay discrepancy

The initial Phase 13.10 example's fourth handle E11 refers to /logs/0/message.
It was incorrectly described as equivalent: it used different valid evidence.
The corrected example uses E01 (/actual_behavior), whose leaf hash matches the
malformed original fourth ID. The initial example remains preserved and is
illustrative only. The corrected example is the faithful reference-selection
demonstration. Neither is a real model response, and neither repairs or changes
the historical failed run. Runtime resolvers perform no guessed substitution.

## Complete overhead and budget

The prior byte check included message content and the Pydantic schema, not the
complete serialized SDK request. A new pre-transport guard checks the complete
visible JSON body, including roles, system/user content, actual response schema,
model/options and SDK wrappers, plus 4,096 bytes allowance, against the 32,768
input reservation. Output is limited to 1,536 tokens. Recorded usage exceeding
the reservation or missing usage halts further dispatch. This conservative byte
envelope includes visible overhead; unobservable provider token framing is not
an invoice guarantee. Unknown-usage failures retain full reservations.

At saved rates $0.40/$1.60 per million input/output tokens, six calls reserve
at most $0.0933888. Carry $0.0188840 into the original $2 budget; conservative
aggregate ceiling $0.1122728. One connectivity call plus up to five agent calls,
no retries, stop on first failure, no tools/fixes/additional cases. Reverify
current pricing before any approved dispatch; stop if rates differ.

## Future gate and review

The dormant existing `run_approved_single()` recognizes the new manifest version
and rechecks its complete bindings. It preserves the previous marker, links a
new single-use marker to the latest ledger, uses the cooperative process lock,
checks intervening spend and initializes the budget at the manifest's carried
spend. It has not been invoked for this manifest. Do not run historical scripts
concurrently or delete dispatch records to resume/retry.

Node coverage is zero until actual runner events exist. Post-output human review
is blank; approved pre-execution alternatives are human-only grading context.
No causal/resolution accuracy, validation success or live handle reliability is
claimed by offline preparation. GO means ready to request explicit scoped paid
approval, not approval granted. A future failed or interrupted run needs ledger
reconciliation and a new reviewed continuation rather than automatic retry.
