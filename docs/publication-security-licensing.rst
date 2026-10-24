Publication security and licensing readiness — 2026-10-24
========================================================

Historical milestone
--------------------

The later licensing-owner-confirmation.rst supersedes this milestone's proposed
owner identity and pending project-license status. Its private drafts and audit
results remain unchanged. Current project copyright is Ravi Konduru, not the
earlier shortened proposal.

Decision
--------

Targeted npm correction and regression verification: PASS.
Public publication: NO-GO until project-license approval, notice/source follow-up,
and actual Git index/history auditing are complete. No project license was
applied. No application logic, agent prompt, routing, architecture, evaluation
result, frozen data, historical review, or append-only audit was changed.

Dependency findings and bounded correction
------------------------------------------

Before the change, the installed development graph contained Vitest 3.2.7,
@vitest/mocker 3.2.7, and Tinypool 1.1.1. npm audit reported two critical and one
moderate affected-package entries, including transitive effects; those counts
are not independent exploit proofs.

The applicable mechanisms and patch boundaries are:

* Tinypool: affected ranges include <=2.1.0 and <2.1.2 for two prototype-pollution
  gadgets. Version 2.1.2 addresses both. Exploitation requires relevant polluted
  properties and unsafe worker-option use. This project did not expose a pool
  API accepting incident data as worker options.
* Mocker redirect handling: >=2.1.0 and <4.1.11 is affected; 4.1.11 supplies that
  fix. The separate manual-registration issue affects >=2.1.0-beta.7 and
  <4.1.12 on the relevant release line; use 4.1.12 to address both.
* The inspected registry has no patched Vitest 3 release. Overriding its internal
  mocker to a different major or forcing Tinypool 2 under its old runner would
  introduce unsupported coupling.

Vitest is therefore pinned to **4.1.12**, the smallest supported patched release
line for both mocker issues. Its own @vitest/mocker is 4.1.12, and it no longer
requires Tinypool. Tinypool is absent from the resolved lock. No Vitest 5 upgrade,
forced npm audit fix, or transitive override was used. The existing Vite 7.3.7
satisfies its declared peer range and was not upgraded.

Node metadata now explicitly supports ^22.12.0 or >=24.0.0, matching the
intersection of the application/toolchain requirements. Tests ran on Node
24.22.1, Python 3.11, macOS ARM64. Node 22 compatibility was checked from package
metadata, not by running an additional Node 22 environment.

Only development dependency versions changed: 13 existing version entries,
10 removed entries, and two added entries in the lock. Runtime dependency entries
remain unchanged. The full/production-only npm audits both report **zero known
findings** after clean npm ci. A clean advisory result is not proof against
unknown vulnerabilities or a substitute for later rechecks.

Exposure assessment
-------------------

These dependencies belong to development/test execution, not the deployed
React bundle or FastAPI/LangGraph runtime. Tests use jsdom; application config
does not install the browser mocker/interceptor plugins implicated in the
WebSocket issues. Playwright runs against the ordinary loopback Vite server.
No Vitest API or UI server is configured. No exploitation was performed or
observed. Exposing development tooling or adding browser-mocker integrations
would change the exposure analysis; development status does not make the
original findings harmless.

Primary advisory references:

* https://github.com/advisories/GHSA-5gmw-xhrv-c9v3
* https://github.com/advisories/GHSA-85c8-ppgw-ccpr
* https://github.com/advisories/GHSA-82fw-gwwq-j7x9
* https://github.com/advisories/GHSA-qxg6-89mg-rqv8

Verification
------------

* Full backend with authentic private archives: **1013 passed**, including the
  110 PostgreSQL-marked tests. Test databases are unique and disposable.
* Clean public export backend: **960 passed, 53 explicit private-audit skips**.
  No .env, private reports, node_modules, or existing virtual environment was
  copied. All 53 skips are authentic archive checks, not passed assertions.
* Final lock/Node-metadata binding regressions: **29 passed**.
* Fresh npm ci, **70 Vitest passed**, **13 Chromium fixture tests passed**,
  TypeScript tsc -b, and production build: PASS.
* Separate clean Python hash-lock installation and uv pip check: **88 packages**,
  PASS. Python dependencies were not changed. Python vulnerability advisories
  were not independently queried in this narrowly scoped npm milestone.
* Separate disposable migration to **0008**, Alembic schema consistency, and
  checkpoint setup: PASS. No primary migrations were applied. No disposable
  phase8_*_test databases remained.
* Protected application source, private configuration, requirements lock, and
  frozen data matched the pre-change snapshot. All **260 historical protected
  artifacts** matched the prior immutable baseline. The original accounting
  chain remains **0.0550552 USD estimated against 2 USD**.
* Enforced read-only primary/manual schema-count fingerprints matched the prior
  submission baseline. This is not a byte-level backup or a row-content proof.

Chromium here uses fixture HTTP responses. This milestone does not claim a new
paid model evaluation, independent diagnostic accuracy, or a repeated complete
live worker-crash acceptance run. Existing consolidated acceptance artifacts
remain unchanged and separate.

Public-file audit
-----------------

The complete explicit candidate inventory excludes private .env files, local
tooling, signing-key/export file types, database dumps, all private reports,
installed packages, caches, browser artifacts, and builds. Public .env.example
files contain configuration examples only. No home-directory paths, oversized
files (>10 MiB), unexplained personal email addresses, or unresolved secret
candidates were found in the candidate set. All matched email domains were
reserved synthetic/example domains. Database URL examples were reviewed without
printing their values. The existing reviewer pseudonym remains unchanged with
user confirmation; the proposed copyright name Ravi K was supplied by the owner.

Gitleaks **8.30.1**, downloaded from its official release and checked against its
published SHA-256, ran locally on the sanitized export. Its default scan found
one generic-api-key false positive: a synthetic outage branch assigning None
to integration.ingestion.keys. No credential value exists there.

.gitleaks.toml inherits all default rules and adds a rule-specific AND exception
for that exact safe assignment line in that exact test-file suffix. A generated,
non-provider key on the next line of the same file is still detected. An initial
global-allowlist experiment failed this negative control and was replaced; no
whole-file or test-directory exception remains. The final configured scan has
zero findings; the raw finding and triage are preserved privately.

There is **no .git metadata**. Tracked files, index contents, deleted historical
files, branches, and prior commits cannot be audited. No history scan is claimed.
No Git repository was initialized, and no commit or push occurred. Before an
actual push, inspect the real staged/index file set and scan all refs with the
maintained scanner. Ignore rules do not erase already committed secrets.

Licensing and owner review
--------------------------

MIT remains the recommendation. The exact proposed text is prepared privately
as DRAFT-PROJECT-MIT.txt, naming **Ravi K** with **2026 proposed for confirmation**.
That is not a license grant. No root LICENSE or public notice bundle was added.

DRAFT-THIRD-PARTY-NOTICES.txt contains **357 exact notice sections** with source
references and SHA-256 values. Six missing distribution notices were recovered
from exact npm gitHead commits or an explicit upstream release tag. The index
separates installed texts, upstream recovery, and unresolved gaps. The public
metadata inventory covers **395 entries** (88 Python and 307 npm lock entries,
including optional/development entries), not 395 fully cleared distributions.

Three notice/source gaps remain:

* **react-remove-scroll-bar 2.3.8** — runtime; MIT metadata, no located packaged
  notice, and the reported upstream commit could not be retrieved.
* **stackback 0.0.2** — development; MIT metadata, no located notice; upstream
  default tree inspection does not establish version-specific notice provenance.
* **uri-js-replace 1.0.1** — development; MIT metadata, no located notice in the
  inspected reported commit.

Obtain the appropriate publisher notices or authoritative clarification rather
than inventing copyright/permission text. Retain the full upstream shadcn MIT
notice for substantial source adaptations and review provenance of the adapted
components. Preserve all applicable MIT/BSD/ISC copyright/permission notices,
Apache LICENSE/NOTICE files, and native/bundled notices. Psycopg, psycopg-binary,
and psycopg-pool declare LGPL-3.0-only; redistributing their binaries requires
review of their actual terms. A project MIT grant would not replace third-party
obligations. Optional platforms and native binary subcomponents need their own
applicable distribution review; the prepared bundle is not legal clearance.

Drafts and audit evidence are in ignored reports/publication-security-licensing/.
They are deliberately absent from the public export until the owner completes
review and separately authorizes applying the license/notices.

Exact public-file changes
-------------------------

* frontend/package.json — Vitest pin and supported Node range only
* frontend/package-lock.json — corresponding development graph and root metadata
* .gitleaks.toml — inherited defaults and narrowly tested false-positive rule
* README.md — supported Node range and current readiness reference
* frontend/README.md — supported Node range
* docs/dependency-license-inventory.json — refreshed current dependency metadata
* docs/third-party-licenses.rst — draft status, inventory, unresolved notices
* docs/publication-checklist.rst — resolved prior finding and current checklist
* docs/submission-readiness.rst — marks earlier dependency status as historical
* docs/publication-security-licensing.rst — this report

No tests or assertions, application source, migrations, agent prompts, diagnostic
routing, evaluation records, frozen data, or historical audits were changed.
Local logs/drafts and downloaded tools are private artifacts, not publication
inputs. No paid calls, deployment, production activation, or automatic publication
occurred. Stop here pending owner decisions and the actual Git history audit.
