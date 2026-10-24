# Diagnostic Benchmark V2

80 synthetic incidents: **40 development / 40 test**, with 16 distinct mechanism
families and five variants per family. Family ownership is assigned before
variant generation; no family occurs in both splits. Each split contains all
eight categories (five cases each), 24 complete and 16 ambiguous cases, and eight
examples of each of five log layouts. No network or model is used to generate it.

`inference/*.jsonl` contains only domain-model incident inputs. Ground truth,
category, family, alternatives and semantic-review expectations live separately
in `labels/*.jsonl`; family assignments and variant membership live in
`manifest.json`. These are evaluator-only. Load inference records directly with
`models.incident.Incident`; never pass labels, the manifest, this generator's
scenario catalog or the review rubric into agent prompts. Scalar evidence
pointers in labels guide post-inference review, not inference.

Ambiguous cases omit the discriminating observations. Their latent generator
cause is not a demand that a model guess the hidden answer: appropriate
abstention, alternatives and safe discriminating checks are the target behavior.
Variants are related observations of the same mechanism, not independent samples.
Always report family-level coverage alongside incident counts.

## Regeneration and audits

From the project root, using the existing Python environment:

```bash
python -m data.benchmark_v2.build audit
# Generate a reproducible copy into a NEW directory; existing outputs are refused:
python -m data.benchmark_v2.build generate --dataset /tmp/troubleshooter-benchmark-v2-copy
python -m pytest tests/test_benchmark_v2.py -q
```

Generation is deterministic (fixed specification version/seed 1324001; no ambient
randomness). The committed dataset and prior reports are never overwritten by
generation. Audits check schema, joins, IDs, field/text answer leakage, fixed
family assignment, normalized payload duplicates, log-layout-independent evidence
duplicates, cross-split near duplicates (diagnostic token Jaccard >=0.85), coverage,
and V1 normalized payload/exact mechanism-text overlap. Near matching is a lexical
screen, not semantic proof. JSON file hashes bind the frozen plan to its inputs.

## Semantic review and planning

`semantic_rubric.json` defines independent human review, 0–3 anchored dimensions,
hard safety failures, applicability, inter-reviewer agreement, abstention and
coverage metrics. Its status is **NOT_REVIEWED**. No scores or successful review
are fabricated. Workflow errors are reported separately from scored diagnostic
accuracy. Unscored cases and unexercised validation branches are not wrong
diagnoses or successful validations. Lexical similarity is an optional proxy.

The prepared plan is `reports/phase134-v2/plan.json`: five different test families,
three complete and two ambiguous cases, covering five statuses and all five log
layouts. No evaluation has run. Planning can be reproduced into a new file:

```bash
python -m data.benchmark_v2.build plan --output /tmp/v2-five-case-plan.json
```

The plan carries forward **$0.0170944** estimated spend against the original $2
aggregate limit. Saved rates are planning inputs only and must be reverified
before paid approval. Maximum planned calls: 26 including connectivity, no retries,
stop on first failure. New full reservations at those rates total $0.4046848;
these are operational estimates, not invoice guarantees. The plan is deliberately
non-executable and unapproved. Existing V1 evaluation code has a hardcoded taxonomy
and must not score V2; a V2-aware adapter and fresh paid authorization are required.

V1 data, historical audits, dispatch markers and spending records are preserved.
V2 shares one synthetic author/generator across splits and contains related
within-family variants. Structural isolation passes; independent semantic review,
broader untouched data and real production effectiveness remain unverified.
Do not tune on the test catalog or selected test records after the plan is frozen.
