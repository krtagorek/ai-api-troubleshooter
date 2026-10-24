# Phase 13.5: offline V2 evaluation and review

`evaluation.v2.V2Adapter` verifies the frozen five-case plan, dataset/rubric hashes,
prior ledger hash, original $2 aggregate budget and $0.0170944 carried spend before
returning inputs or scoring outputs. It never runs a graph, model, tools or jobs.
There is no execution or approval CLI. The original plan remains non-executable.

```python
from evaluation.v2 import V2Adapter
adapter = V2Adapter('reports/phase134-v2/plan.json')
incidents = adapter.inputs()  # Incident models only, no labels/families/rubric
coverage = adapter.summarize([])  # Zero attempted cases; accuracy is null
# After separately authorized inference elsewhere:
# metrics = adapter.score(completed_state)
```

Only completed state enters scoring. V2 labels join after that boundary; they
must never be passed to agents. `v2-scoring-1` uses the V2 category mapping and
does not call V1 scoring or use its CAUSES taxonomy. It checks frozen incident
identity, literal grounding/provenance, final abstention, plan safety and truthful
validation. These are deterministic structural checks, not semantic entailment.
Skipped validation is distinct from a verified NOT_TESTED model output.

Coverage includes categories, families, quality, log shapes, workflow outcomes,
grounding and validation applicability. Classification uses scored cases only;
workflow failure and unattempted counts are separate. Semantic causal/resolution
accuracy is null until human review. No V1 lexical similarity is substituted.
Actual agent execution coverage requires future runner events; the adapter does
not fabricate it from retry counts. V1 interfaces and historical scores remain
untouched, with explicit compatibility tests.

## Five-case inspection and human worksheet

`reports/phase135-v2/planned-case-inspection.json` records this agent's offline
inspection of all five planned inputs and labels, not independent human review.
In particular, the retention-lock and upload-buffer cases expose insufficient
evidence for their latent causes and must reward abstention. The compression and
cardinality cases require reviewers to distinguish supported mechanism from
unverified component ownership. No labels were changed or used to refine prompts.

`human-review-worksheet.json` has blank ratings, identities and output hashes.
No model outputs exist for these cases. Leave worksheets pending until an actual
sanitized output exists; do not count blank fields as zero, review or agreement.
Two independent humans should each complete a separate copy using
`data/benchmark_v2/semantic_rubric.json`. Missing/N/A ratings require an explicit
`N/A:` or `NOT_EXERCISED:` applicability reason. Validation scores allow only the
rubric's 0/3 anchors. No model is used as a judge.
Set the worksheet status to `COMPLETED` only after filling the required fields.

```python
from evaluation.v2_review import bind_review, compare_reviews, adjudicate_reviews
# bound = bind_review(human_record, scored_metrics)
# comparison = compare_reviews(first_human_record, second_human_record)
# final = adjudicate_reviews(first, second, 'third-reviewer', final_record,
#                            'Evidence-based rationale for resolving the disagreement.')
```

`bind_review` requires the exact scored output hash/incident. Comparisons require
different reviewer IDs for the same output; adjudication requires a third ID and
retains both source review hashes. Identity strings are attestations, not an
authentication system. Store original review records alongside sidecars; never
replace them with the decision. `semantic_summary` computes human dimension means
and N/A counts, not adjudicated case accuracy or claims of human completion.

## Label adjudication

If either reviewer disputes a frozen label, record the proposal with
`label_adjudication(original_label, proposed_label, reviewer_ids=[...],
adjudicator_id=..., decision='retain'|'propose_revision'|'exclude', rationale=...)`.
It returns a hash-bound evaluator-only sidecar and does not edit any dataset.
Two distinct reviewers and a third adjudicator are required. A revision or
exclusion requires a separately versioned benchmark, new audit and frozen plan;
never silently repair labels or exclude difficult cases from an existing run.
Do not automatically apply these decisions to model prompts or the current plan.

The budget checks verify the latest explicitly referenced ledger; they are not
a concurrent paid-run reservation system. Future dispatch must recheck for newer
spend and atomically reserve against the aggregate budget. This phase neither
authorizes nor implements paid execution. Shared synthetic generator bias and
the need for independent human review remain limitations.
