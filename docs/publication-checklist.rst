Public GitHub publication checklist
==================================

The working directory is not a Git repository. No commit, index, branch, remote,
or history audit is possible here. Do not interpret a clean working-file scan
as a clean repository-history result. No repository was initialized or published.

Before publishing
-----------------

1. The owner approved MIT with Copyright (c) 2026 Ravi Konduru; verify the root
   LICENSE and retain THIRD_PARTY_NOTICES.txt. Resolve remaining third-party
   provenance items in third-party-licenses.rst before claiming clearance.
2. Review the explicit candidate file inventory produced by::

       python scripts/publication_audit.py --output reports/submission-hardening/public-files.json

   Never upload this whole working directory. Exclude .env, virtual environments,
   node_modules, builds, browser traces/HAR, dumps, keys, private local tooling,
   generated test output, and all private reports except package placeholders.
3. Preserve the private evaluation archive separately, access-restricted. Do not
   copy raw provider outputs, reviewer worksheets, private bearer tokens, or
   hash-chained audit bundles into a public repository to make tests pass.
4. The existing pseudonymous reviewer ID was approved for retention; preserve
   its bound historical adapter/tests and private review records. Ravi Konduru
   is the approved copyright identity, not a replacement reviewer identity. Check
   the actual staged set for any additional unapproved personal information.
5. In the actual Git repository, inspect tracked files and the staged diff,
   including previously committed/deleted files. Use a maintained secret scanner
   over all refs and the candidate index before pushing. .gitignore does not
   untrack existing files or erase history. If a genuine secret is found, stop,
   revoke/rotate it, and coordinate remediation before publication.
6. Run the locked clean-environment tests described in README.md. Private-audit
   skips are expected in a public checkout; they do not attest to archive safety.
7. Verify no primary database URL, credentials, identifying browser data, database
   export, or local home path is staged. Synthetic negative security strings and
   fixture-only identities are intentional and clearly labeled.
8. Re-run dependency/security advisories and review direct/native dependencies.
   Pinning improves reproducibility, not vulnerability immunity. Preserve the
   frontend lock and all required dependency notices.
9. Review README claims against submission-readiness.rst: deterministic browser
   fixtures, local live acceptance, and limited paid exploratory evaluations are
   different evidence. Do not claim production configuration or benchmark accuracy.
10. Owner performs any commit, remote creation, push, or publication only after
    these checks. This milestone performs none of those operations.

Private historical budget remains 0.0550552 USD estimated against 2 USD; no paid
manifest or automatic dispatch was prepared during submission hardening.

Prior advisory finding (resolved by targeted follow-up)
------------------------------------------------------

The prior frontend lock installs Vitest 3.2.7, @vitest/mocker 3.2.7, and
Tinypool 1.1.1. npm audit reports two critical and one moderate affected-package
entries, including transitive effects; these are not three independent exploit
proofs. These packages are development dependencies. No production exploitation
was tested or observed, and passing tests do not dismiss the advisories.

* https://github.com/advisories/GHSA-5gmw-xhrv-c9v3
* https://github.com/advisories/GHSA-85c8-ppgw-ccpr
* https://github.com/advisories/GHSA-82fw-gwwq-j7x9
* https://github.com/advisories/GHSA-qxg6-89mg-rqv8

npm suggests a major Vitest upgrade. No npm audit fix, override, package update,
or frontend lock change was performed. Review the patched dependency graph and
rerun Vitest/Chromium/build in a separately approved dependency correction before
calling this an unqualified publication-ready candidate.

Targeted follow-up
-----------------

Vitest is now pinned to 4.1.12, mocker resolves to 4.1.12, and Tinypool is absent.
The current npm audit is clean. The preceding paragraph is retained as the
original finding, not the status of the updated lock. Review the current report
in publication-security-licensing.rst before publication. Project MIT approval is now recorded;
third-party provenance gaps and actual Git index/history review remain pending.

Gitleaks 8.30.1 can scan the explicitly sanitized publication export with the
repository .gitleaks.toml. The config inherits all default rules and suppresses
only the exact synthetic None-assignment outage line in one named test file.
A same-file synthetic-key negative control was detected. Do not run an export
by copying the complete working directory; use the audited candidate inventory.
The maintained scanner did not and could not verify absent Git history.

Approved owner identity and future commit privacy
------------------------------------------------

Use Ravi Konduru for project-owned copyright; never substitute it into upstream
third-party notices. See licensing-owner-confirmation.rst for the exact approved
license and how to obtain GitHub's private noreply address. No address was
invented or configured, and no Git repository was initialized.
