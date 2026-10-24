Submission-hardening readiness — 2026-10-24
==========================================

Historical baseline
-------------------

This document records the preceding submission-hardening milestone. The later
publication-security-licensing.rst supersedes its npm advisory and lock status;
its original verification records remain unchanged.

Decision
--------

Application regression and portable-export verification: PASS.
Unqualified public GitHub publication: NO-GO pending the dependency advisory,
license/notice decisions, and actual Git repository/index/history review below.
No application logic, agent prompt, routing, or production configuration changed.
No commit, push, deployment, paid provider call, or new paid manifest occurred.

Verification evidence
---------------------

* Complete local backend suite, including authentic private-history tests:
  **1013 passed**, 110.28 seconds. This includes all 110 integration-marked tests.
* Sanitized public export with a clean Python environment and real disposable
  PostgreSQL: **960 passed, 53 skipped**, 78.35 seconds. All 53 skips are explicitly
  marked authentic private-archive checks; none is counted as a passed history
  assertion. Portable SDK/dispatch, incomplete-review denial, numeric usage,
  grounding, output-safety, and synthetic reconciliation tests still execute.
* Clean export frontend: **70 Vitest passed**, **13 Chromium passed**, production
  TypeScript/Vite build passed. Chromium uses fixture HTTP responses; no new
  full live UI-to-worker acceptance is claimed for this milestone.
* Standalone disposable database migrated to **0008**; Alembic schema check
  passed, separate LangGraph checkpoint tables initialized, database removed.
* Locked fresh Python installation: **88 packages**, all distribution hashes
  enforced by uv; uv pip check passed. Python 3.11/macOS ARM64 verified.
* Fresh npm ci from the preserved frontend lock passed. That file matches the
  earlier RC snapshot byte for byte. The frontend application source is unchanged.
* Two local database schema/table-count fingerprints remained unchanged across
  the final verification window. Those checks used enforced read-only sessions;
  they are not a byte-for-byte backup or proof of unchanged row contents.
* No disposable phase8_*_test databases remained after verification.
* **260 protected historical files** matched the prior immutable baseline.
  Frozen benchmark data, recorded outputs, human reviews, and audit records were
  not rewritten. The accounting chain verifies **0.0550552 USD / 2 USD**.

Fresh-checkout feasibility
-------------------------

The directory has no Git metadata, so an actual git clone was not possible.
Instead, an explicit public-file inventory was copied to a new temporary
location without .env, reports, virtual environments, node_modules, caches,
builds, or browser artifacts. A separate Python environment installed the lock;
npm ci installed the frontend lock. Tests ran there against disposable databases,
not the primary/manual databases. Test settings were passed privately from the
original local configuration and were not copied into the export.

A PostgreSQL server, local CREATE DATABASE-capable test role, Node, browser
installation, and Python/uv are prerequisites, not bundled services. Integration
tests skip without TEST_DATABASE_URL. No Linux/Windows installation, offline
package-download guarantee, or configured production provider is claimed.

Private audit tests
-------------------

Real human reviews and append-only accounting proofs must not be fabricated or
exported just to satisfy CI. They are opt-in with --private-audit. The public
suite uses extracted sanitized state/output fixtures and isolated synthetic
preparation/ledger contexts. Only pytest patches those contexts; real production
preparation, approval, accounting, budget locks, and dispatch code are unchanged.
An unmodified public runner still fails closed when its private archive or fresh
explicit approval is missing.

Output snapshots are known regressions, not new independent holdouts. Synthetic
complete-review flags are test controls, not evidence that a human approved a
paid operation. Original audits remain private and unchanged.

Publication findings
--------------------

* The bounded public-file scanner found no secret candidates, home-directory
  paths, binary artifacts needing review, or files over 10 MiB in the candidate
  set. The largest candidate is the hash lock, about 214 KB. This is not proof
  against every secret format. Neither gitleaks nor trufflehog was installed.
* No .git metadata exists here: tracked files, staged content, removed files,
  branches, remotes, and commit history cannot be audited. Perform the full
  maintained-scanner/history/index review in the actual Git repository.
* Private .env exists and remains excluded. Reports, local tooling, credentials,
  keys, database exports, browser traces, installed dependencies, and builds must
  stay outside the publication set. Ignore rules do not erase tracked history.
* The historical adapter/tests include an existing pseudonymous reviewer ID.
  Owner privacy review is still needed; it was not removed by weakening bound
  historical authorization checks or rewriting reviews.
* No project license was added. MIT is recommended subject to owner approval.
  Resolve shadcn-derived source notice provenance and retain required third-party
  copyright, permission, LICENSE, and NOTICE material. The inventory covers 403
  dependency entries; tiktoken's missing metadata was resolved by inspecting its
  installed MIT license. See third-party-licenses.rst.
* npm audit reports **two critical and one moderate affected-package entries**:
  Vitest 3.2.7, Tinypool 1.1.1, and @vitest/mocker 3.2.7. These are development
  dependencies, with transitive counts; no production exploit was demonstrated.
  The suggested fix is a major Vitest upgrade. No automatic package update or
  npm audit fix was applied. See publication-checklist.rst for advisory links.
* Python distribution vulnerability advisories were not audited in this
  milestone. Dependency compatibility/hash verification is not vulnerability
  clearance. A maintained advisory scan remains appropriate before release.

Historical Phase 14.6 evidence and limitations
---------------------------------------------

The consolidated Phase 14.6 records remain the source of real local HTTP/browser,
worker-process crash, lease-reassignment, revocation, signed-ingestion, timeline,
SSE, and persistence acceptance evidence. They use deterministic LLM responses,
synthetic trusted identities and signing keys, and disposable PostgreSQL.
Administration and worker recovery were covered by complementary acceptance
flows, not a claimed single browser-only production flow. This milestone reran
regressions and fixture Chromium; it did not repeat every historical crash flow.

Production IdP membership/revocation, independent ownership/security review,
producer key management, least-privilege database roles, TLS/egress isolation,
monitoring, retention, backups, and operational runbooks remain dependencies.
The contract flag stays off by default and alone grants no authority. No current
production endpoint or contract was activated.

Manual OpenAI UI execution is paid and has no integrated aggregate evaluation
budget cap. Model-invocation metadata is not implied by wrapper-completed SSE
receipts. NOT_TESTED remains truthful. Ordinary uploaded evidence does not gain
signed provenance. Conservative holds, imperfect evidence selection, bounded
semantic rules, and mechanism-versus-origin reasoning remain quality limits.
Output safety uses conservative rules and can reject harmless wording; it is not
a proof against every paraphrase. Broad secret-key redaction can also redact safe
metadata. No diagnostic accuracy claim follows from lexical metrics or the small
single-reviewer exploratory paid pilots.

Exact public-file changes
-------------------------

Updated:

* README.md
* .env.example — public example port 55432; private .env unchanged
* .gitignore — private local tooling, key/export, and dump patterns
* pytest.ini — explicit private_audit marker
* frontend/README.md
* frontend/playwright.config.ts — selectable isolated port, strict server port
* tests/test_tentative_reports.py
* tests/test_output_safety.py
* tests/test_enhanced_pilot.py
* tests/test_json_redaction.py
* tests/test_live_handle_contract.py
* tests/test_minimal_pilot.py
* tests/phase14_6_acceptance.py
* tests/phase14_6_1_acceptance.py
* tests/phase14_6_2_acceptance.py
* tests/phase14_6_2a_acceptance.py
* tests/phase14_6_3_acceptance.py
* tests/rc_live_acceptance.py

Added:

* requirements.lock
* scripts/publication_audit.py
* tests/conftest.py
* tests/publication_fixtures.py
* tests/runtime_tools.py
* tests/fixtures/publication/README.rst
* tests/fixtures/publication/authorization-classifier.json
* tests/fixtures/publication/enhanced-manifest-template.json
* tests/fixtures/publication/investigator-selection.json
* tests/fixtures/publication/output-safety-state.json
* tests/fixtures/publication/paired-manifest-template.json
* tests/fixtures/publication/tentative-report-state.json
* tests/fixtures/publication/v2-plan.json
* tests/fixtures/publication/root-schema-failure-calls.json
* docs/dependency-license-inventory.json
* docs/third-party-licenses.rst
* docs/publication-checklist.rst
* docs/submission-readiness.rst

Local evidence only
-------------------

New logs, inventory, schema, integrity, and advisory metadata live under ignored
reports/submission-hardening/. They are not public source. No historical reports
were overwritten. frontend/package-lock.json, requirements.txt, compose.yaml,
all application modules/migrations, and frozen benchmark/review/audit files
remain unchanged. No license grant, commit, or GitHub publication was performed.
