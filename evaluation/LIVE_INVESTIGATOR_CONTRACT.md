# Phase 13.12: minimal live investigator output

`LiveLogInvestigatorOutput` has exactly five required fields:
`evidence_contract`, `evidence_snapshot`, `selected_evidence_handles`,
`completeness`, and `diagnostic_gaps`. A gap contains a description and evidence
needed to resolve it. No live evidence objects, observations, provenance, paths,
internal IDs or model-defined mappings are accepted. Extra fields fail validation.

The live prompt requests only this shape. The actual provider JSON schema binds
the exact supplied version/snapshot and enumerates the available handles. The
ChatOpenAI path requests `json_schema` with `strict=True` for this output. If a
provider rejects strict mode or the schema, execution fails rather than retrying
or switching to a legacy format. Local MockTransport tests verify the SDK's
actual serialized schema and strict flag; no live support claim is fabricated.

Python resolves selected handles against the sanitized incident snapshot and
copies canonical observations, references, provenance and internal IDs with
conservative reliability 0.5. Unknown, stale, cross-incident and duplicate handles
remain rejected. Empty selection is supported for abstention: completeness is
forced to missing and a gap is provided if the model supplied none. Completeness
with nonempty selections remains a model claim, not semantic verification.

The legacy schema remains for old reports and fixtures. Its resolver lives in
`agents/legacy_log_investigator.py` and requires an explicitly injected output
source; it cannot construct a default provider. It is never an automatic fallback
from the live factory. Historical replay tests call the adapter explicitly. Old
checkpoint/domain evidence shapes and strict scalar grounding remain unchanged.

The exact Phase 13.11 output is stored as a regression fixture. It remains rejected
by both the new live parser (legacy fields) and the legacy adapter (declared handle
contract combined with evidence objects). A separately constructed minimal output
selecting all 22 existing handles succeeds. The original response, workflow failure
and ledger are untouched. This demonstration is offline, not another model run.

Original sanitized parsed provider output is captured before domain adaptation.
No model observation/provenance is silently substituted to make a failure pass.
Downstream causal evidence linkage, redaction, human approval and no-tool controls
are preserved. Live semantic quality and successful real-model use of the new
shape remain unverified. All prior paid manifests are stale after these code
changes. A future controlled pilot needs new ledger/code bindings and explicit
approval, carrying $0.0225020 of the original $2 aggregate budget.
