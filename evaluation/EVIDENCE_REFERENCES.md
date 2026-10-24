# Phase 13.8: trusted evidence catalog

`tools.evidence_catalog.build_catalog()` deterministically enumerates scalar leaves
of the existing allowlisted incident roots. It revalidates/redacts the incident,
excludes sensitive keys and label fields, and derives canonical pointer, scalar
observation and provenance in trusted code. Objects/arrays are traversed, never
offered as observations. Empty strings are omitted and nonfinite values fail.
The catalog fails closed above 2,048 entries rather than silently truncating.

IDs use `ev:<incident-snapshot-hash>:<path-and-value-hash>`, with 96-bit hash
components. They are deterministic over canonical JSON, independent of map order
and thread ID, and intentionally change when the incident snapshot changes.
They are references, not authentication credentials or database primary keys.
Catalogs are regenerated from sanitized inputs, not accepted from model output.

Classifier prompts now request catalog IDs in `evidence_references`. Trusted code
resolves them and stores canonical pointers for compatibility with existing
reports/UI. Investigator output prefers `selected_evidence_ids`: code copies
full literal observations and provenance and assigns conservative reliability
0.5, without asserting that selected evidence is semantically sufficient.
The model may separately report completeness and gaps. Downstream hypotheses
reference the trusted IDs of selected evidence. IDs do not prove causal entailment.

Legacy classifier pointers and investigator `evidence.items` remain supported.
Legacy observations still undergo exact non-string scalar/substring string
grounding; no paraphrase, prefix or numeric coercion is allowed. Original parsed
provider outputs are journaled before normalization; historical files are not
rewritten. Legacy local evidence IDs remain readable. New `ev:` IDs are also
rechecked at state/checkpoint boundaries for matching reference, source and exact
observation. Unknown, stale and cross-incident IDs fail.

The only pointer compatibility alias removes exactly one leading `/incident/`
wrapper, then requires an existing allowlisted scalar catalog path. There is no
recursive prefix stripping, percent decoding, URL/file resolution, guessed root,
leading-zero list index coercion or fallback to unsupported paths. Sensitive,
label, container, nonexistent and malformed references remain rejected.

The recorded V2-02-01 classifier failure is preserved in
`tests/fixtures/v2_02_01_classifier.json`. Offline replay accepts its valid
wrapper-prefixed scalar references and returns canonical incident-relative paths.
This only demonstrates compatibility; it does not rerun the case or retroactively
change its recorded workflow failure. Tests also retain the V1/V2 legacy fixtures
and continue rejecting fabricated numeric observations such as `status_code 403`.

No dataset, report, database schema or ledger migration is required. Catalogs add
prompt/input overhead and bound their own entry count; existing provider byte and
token reservation controls still apply. The changed source code invalidates the
old paid-approval manifest. A new reviewed manifest/plan must incorporate the
latest ledger ($0.0175852 estimated aggregate spend of $2), not reuse older
$0.0170944 carry-forward checks. No paid rerun is authorized by this phase.
