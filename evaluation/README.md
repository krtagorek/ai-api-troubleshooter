# Phase 13: real-model diagnostic evaluation

This package evaluates the existing LangGraph and five production agents against
label-free views of the existing 120 synthetic incidents. The first planned run
selects **20 of the 40 test cases**, deterministically balanced across HTTP status
codes (seed 314159). It never substitutes fixtures for a failed real model call.
Deterministic fakes exist only inside explicitly offline unit tests.

## Audit before spending

```bash
python -m evaluation audit
```

The audit validates 80 development + 40 test records, schemas, IDs, label joins,
matching inference/label payloads, answer field/text leakage, exact duplicates,
and normalized duplicates within/across splits. Normalization removes timestamps,
request/correlation/incident IDs and duration noise, preserving diagnostic content.
Dataset files are read only, never regenerated or resplit by this phase.

**Existing dataset finding:** 34 normalized groups span development and test.
The 40 test IDs are disjoint but their diagnostic content is not independently
held out. No answer-field leakage or exact payload duplicates were found. Treat
results as performance on a repetitive synthetic benchmark, not evidence of
unseen-case generalization. `--acknowledge-overlap` is required before a real run.
Do not tune prompts using these first 20 and then call their rerun independent.

## Reviewable paid-run plan

```bash
python -m evaluation plan \
  --model gpt-4.1-mini-2025-04-14 \
  --input-rate 0.40 --output-rate 1.60 --budget-usd 2 \
  --output reports/phase13-heldout20
python -m evaluation preflight --plan reports/phase13-heldout20/plan.json
```

Rates are USD per million tokens, explicitly supplied and bound to the plan.
Review current [official pricing](https://developers.openai.com/api/docs/pricing)
and [model availability](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
before approving. No model availability or account access is assumed. There is
no automatic model migration if the selected snapshot is unavailable. The above
rates were checked against the official model documentation on 2026-10-22.
The example is an evaluation model choice, not a change to the application's model.

The plan stores the dataset SHA-256, selected IDs, model, rates, seed, budget,
limits and an approval digest. `preflight` performs **offline configuration
checks only**; it does not claim network connectivity. Set `OPENAI_API_KEY` in
the private root `.env`, never in source, command arguments, frontend config,
or reports. Do not send the key in chat. Custom provider origins and enabled
tracing are rejected. A real structured OpenAI connectivity call runs only after
paid approval and is included in the same budget/call count as evaluation.

After the human explicitly approves the exact plan and overlap limitation:

```bash
python -m evaluation run \
  --plan reports/phase13-heldout20/plan.json \
  --approve-paid <approval_sha256-from-the-reviewed-plan> \
  --acknowledge-overlap --case-limit 3
```

The approval digest is not an API key. The CLI flag records deliberate operator
approval; automation must not manufacture consent. This agent has not run paid
calls without the user's approval. Missing credentials produce an actionable
blocked result, never simulated results. Commands return nonzero on blocked,
failed, stopped or partially failed evaluations. Existing plan files cannot be
silently replaced. Dispatched runs are single-use; an exclusive dispatch marker
also prevents two processes from racing the same plan. Create a fresh reviewed
plan after any dispatched interruption; there is no automatic paid resume.

The three-case pilot preserves the existing plan and selects its first three IDs.
It permits at most 16 calls including connectivity, stops on the first failed
case, and records the remaining 17 IDs without executing them. At the saved
rates its total maximum reservations are $0.2490368. This is an operational
estimate, not an invoice guarantee. The dispatch marker remains single-use:
do not reset the ledger or blindly run a fresh $2 budget for the remaining cases.
Review pilot spend and explicitly approve a continuation budget reduced by all
pilot spend and unresolved reservations before preparing another run.

## Budget and telemetry

The first run has at most **101 calls**: one paid connectivity preflight and up
to five calls for each of 20 cases. Workflow retries and provider retries are
both zero, calls are sequential, and there are no LLM judges or fallback models.
Each call reserves the approved input ceiling (32,768 tokens) and output ceiling
(1,536 tokens) before dispatch. The full 101-call reservation at the example
rates is approximately $1.57, below the $2 operational limit. Provider output
limits are set explicitly; inputs are bounded by serialized message/schema byte
size plus a conservative allowance for provider formatting. This is not an
absolute invoice guarantee; use provider project spend controls as well.

Reports track provider-reported input/output tokens, resolved model names,
latency, call indices, stages, observed-usage cost estimates, conservative spend,
and unresolved calls. Known usage releases unused reservation; failures with
unknown usage consume the full reservation and halt the run. Missing/oversized
usage also stops further calls. No network retry occurs after a timeout. Reports
are journaled before/after each dispatch, so interrupted work is visible.

The transport uses the fixed official HTTPS origin with redirects, environment
proxies and retries disabled. All model inputs pass through existing domain
validation/redaction. No raw prompts, credentials, driver exceptions or provider
error bodies enter reports. Parsed outputs are sanitized. Reports are written
atomically with restrictive permissions. Existing endpoint approval tools and
worker/database workflows are unchanged; this evaluator enables no external
diagnostic tools and executes no fixes.

## Quality scoring and honest limitations

### Evidence provenance contract (Phase 13.1)

`models.evidence.REFERENCE_SOURCES` is the shared immutable provenance mapping.
Reference resolution, investigator output, and evaluation grounding use it.
The investigator treats the model's source as an untrusted hint: after resolving
an allowlisted pointer and checking the exact observed substring, trusted code
derives its source. Both expected and actual behavior are `user` evidence;
neither becomes independently verified documentation. Unsupported roots,
invalid pointer escapes, missing values and fabricated quotations still fail.
General evidence retains its existing schema for compatibility; extracted
investigator observations still require a resolvable incident reference.

`tests/fixtures/inc0110_investigator.json` is a sanitized snapshot of the actual
failed pilot output, used exclusively for offline regression replay. Its seven
observations now pass with canonical provenance. This is not a new model run or
a full diagnostic quality result. The original pilot report, dispatch lock,
approval plan and spend ledger remain unchanged. Additional paid calls require
new approval and must carry forward the existing pilot spend.

Ground truth joins **after inference**; the graph receives only allowlisted
inference records. Rubrics/reference answers are evaluator-only. The same plan
and dataset hashes identify exactly which cases were selected.

- Classification accuracy compares the actual category to the dataset's explicit
  HTTP-status category mapping among scored cases. Read accuracy alongside
  scored-case coverage and workflow failure rate; unscored is not incorrect.
- Root-cause accuracy is a **lexical taxonomy proxy**: compare the selected
  hypothesis/diagnosis to the three reference causes for that HTTP status.
  Scores below 0.25 F1 abstain. This is not a semantic or human-graded accuracy.
- Resolution reference precision/recall/F1 and a conservative lexical pass
  (at least 0.5 recall, no unresolved negation) are proxies. Synonyms, nuanced
  negation and contradictory advice require human review.
- Root-cause/resolution accuracy uses complete-evidence cases only, including
  scored cases only in the denominator. Missing/ambiguous evidence is separately scored
  for low-confidence, explicit uncertainty/abstention.
- Evidence checks independently resolve incident JSON pointers, verify exact
  observations and source types, and check hypothesis/resolution references.
  Flags identify detectable unsupported claims; absence of flags does not prove
  all causal reasoning is true. Rejected cases remain failures and are not
  counted as explicit workflow failures, not incorrect diagnoses. Unattempted
  cases reduce coverage. A zero scored-case denominator yields null accuracy.
- No validation tools are configured. Truthful observed validation is
  `NOT_TESTED` with no checks. There is no claim of a validated fix. Actual fix
  effectiveness remains `NOT_TESTED`; human semantic review remains required.

The existing workflow may end `awaiting_evidence` rather than generate a final
report without tools. Evaluation scores the validated graph state and clearly
records that workflow status. It does not force completion or fabricate checks.
Grounded semantic quality and real fix effectiveness cannot be established by
word overlap, HTTP status alone, or model self-assessment.

## Artifacts and CI

### Phase 13.2 offline hardening

`canonical_scalar` and `observation_matches` define the shared agent/evaluator
grounding contract. Strings allow exact nonempty literal substrings; integers,
finite floats, booleans and null require the whole JSON scalar representation.
For example, integer 403 accepts only `403`, not `40`, `403.0` or
`status_code 403`. Float 1.0 uses `1.0`; booleans use lowercase `true`/`false`;
null uses `null`. No trimming, numeric coercion, container serialization or
repair of raw model observations occurs. Nonfinite numbers are rejected.
Canonical serialization follows Python JSON float formatting, including `-0.0`
and exponent notation; it is deterministic here, not cross-language RFC 8785.
Existing reference allowlists and trusted provenance derivation remain enforced.

Raw sanitized structured provider outputs are journaled before agent processing.
Historical reports are immutable audit evidence, not rewritten with corrected
observations or new metric semantics. The recorded INC-0025 regression rejects
the original response; a separate offline copy with `403` succeeds. This is
neither a new model response nor a paid evaluation result.

Root-cause prompts now distinguish symptoms, assumptions and competing causes.
Intermediary versus application explanations are compared only when applicable;
neither is preferred from held-out reference answers. Competing hypotheses with
no referenced discriminating evidence are downgraded to inconclusive with no
selected cause. Invalid discriminating/contradicting evidence references fail.
Tentative diagnoses permit only diagnostic plans; inconclusive diagnoses abstain
from fixes and retain proposed diagnostic checks on the diagnosis. Diagnostic
plans containing common disruptive verbs are conservatively rejected. Every
accepted plan requires human approval, and nothing is executed automatically.

These guards cannot establish semantic causal entailment or comprehensively
classify arbitrary natural-language actions. Valid evidence IDs can still be
misinterpreted; keyword action screening can miss paraphrases or reject benign
phrasing. Human semantic review and a newly authorized pilot remain necessary.
Reasoning tests use development incidents and generic competing explanations;
held-out snapshots are used only for explicitly requested contract replay.

Offline verification: 377 passed, 2 skipped, including disposable PostgreSQL
integration tests. Dataset, prior plans/reports and budget ledger remain unchanged
at aggregate estimated spend $0.0096700 of $2. No paid calls were made.

Each output directory contains `plan.json`, `evaluation.json`, `evaluation.md`,
and (only after authorized dispatch) `.dispatch.lock`. Before a real run,
`metrics` is null and real-model execution is false; null is never a zero score.
Review reports for overlap, provider failures, schema/evidence rejections,
coverage, uncertainty handling and manual-review limitations before claiming
model diagnostic quality.

```bash
python -m pytest tests/test_evaluation.py -q
# Existing PostgreSQL regression suite; uses disposable databases:
python -c 'from dotenv import load_dotenv; import subprocess,sys; load_dotenv(".env"); sys.exit(subprocess.run([sys.executable,"-m","pytest","-q"]).returncode)'
```

CI tests forbid real ChatOpenAI construction unless an explicit test-local fake
is installed. They test leaks/duplicates, deterministic selection, approval and
manifest tampering, budget reservation/uncertainty, safe telemetry, grading,
reporting, and no paid calls. Passing CI is not a real connectivity or quality
result. Production quality results remain blocked until credentials and explicit
paid approval are supplied.
