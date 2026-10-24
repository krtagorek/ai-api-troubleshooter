Approved MIT owner identity and provenance follow-up — 2026-10-24
================================================================

Project license
---------------

The owner explicitly approved MIT and supplied the exact professional name.
The root LICENSE now contains::

    Copyright (c) 2026 Ravi Konduru

Only the project-owned copyright identity changed from the earlier proposal.
The standard MIT permission/disclaimer text is unchanged. Earlier private
proposals naming Ravi K remain archived, not applied or represented as current.
No frozen benchmark, human review, evaluation output, or audit was rewritten.

THIRD_PARTY_NOTICES.txt preserves the shadcn source-adaptation MIT notice,
the embedded V8 component notice, and the recovered scroll-bar MIT notice verbatim. Their copyright holders, dates,
attribution, conditions, and disclaimers remain unchanged. Project MIT does not
replace third-party rights. This is source-publication preparation, not approval
to distribute every native dependency or a production build without its notices.

Provenance progress
-------------------

react-remove-scroll-bar 2.3.8 (runtime)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The publisher-linked repository has an MIT notice. It was retrieved at pinned
commit 8ca9ba5ea52de03308fe8ced94f7b159a44d28ff without changing its author's
copyright or email. Its package.json identifies version 2.3.7, however. There
is no 2.3.8 tag in the inspected tag list, and the reported npm gitHead returns
HTTP 422. Further read-only comparison verified the 2.3.7 tarball's SHA-512
integrity and found all 24 dist/ files identical to the installed 2.3.8
dist/ files. The 2.3.7 npm gitHead matches its version tag, and all five source files
match the pinned MIT-licensed commit. The exact publisher notice is therefore
preserved in THIRD_PARTY_NOTICES.txt with this comparison disclosed. Its author's
public email is retained as required attribution, not used as the project owner's
commit email. The unavailable 2.3.8 release metadata remains a limitation, and
this does not claim signed-build provenance or recovery of the missing commit.

stackback 0.0.2 (development)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The actual v0.0.2 release tag resolves to commit
2963095372abf7b75ba55f01cef08ab1e62c2ff4. Its formatstack.js is byte-for-byte
identical to the installed file. The embedded header explicitly identifies the
V8 project authors and BSD-style redistribution conditions. That exact header
is now retained in THIRD_PARTY_NOTICES.txt. The package's MIT metadata alone is
incomplete as a description of this embedded component. Its package-level MIT
notice remains unlocated; no copyright owner or grant text was invented.

uri-js-replace 1.0.1 (development)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The README was recovered at reported commit
38927505b51a74b53444dc723be10e0d2e53823c. It describes a replacement implementation
and mentions copied tests. Neither that statement nor an MIT metadata field
supplies the missing full package notice or establishes all copied-code rights.
No new conclusion about code ownership is inferred. Obtain publisher clarification.

Remaining action
~~~~~~~~~~~~~~~~

Resolve the release-binding/package-notice items above before declaring complete
third-party clearance. The previously prepared 357-section review bundle remains
unchanged. Newly recovered notices and release-file comparisons are separate
private evidence, not modifications to that historical draft. No package update,
runtime replacement, upstream issue, or message to a maintainer was performed.

Future GitHub commit privacy
----------------------------

No Git metadata or authenticated GitHub email address is available here. No Git
configuration was changed and no email address was invented. The professional
copyright name does not determine a GitHub username or noreply address.

In your own GitHub account, open https://github.com/settings/emails, enable
Keep my email addresses private, and copy the exact GitHub-provided noreply
address shown there. Do not derive an address from your name or guess an account
ID. GitHub documents the procedure at
https://docs.github.com/en/account-and-profile/how-tos/email-preferences/setting-your-commit-email-address.

After a real local Git repository exists under separate authorization, these
optional commands configure only that repository. They do not initialize Git,
create commits, contact a remote, push, or publish::

    printf 'Paste your exact GitHub-provided noreply address: '
    IFS= read -r GITHUB_NOREPLY
    case "$GITHUB_NOREPLY" in
      *@users.noreply.github.com)
        git config --local user.name 'Ravi Konduru'
        git config --local user.email "$GITHUB_NOREPLY"
        ;;
      *) printf '%s\n' 'Not configured: copy the exact GitHub noreply address first.' ;;
    esac
    unset GITHUB_NOREPLY

Changing commit configuration affects future commits, not existing history.
Audit the actual index and every history ref before publication. Historical Git
privacy cannot be verified in this directory, and no history rewrite is authorized.

Verification scope and publication decision
-------------------------------------------

This follow-up changes licensing/attribution/documentation only. Application
source, prompts, routing, dependencies, private configuration, frozen datasets,
reviews, and audits are protected by before/after hashes. No paid calls or database
migrations are involved. The preceding application regressions (1013 backend,
960 portable backend plus 53 archive skips, 70 Vitest, 13 fixture Chromium,
TypeScript/build and disposable PostgreSQL 0008) are retained evidence, not
claimed as newly rerun for a license-text change.

Current focused checks cover the exact MIT text, verbatim third-party notices,
application/history integrity, budget reconciliation, explicit public-file
inventory, maintained secret scan, and read-only npm advisory query. Private
configuration, generated reports, historical drafts, and installed packages remain
excluded from the sanitized export. See the local results under
reports/licensing-owner-confirmation/ for actual outcomes.

MIT approval is complete. Public publication remains NO-GO pending the outstanding
third-party provenance items and audit of the actual Git index/history. No Git
initialization, commit, push, publication, production activation, or paid dispatch
is authorized or performed by this milestone.
