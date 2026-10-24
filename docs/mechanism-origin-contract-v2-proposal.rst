Mechanism versus origin: contract investigation and V2 proposal
=============================================================

Decision: no application correction under the current contract
--------------------------------------------------------------

No agent prompt, model schema, counterevidence validator, readiness rule, routing,
report eligibility, persistence or authorization behavior is changed in this milestone.
Only characterization tests, a sanitized read-only regression copy, this proposal
and investigation reports are added. This is not an executable diagnostic contract.

The guard in agents/root_cause.py treats every list with more than one hypothesis
and no discriminating_evidence_ids as unresolved. It forces status=inconclusive,
selected_hypothesis_id=null and confidence=0. It cannot distinguish a possible
upstream origin from an immediate mechanism or a genuinely competing cause.
This is a global-discrimination requirement, not proof that hypotheses are
mutually exclusive. It can withhold useful mechanism analysis, but removing the
check would also admit genuinely unresolved competing causes.

The current strict Hypothesis schema has only description, confidence, supporting
and contradicting evidence IDs, assumptions, and evidence assessments. Assessment
relations connect evidence to a hypothesis; they do not relate hypotheses to each
other. Diagnosis has one selected hypothesis and one undifferentiated collection
of discriminating IDs. Additional role/relationship fields are rejected by the
provider-bound Pydantic schema. Free-text descriptions and predictions cannot
serve as trusted executable semantics. Confidence, list ordering, incident IDs,
case labels and identical supporting references are not causal hierarchy.

Existing counterevidence validation checks selected references, explained
contradictions and narrow coexistence guards. It is not a general causal entailment
checker. The tests demonstrate that unsupported intermediary text can cite valid
shared observations and pass structural reference checks. This finding is not
approval of the intermediary claim and is not addressed by silently selecting H1.

Preserved manual run
--------------------

Investigation 89738f60-5fe9-4d58-b0ad-1b2b95f0213f has a grounded issuer comparison,
complete selected evidence, two hypotheses and no discriminating IDs. Its
accepted diagnosis is inconclusive after the guard. Original provider diagnosis
and safety-replaced wording are unavailable; they are not reconstructed.
The evidence supports consideration of an issuer discrepancy, but it does not
verify the verifier's matching policy or identify where the discrepant value
originated. The user explanation is not independent proof. The reported signature
result is not independently verified ingestion provenance.

The preserved output remains held. The source HTTP-request noun wording remains
accepted by the existing corrected safety boundary. Repeated paid calls are not
an appropriate way to establish a contract-level distinction.

Independent generic characterization
-------------------------------------

Synthetic fixtures cover issuer mismatch, actual/expected resource identifiers,
request counts above an explicit limit in a reported window, and decoding failures
with an unknown producer. Their existing readiness checks allow an attempt at
qualified analysis, never definitive confirmation. In all four, adding a second
hypothesis without discriminating IDs causes the same inconclusive guard, regardless
of relative confidence or ordering. Truly competing authentication explanations
and conflicting correlated observations remain held.

A separate single selected hypothesis with explicit comparison references,
uncertainty, supporting assessments and a human-approved diagnostic plan still
qualifies for a legacy tentative report with NOT_TESTED validation. This confirms
that the earlier report-finalization correction is independent of the present gap.

Proposed version: diagnostic-reasoning-output-v2-draft
-----------------------------------------------------

This is an agent output-contract proposal, separate from endpoint approval and
owner-approved diagnostic semantic contracts. It is not registered or activated.
A separately authorized implementation should introduce strict version dispatch:
V1 remains readable with its conservative guard; V1 must never be automatically
upgraded by inventing roles or selecting a hypothesis.

Suggested provider proposal fields:

* contract_version: diagnostic-reasoning-output-v2.
* hypotheses[].claim_scope: observed_mechanism, upstream_origin, or unknown.
* hypotheses[].mechanism_observation_handles: supplied trusted observation handles,
  empty if no mechanism observation is asserted.
* hypotheses[].origin_of_hypothesis_id: a proposed mechanism parent or null.
* relationships[]: source/target hypothesis IDs, proposed relation
  (may_explain_origin, competes_with, may_coexist, unknown), evidence handles and
  falsifiable predictions. No relation is assumed from confidence or wording.
* diagnosis.analysis_scope: observed_mechanism, upstream_origin, or inconclusive.
* diagnosis.qualifying_evidence_handles: facts supporting the selected qualified
  mechanism, separate from discriminating evidence between competing origins.
* diagnosis.discriminating_evidence_handles: facts testing alternatives at the
  same declared scope. Unknown origin evidence remains missing, never fabricated.

Retain uncertainty, assumptions, selected hypothesis, diagnostic checks and
missing evidence. Preserve snapshot-bound handle resolution, strict output,
redaction, human approval, existing report schemas and honest NOT_TESTED.
Origin-of references must exist, be acyclic and cannot point to themselves.
Unknown relationships remain unknown; supporting assertions are not contradictions.

Crucial validation boundary
----------------------------

A model-supplied claim_scope or relationship is a proposal, not trusted authority.
The following server-derived proof must exist before such annotations can affect
routing:

1. A versioned, allowlisted deterministic observation evaluator binds the proposed
   mechanism to the exact snapshot, comparison operands, scope and constraints.
2. Literal inequality supports a discrepancy observation; it does not by itself
   prove a rejection cause. Any needed matching/rejection rule must be grounded in
   correlated verifier diagnostics or a separately approved semantic contract.
3. A proposed upstream origin without source-specific evidence is explicitly
   conditional/unverified. It cannot be published as an established cause or used
   to claim a proxy exists. Unverified relations cannot exempt true competitors.
4. Only a verified mechanism-support proof, selected grounded hypothesis and
   qualified language can justify advancing while upstream origin remains unknown.
   Genuine same-scope competitors without discrimination continue to hold.
5. Counterevidence and conflicting evidence must be reviewed at the stated scope.
   Labels alone, free-text keyword parsing, confidence gaps and hypothesis ordering
   cannot mint the proof. Arbitrary model-generated semantic operators are forbidden.

Observation proofs may use ordinary sanitized observations without claiming signed
provenance. Protected owner semantics still require active approved versions,
verified ingestion and fresh permits/revocation checks. A new field must not bypass
any Phase 14 authorization fence. Definitive confirmation stays disabled unless
separately established. Diagnostic checks and fixes are never automatically executed.

Evidence and authorization still needed
---------------------------------------

For literal configured/received inequality, no new owner contract is needed merely
to observe different strings. For attributing a particular verifier rejection to
that difference, obtain sanitized correlated rejection decisions and matching
policy. To establish origin, obtain stage-specific, correlated observations and
producer/source authority. Do not request complete JWTs or reusable credentials.
The current approved-contract implementation is limited to bounded fencing
semantics; it grants no issuer-normalization or general causal-hierarchy authority.

A schema change alone is insufficient: the validation/proof rules and their threat
model must be reviewed before enabling any new routing. If these cannot be defined
without guessing for a scenario, keep the inconclusive outcome.

Separately authorized implementation acceptance
-----------------------------------------------

Require generic tests for compatible mechanism/origin claims, genuinely competing
causes, missing peers, conflicting scopes, unsupported intermediary assertions,
cyclic/dangling relations, false role labels, fabricated handles, stale snapshots,
unknown contract versions, policy uncertainty, revoked semantic authority and
checkpoint replay. Validate provider schema, output safety, legacy compatibility,
report truthfulness, human approval, transactional publication and owner isolation.

No new paid pilot should be prepared until that implementation passes offline
acceptance. This milestone makes no new diagnostic-accuracy claim.
