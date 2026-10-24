Manual-test safety precision and legacy tentative reports
=======================================================

Scope and findings
------------------

The source observation ``JWT issuer validation rejected the request`` was rejected
because the credential-request filter interpreted the HTTP-request noun as an
action and JWT as its requested material. The correction masks only bounded HTTP
noun constructions for action detection. It never exempts a whole sentence.
Solicitation verbs, indirect requests, mixed clauses, conditions, nested JSON,
Unicode normalization, and raw credential protection remain in force.

Sanitized token metadata requests require an explicitly bounded metadata target.
A sentence mentioning sanitized metadata cannot authorize a separate raw token
request. Redaction of token-prefixed keys is unchanged in frontend and backend.
Literal source evidence is not rewritten to pass grounding or publication.
Ambiguous generated action fields remain fail-closed.

The preserved manual issuer investigation has accepted tentative analysis and a
diagnostic plan, but multiple safety replacements. Original provider wording is
not retained by the ordinary worker. These replacements cannot be classified as
true/false positives or reversed. A sanitized read-only copy is stored at
``tests/fixtures/manual_issuer_hold.json`` with its original state digest.
The database investigation, checkpoints and absence of a report remain unchanged.

Separate legacy eligibility
---------------------------

``legacy_tentative_report_decision`` applies only when no trusted-semantic result
is present. It requires:

* A tentative positive-confidence diagnosis, selected hypothesis and explicit uncertainty.
* Snapshot-grounded, nonambiguous catalog evidence and current qualified-analysis readiness.
* Discriminating references covering the recognized comparison/diagnostic observation.
* Supported hypothesis references, supporting assessments and validated counterevidence.
* An evidence-linked diagnostic-only plan requiring human approval.
* NOT_TESTED validation; no assumption that a completed validator executed a check.
* No withheld/replacement markers or unsafe diagnostic requests, including gaps,
  prerequisites and verification steps; no disruptive diagnostic instructions.

Known unsupported certainty/authority assertions are rejected. This is structural
and bounded semantic validation, not a general proof of causal truth or an
exhaustive natural-language safety classifier. Human review remains mandatory.

The existing trusted-contract eligibility function and its requirements are
unchanged. Any existing contract result, including held/denied metadata, prevents
legacy fallback. Contract evaluation/publication still requires fresh authorization,
permit consumption and revocation fencing. Feature flags grant no authority.

Routing and persistence
-----------------------

Eligible ordinary diagnoses now route from NOT_TESTED validation to final_report.
The unchanged report schema retains tentative diagnosis, supporting evidence,
uncertainty, human approval and NOT_TESTED. A server-authored limitation states
that ordinary observations have no verified signed-ingestion provenance or
trusted diagnostic-contract authority.

Ineligible legacy diagnoses retain awaiting_evidence with a specific safe reason,
including diagnostic_output_safety_review_required. Earlier evidence holds and
existing passed/failed validation paths retain their historical behavior.

Immediately before storing an unvalidated completion, the existing transactional
publication boundary rechecks legacy eligibility and recreates the canonical
report. Recovered checkpoints cannot substitute false summaries, claim signed
provenance or validation success, omit required validation, or publish mismatched
state. Existing report/job/event atomicity, leases and unique report constraints
provide rollback and idempotency; no new migration is needed.

Offline acceptance and operations
--------------------------------

The correction uses synthetic development fixtures and mocked/injected providers.
The original manual run is an immutable negative regression fixture, not a source
of reconstructed model wording or a retroactively published report.
Disposable PostgreSQL tests exercise publication, rollback, checkpoint/lease
recovery, owner isolation, ordered SSE and one terminal event/report. The recovery
fixture expires a test lease deterministically; it is not a new real OS-process
crash acceptance run. Existing process acceptance code is unchanged.

Frontend Chromium tests run with intercepted fixture APIs on isolated port 5273,
without stopping the manually running frontend. There are no frontend API/schema,
agent prompt, generic comparator, evidence-sufficiency routing or database migration changes.

Old paid manifests remain invalid after code changes. Offline transport unit tests
now use temporary code identities rather than rewriting the historical RC identity;
a negative test verifies altered identity blocks preparation before provider setup.
No paid dispatch or new approval manifest is prepared. The historical evaluation
ledger remains $0.0550552 / $2; manual UI costs are not reconciled into it.

New code takes effect in the manual application only after the operator deliberately
restarts its processes. No live process was restarted and no investigation was
resubmitted during this correction. Another paid/manual test requires the user's
separate decision; ordinary UI execution still lacks the evaluation runner's
hard call/reservation limits and provider usage audit.
