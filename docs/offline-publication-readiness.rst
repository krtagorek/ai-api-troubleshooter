Offline source-publication decision — 2026-10-24
===============================================

Scope and decision
------------------

Conditional GO for a clean initial SOURCE-ONLY repository prepared from the exact
reviewed inventory. This is not authorization to initialize Git, commit, push,
or publish. It is not complete third-party redistribution clearance for bundled
applications, node_modules, wheels, containers, or development-tool archives.
This scope distinction supersedes the broader publication hold in the earlier
licensing follow-up; the earlier evidence and reports remain unchanged.

Package provenance: verified facts and limits
--------------------------------------------

stackback 0.0.2 is development-only through Vitest -> why-is-node-running.
The cached publisher release tree at v0.0.2 / commit
2963095372abf7b75ba55f01cef08ab1e62c2ff4 contains README.md, index.js,
formatstack.js, test.js, package.json, and .travis.yml, without a package LICENSE.
The released formatstack.js matches the installed file and contains the actual
2012 V8 project authors' BSD-style notice. That notice remains verbatim in
THIRD_PARTY_NOTICES.txt. Its conditions apply to that component, not automatically
to the other package files. The MIT package metadata is a publisher declaration,
not recovered full permission/copyright source text. No package-wide copyright
holder has been inferred from an author field.

uri-js-replace 1.0.1 is development-only through openapi-typescript ->
@redocly/openapi-core -> @redocly/ajv. The previously retrieved upstream tree and
README at reported commit 38927505b51a74b53444dc723be10e0d2e53823c did not
supply a full package notice. MIT metadata alone does not fill that gap. The
README says tests were copied from URI.js; this neither proves runtime copying
nor supplies the rights for copied tests. Do not assign another project's license
or invent a copyright line for this implementation.

These conclusions use locally cached source evidence, not a new upstream search.
No maintainer was contacted. Obtain a release-bound full notice/permission from
the publisher before redistributing either package itself. Preserve all existing
notices meanwhile. All three recovered notices, including the scroll-bar notice,
remain unchanged. The scroll-bar missing 2.3.8 release-metadata limitation remains
as documented in licensing-owner-confirmation.rst.

Redistribution boundary
-----------------------

The reviewed source export excludes node_modules, compiled frontend assets,
private reports, environments, database files, and dependency archives. A lockfile
reference to an externally installed package does not include its implementation.
No copied stackback or uri-js-replace implementation was identified in the
candidate source review. This is bounded inspection, not a universal authorship
proof. Therefore those two notice gaps are documented dependency limitations,
not identified blockers for this source-only export. They remain unresolved
redistribution risks for packages or bundles containing their code. Do not label
the entire dependency graph fully attribution-cleared.

Git history and initial-repository plan
--------------------------------------

No Git repository, index, object pack, bundle, or recoverable ref was found by
workspace and ancestor probes. Installed dependencies/caches were excluded from
recovery discovery. No Git environment overrides were present. External backups,
other folders, and remote accounts were not searched. A historical Git scan was
not possible and is not reported as passed.

Future actions, requiring separate authorization:

1. Review the exact file paths, sizes, and SHA-256 values in
   reports/offline-publication-readiness/public-files.json. Export only those
   files into a new empty directory; never copy this workspace wholesale.
2. Verify the export hashes and exclusions again. Initialize Git only in that
   reviewed source export after explicit authorization. Do not manufacture old
   commits or reconstruct development history.
3. Copy the actual private noreply address from GitHub Settings -> Emails.
   Configure repository-local commit identity as Ravi Konduru with that exact
   address. No address is inferred from a name or username.
4. Stage only the inventory. Inspect the actual index and scan staged contents
   with the maintained secret scanner. Confirm no extra ignored/private files
   were force-added. Review the owner-approved MIT and third-party notices.
5. Create a truthful initial source commit only when authorized. Scan all refs
   and the resulting commit before a separately authorized remote/push/publication.

Verification limits
-------------------

The exact inventory and sanitized local evidence are stored privately under
reports/offline-publication-readiness/. They are excluded from the public export.
The offline npm audit result is cache/offline evidence, not refreshed advisory
coverage. Refresh advisories when network access is separately permitted.
Secret scanning is bounded: synthetic fixtures and the deliberately public
copyright identity, approved reviewer pseudonym, and upstream attribution are
not claims that all personal information has been removed.

No application behavior, dependencies, historical artifacts, database, or Git
configuration was changed. No paid call was made. Historical estimated evaluation
spend remains $0.0550552 against the original $2 budget. Prior application test
results are retained evidence, not newly executed regressions for this document.
