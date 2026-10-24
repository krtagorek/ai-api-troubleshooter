# Phase 13.6: human label review before execution

This review concerns the frozen input and reference label, **not model output**.
It is separate from Phase 13.5's post-execution semantic-review worksheet.
No model output, rating, human decision or paid approval is fabricated.

Open `reports/phase136-v2/pre-execution-label-review.json` locally. It contains
five selected cases with sanitized inference evidence, human-only injected cause,
what evidence supports, what remains unidentifiable, alternatives, diagnostic
checks, conditional safe resolutions and unsupported assumptions. Do not send
this worksheet or the revision proposals to agents or model prompts.

For each case, edit only `human_review`:

- `decision`: `ACCEPT`, `ACCEPT_WITH_ALTERNATIVES`, `NEEDS_REVISION` or `REJECT`.
- `reviewer_id`: your human reviewer identity or attributable reviewer handle.
- `rationale`: at least ten characters explaining the decision from evidence.
- `timestamp`: actual review time in ISO 8601 with timezone; future times fail.
- `accepted_alternatives`: explicit alternatives if accepting with alternatives.

All fields are deliberately blank initially. Reviewer identity is an attestation,
not an authenticated identity service. A valid review is not authorization to pay
or execute. Material, label, dataset, rubric and plan bindings must remain intact.
Use a separate copy per reviewer if applying the existing multi-review protocol.

```bash
# Validate the five human entries; no network, model, graph or database access:
.venv-py311/bin/python -m evaluation.v2_preexecution validate \
  --worksheet reports/phase136-v2/pre-execution-label-review.json
```

Exit 2 means entries are incomplete or invalid; this is expected for the delivered
blank worksheet. Once all five have valid human decisions, exit 0 reports
`all_cases_reviewed=true`. That does NOT imply `labels_accepted=true`: revision
or rejection blocks the original plan. Inspect the printed decisions/status.

## Revisions and adjudication

Two optional narrower-wording drafts are in
`reports/phase136-v2/conditional-revision-proposals.json`. They do not assert that
a human requested or approved revision. Compression middleware ownership and
collection serializer ownership are assumptions worth reviewing. The ambiguous
retention and timeout cases must not require guessing hidden injected causes.

`revision_proposal()` produces a `v2.1-label-proposal-1` sidecar bound to the old
label, dataset and plan hashes. It never applies it. If a human chooses revision,
use the existing `evaluation.v2_review.label_adjudication` two-reviewer plus
third-adjudicator workflow. Retain source reviews and proposals. An approved
revision needs a separate benchmark version, audits and newly frozen plan.
Never edit frozen V2 files or silently score against draft wording.

## Runner handoff, without execution

After all labels are accepted, a future operator can prepare a new, label-free
handoff file using:

```bash
.venv-py311/bin/python -m evaluation.v2_preexecution prepare-runner \
  --worksheet reports/phase136-v2/pre-execution-label-review.json \
  --output /tmp/v2-five-case-handoff.json
```

The command is blocked while reviews are pending/rejected/need revision. It does
not instantiate a model, invoke a graph, grant paid approval, or create a dispatch
lock. The handoff contains only sanitized incident inputs, hash bindings, model,
limits and carried-budget policy. Human-only reference material stays outside.
Future guarded dispatch initializes shared state from those inputs, uses the
existing five-agent workflow with no external tools/retries, and passes returned
states to `V2Adapter.score()` only after inference, then `V2Adapter.summarize()`.
The integration contract has 26 maximum new calls including preflight and stops
on the first failure. No mocked outputs substitute for actual model responses.

Execution remains disabled even after handoff preparation. Actual paid dispatch
still requires fresh explicit approval, current pricing/account checks, checking
for newer spend, and atomic budget reservation/single-use dispatch. This phase
prepares the handoff, not an authorized paid-run command. The $2 ledger carries
$0.0170944 prior spend; no ledger or dataset is reset.

To generate a separate blank worksheet (never overwrite an existing review):

```bash
.venv-py311/bin/python -m evaluation.v2_preexecution create \
  --worksheet /tmp/v2-preexecution-label-review.json
```

Offline regression tests exercise synthetic reviewer identities only inside tests.
They are not real human reviews and do not populate delivered artifacts.
