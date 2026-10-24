# Phase 13.10: LLM-friendly evidence handles

`evidence-handles-v1` preserves the Phase 13.8 cryptographic IDs internally.
`handle_catalog()` sorts existing scalar catalog entries by canonical reference
and assigns E01, E02, etc. It exports literal observations, references, source and
type, not the long internal IDs. Mapping definitions are never read from model
output. Unknown extra mapping fields are rejected by existing strict schemas.

Handles are local labels, not globally unique identifiers. They MUST be used
with the exact version and snapshot envelope. The snapshot binds the complete
trusted catalog, including its incident-scoped internal IDs. A changed incident
or incident identity changes the snapshot; replay against that context fails.
Bare E01 does not carry provenance or identity and is never globally resolved.
The model must echo the snapshot token; a malformed echo still fails closed.

Classifier responses select handles in evidence_references. Trusted code stores
canonical pointers for existing report/UI compatibility. Investigator responses
select selected_evidence_handles; code derives the full observation, provenance,
reference and internal ID. No guessed scalar text is used. Downstream input
projections replace selected internal IDs with the same handles in evidence and
causal-reference fields. Output references are translated back to internal IDs
before state/checkpoint validation. No handle mapping is persisted in graph state.

Root-cause and resolution outputs may cite only selected evidence. A valid handle
for an unselected catalog entry is rejected there. Existing evidence-ID linkage,
literal scalar grounding, redaction and human-approval guards remain active.
Valid references still do not prove causal entailment or evidence sufficiency.

Legacy unversioned IDs, pointers and local evidence items remain readable/replayable
for V1/V2 historical compatibility. A declared handle-contract classifier or
investigator output cannot mix legacy formats. Raw structured provider outputs
remain recorded before handle translation; old reports/checkpoints are unchanged.
New prompts/schema descriptions require handles; legacy acceptance remains an
explicit compatibility surface, not permission to guess or repair hash IDs.

The Phase 13.9 malformed-ID response remains rejected offline. The separate
short-handle example selects its three valid entries and the actual_behavior
entry corresponding to the malformed fourth reference's leaf hash. The original
provider response and failed-run status are never modified or rescored as success.

Local o200k_base tokenization of equivalent V2-02-01 classifier input JSON measured
1,894 tokens with long IDs versus 1,282 with the handle version/snapshot envelope:
612 tokens saved (about 32.3%). Catalog lists alone measured 1,578 versus 903.
This is not an actual API usage measurement; system prompts, output schemas and
provider chat framing are excluded. A public tokenizer vocabulary was downloaded
for local counting, with no model inference or charges.

Compatibility risks include the new version/snapshot fields in output schemas,
handle-format failure, snapshot-copy failure and conservatism of legacy parsing.
Handles do not guarantee that a real model will select useful evidence. The
latest aggregate spend remains $0.0188840 of $2. No paid dispatch/approval manifest
is prepared by this phase; a future single-case pilot needs fresh bindings and
explicit paid approval after reviewing offline results.
