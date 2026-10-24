# Evidence selection quality after Phase 13.12

The five-field live investigator output and existing agent output schemas are
unchanged. Prompts now prioritize discriminating observed comparisons rather
than repeat the classifier's status citations: timing versus threshold, actual
versus expected identifiers, false/null values, and correlated supporting and
contradictory observations. Root-cause prompts review those comparisons before
generic origin speculation; they do not prefer any benchmark cause.

`tools.evidence_selection.assess_selection` uses only sanitized inference inputs
and the trusted catalog. It imports no dataset labels or cause taxonomy. It
identifies structured diagnostic leaves and non-boilerplate diagnostic messages.
After a nonempty valid model selection, trusted Python adds missing existing
candidate facts, preserving identifiers, canonical scalar observations and
provenance. It never synthesizes a comparison result, diagnosis or tool outcome.
The original provider selection remains unchanged in the provider audit, while
the resulting state shows the actual delivered evidence. Assessments expose
added/omitted IDs for reproducible coverage audits.

Additions are bounded to 32 and all-or-nothing: no silent truncation of comparison
peers or contradictory observations. Values that would be changed by existing
redaction are not force-delivered. A timestamp with ten digits currently triggers
the personal-identifier string rule; that conservative false positive is retained
rather than weakening redaction. Large/redaction-sensitive omissions block trusted
coverage and add a sufficiency gap. An empty model selection remains empty/missing;
the trusted pass does not override explicit abstention by selecting everything.

For new cryptographic evidence IDs, routing independently checks candidate coverage,
even if an investigator marked complete. Status-only evidence is insufficient when
diagnostic facts are available. At least two distinct candidate fact groups are
required before root-cause analysis; a single key/value record counts as one fact,
not two observations. No candidates, missing peers, redaction conflicts or bounds
failures downgrade a complete claim. Partial/conflicting/missing model outputs and
explicit model gaps are not automatically promoted. Legacy report-local IDs retain
their original routing compatibility.

These checks determine conservative readiness to ATTEMPT analysis, not proof of
causal sufficiency. Structured fields can be irrelevant, two observations can
share one underlying source, and unrecognized log layouts can be missed. All logs
remain untrusted; correlated IDs and opposing records need causal interpretation.
The root-cause agent still must cite evidence and express uncertainty; no evidence
addition justifies automatic remediation or validation success. More conservative
routing can reduce recall and request more user input.

Tuning/tests use development inference records across all eight categories and
generic synthetic timing/counterevidence fixtures. Held-out reference diagnoses
are not inserted into prompts or development examples. No V2-02-01-specific rule
or expected diagnosis is encoded. Historical real-model outputs and their human
review remain intact, with no retroactive rescoring or claim of improvement.

Offline full regression: 487 passed, 2 skipped, including disposable PostgreSQL
tests. No paid calls or new paid manifest/dispatch. Aggregate estimated spend
remains $0.0260008 of the original $2 budget. A future controlled pilot needs new
bindings and explicit approval; live diagnostic improvement is unverified.
