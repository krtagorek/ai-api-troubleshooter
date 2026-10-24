Phase 14.6.1: safe tentative report finalization
==============================================

Scope and reproduced failure
----------------------------

The preserved Phase 14.6 run-4 state had a grounded tentative diagnosis,
a diagnostic-only human-approved plan, and truthful NOT_TESTED validation.
The validation router sent NOT_TESTED to request_user_input, final report
construction handled only validated or exhausted-failure paths, and the
trusted-semantic final-node gate required a validation tool. No final_report
meant no application ReportRow; the report API returned 409 and React had
nothing persisted to render. The historical run remains unchanged.

Narrow correction
-----------------

The optional trusted path can finalize an unvalidated report only with a
qualified semantic result, tentative diagnosis, explicit uncertainty,
canonical grounded evidence, a supported selected hypothesis, discriminating
references, and a diagnostic-only plan requiring human approval. NOT_TESTED
remains NOT_TESTED. Remediation, failed/inconclusive validation, ambiguous
or fabricated evidence, withheld unsafe output, and missing support do not
qualify. Feature-off legacy routing remains unchanged. No tools or fixes are
executed by this correction.

Publication authorization
-------------------------

The final node still reacquires current semantic authorization. Separately,
publication acquires a short-lived diagnostic_report_publication_v1 permit
bound to the exact serialized state digest. The application transaction
consumes it with fresh contract, tenant, endpoint, membership, producer,
key, provenance, and revision checks. Authorization locks remain held until
the report, investigation, terminal event, and fenced job update commit.
A concurrent revocation either commits first and denies publication or waits
for the authorized report transaction. This does not physically interrupt
an operation that linearized before revocation.

Missing tickets, altered states, unavailable authorization, and committed
revocation remove protected findings from the application projection and
retain an evidence hold. Database failures roll back publication. Checkpoint
metadata cannot authorize publication: recovery must reacquire authorization.
Bearer permits are ephemeral and are not serialized into checkpoints.
Existing historical reports/checkpoints are not rewritten. Report insertion
uses conflict-do-nothing rather than overwriting an existing report.

Verification and reproducibility
-------------------------------

The disposable real-HTTP/Chromium harness is::

    .venv-py311/bin/python -m tests.phase14_6_1_acceptance

It creates and removes its own PostgreSQL database, migrates it through 0008,
starts local API/mock/Vite services, injects deterministic structured LLM
responses and synthetic identities/keys, and runs the actual durable worker.
It never calls OpenAI. Results are in reports/phase14_6_1-acceptance/run-1.
The report view distinguishes completion from validation and explicitly shows
uncertainty, diagnostic checks, evidence, human approval, and NOT_TESTED.

Additional PostgreSQL tests cover publication/revocation races, missing
permits, provider outages, altered publication state, transaction rollback,
checkpoint-boundary revocation, ordered replay, and one-report idempotency.
Unit tests reject unsafe, unsupported, incomplete, and non-diagnostic outputs.

Remaining boundaries
--------------------

This is isolated acceptance, not production activation or an independent
quality benchmark. Contract lifecycle and signed ingestion control interfaces
are exercised as Python services; public HTTP/React administration is absent.
Real OS worker termination/lease reassignment was not added to the live run;
regressions cover recovery and lease fencing. Process isolation uses clean
configuration and application/socket/browser guards, not an OS sandbox.
Production identity, ownership, review, key/revocation providers, least-privilege
roles, operational clocks, and audit retention still require configuration
and separate acceptance. No primary migration, deployment, paid dispatch,
or Phase 14.7 work is authorized here.
