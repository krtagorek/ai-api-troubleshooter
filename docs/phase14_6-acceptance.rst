Phase 14.6: isolated end-to-end acceptance
=========================================

Decision: NO-GO
---------------
The end-to-end report requirement fails without any security-control bypass.
A real Chromium user registered/verified/approved the loopback mock and created
and submitted an incident. The actual five agent implementations ran using
injected deterministic structured-output transports. Trusted semantics committed
four permit uses and produced a tentative diagnosis and human-approved diagnostic
plan. Validation truthfully remained NOT_TESTED. The worker stopped at
awaiting_evidence; final_report remained null. app.reports contained zero rows,
GET /report returned 409, and React displayed "More context is needed" instead of
the persisted tentative diagnosis, plan, or validation result. Refresh reproduced
this behavior. This is a functional acceptance failure, not a reason to fabricate
passed validation or a report. No application correction was made.

The implementation currently creates final reports only on validation success or
validation-retry exhaustion. InvestigationRepository persists ReportRow only when
state.final_report exists. The NOT_TESTED path routes through request_user_input
and never creates it. A separately authorized correction must make safe partial
results available while preserving awaiting_evidence and NOT_TESTED, followed by
a fresh end-to-end acceptance run. Do not manufacture success to reach final_report.

Evidence and execution modes
-----------------------------
Canonical live attempt: reports/phase14_6-acceptance/run-4/.
backend-evidence.json contains real HTTP status codes, persisted diagnostic state,
signed verification result, permit references/digests, public lifecycle/authority
audit events, checkpoint hashes and database counts. browser-evidence.json and PNGs
capture real Chromium rendering, responses, SSE requests, refresh and history.
No HTTP responses were mocked in that browser flow. runtime.log contains controlled
HTTP metadata. Raw bearer headers, signing secrets and one-time ownership challenges
were not captured. Scripted provider outputs are labeled real_provider=false and
token_usage=null. No paid model was initialized; these are not model-quality scores.

FastAPI and Worker ran as separate components in one isolated controller event
loop, with real loopback HTTP, a real approved mock service, durable PostgreSQL,
and separate Vite/Chromium child processes. They were not separate OS worker
processes. Restart and lease-reassignment coverage comes from disposable PostgreSQL
regression tests with new Worker/runtime/pool instances, not a live browser plus
OS-kill demonstration. Those evidence levels must not be conflated.

Contract lifecycle, tenant binding, signing and ingestion have Python service
interfaces only in Phases14.1-14.5. They were exercised directly against the same
real disposable database. No test-only routes were invented. Public HTTP/React
control-plane acceptance for these operations is BLOCKED by absent interfaces;
only the existing endpoint registry and incident/investigation APIs were used over
HTTP. Independent owner/reviewer/admin identities and proofs are synthetic mocks,
not real production ownership verification or human adjudication.

Isolation and limitations
-------------------------
The disposable fixture created a unique _test database and migrated through 0008;
Alembic schema comparison passed. App tables and langgraph checkpoint tables were
separate. The fixture removed each database and stopped all started services.
No primary database migration or production endpoint execution occurred. Normal
application .env was not modified; DIAGNOSTIC_CONTRACTS_ENABLED remains false by
default. The controller reads only TEST_DATABASE_URL from private configuration,
then removes inherited credentials/provider variables. Child environments contain
only basic OS variables and synthetic browser authentication. Explicit server DI
enables semantics only for the synthetic worker.

The controller's socket guard blocks non-loopback or unapproved-port connections;
a reserved documentation-IP probe was rejected before connection. Registry DNS
resolution permits only the configured mock. Chromium routing blocks external
origins. These are application-level controls, not an OS/network namespace or
hostile-process sandbox. Browser external routing was configured; no production
origin was contacted to test it. The PostgreSQL administration role can create
and remove disposable databases; it is not a production least-privilege role.
A production-grade containment claim requires separate OS/network isolation and
real deployment credential/key/identity infrastructure.

Acceptance matrix
------------------
See results.json for the machine-readable case-by-case matrix and exact test
references. Evidence modes are LIVE_HTTP_BROWSER, LIVE_SERVICE, POSTGRES_REGRESSION
or OFFLINE_REGRESSION. A regression PASS is not a live HTTP/browser PASS.

* PASS: real synthetic login, UI registry verification/approval, approved local
  mock execution, browser incident creation and actual durable worker execution.
* PASS: actual five agent implementations with mocked strict structured output,
  grounded fencing comparison, tentative-only diagnosis, human approval, NOT_TESTED.
* FAIL: persisted public diagnostic report and UI rendering of diagnosis/plan;
  zero ReportRow records and HTTP409 despite persisted investigation results.
* PASS: ten ordered SSE events, exact authenticated Last-Event-ID replay,
  one job attempt, no duplicate worker execution, history and browser refresh.
* PASS: real HTTP401, unauthorized approval403, cross-owner incident/SSE404,
  database-outage health/incidents503 and revoked endpoint execution blocked.
* PASS: direct real-database invalid-signature, cross-tenant contract, owner
  self-approval, snapshot mismatch, missing-key-provider, expired/replayed permit,
  and revoked-contract no-analyst checks.
* PASS in regression: flag-off parity, missing ownership/contract authority,
  revocation races, restart/checkpoint recovery, lease reassignment, key rotation
  and revocation, cross-endpoint isolation, stale/conflicting domains/correlations,
  connection termination, output safety and endpoint approval enforcement.
* BLOCKED: public HTTP/React diagnostic-contract registration, approval, activation
  and signed-ingestion controls; those public interfaces do not exist.
* BLOCKED for live-browser evidence: OS-process worker crash/restart and lease
  reassignment. Real PostgreSQL service-instance regression coverage is available.

No listed security stop condition was observed. The secret scan found no JWT,
private-key PEM, credential-bearing PostgreSQL URL or OpenAI key in captured text
artifacts. Live HTTP responses/checkpoint blobs were also checked against exact
synthetic bearer values. Screenshots were reviewed. This does not prove arbitrary
unlabeled secrets can never leak; operational redaction/egress controls remain.

Regression results and preservation
------------------------------------
891 backend tests passed, including 78 disposable PostgreSQL integration tests.
43 Vitest tests passed. All 9 fixture-based Chromium tests passed, independently of
the real-HTTP live browser flow. The TypeScript/Vite production build passed.
The live disposable database passed migration/schema consistency checks at0008.
No routing, agent, UI, API, security or persistence application code was changed
in Phase14.6; only manual acceptance harnesses and evidence/documentation were added.
Frozen benchmark/review/audit hashes and the previous phase report are preserved.
Historical accounting remains $0.0550552 against $2, with zero unresolved calls.

Earlier attempts are preserved. The initial environment setup lacked sandbox
socket permission. The first browser run succeeded but the controller collected
an ORM field using the wrong harness name (state vs diagnostic_state). A subsequent
transport-upgrade attempt exposed a harness race starting the worker before HTTP202
commit; the harness now awaits the real receipt. These are harness corrections,
not application/security fixes. The canonical attempt runs the actual agents,
strict schemas and short-handle resolver, with worker retries disabled.

Reproduction (separate authorization/environment still required)
----------------------------------------------------------------
From the repository, with PostgreSQL localhost55432 and free8000/8001/5173 ports::

    PHASE14_6_ATTEMPT=unique-new-attempt uv run python -m tests.phase14_6_acceptance

Use the existing Python3.11 environment and installed local Chromium/Node runtime.
The harness refuses to overwrite run markers. It does not approve a paid plan or
activate any contract outside its disposable synthetic database.
