Licensing and attribution review
===============================

Project license decision
------------------------

The owner explicitly approved the MIT license for project-owned contributions.
The root LICENSE now uses the exact line::

    Copyright (c) 2026 Ravi Konduru

Third-party copyright holders and attribution text are unchanged. The earlier
private Ravi K proposal remains an archived draft, not the current license.
The root THIRD_PARTY_NOTICES.txt preserves verified source-adaptation and
embedded-component notices verbatim; it is not complete distribution clearance.
See licensing-owner-confirmation.rst for the latest provenance and Git privacy
checks. No GitHub publication is authorized by applying the license.

Dependency inventory
--------------------

dependency-license-inventory.json records metadata for the 88 installed locked
Python distributions and 307 npm lock entries, including optional platform and
development packages. It preserves declared license expressions where available.
Metadata is not a substitute for each distribution's actual license and NOTICE
files. Installed package license files remain in their distributions; no vendor
notices were removed or reclassified as project-owned code.

Important review points before distribution:

* Retain MIT/BSD/ISC copyright and permission notices for included source/bundles.
* Preserve Apache-2.0 license/NOTICE material where supplied by dependencies.
* Psycopg and psycopg-binary declare LGPL licensing. Review the installed license
  and bundled binary notices before redistributing a packaged application.
* Lucide icons and their upstream icon attribution remain subject to the actual
  package license files. Do not remove attribution while preparing screenshots
  or redistributing assets.
* The Button component identifies itself as shadcn/ui-derived. The upstream MIT
  license is https://github.com/shadcn-ui/ui/blob/main/LICENSE.md, copyright
  2023 shadcn. If retaining copied/substantial adapted source, include the full
  applicable copyright and permission notice in the eventual distribution.
  A dependency list or this URL alone is not a replacement for that notice.
* Review distribution-specific notices for native/binary dependencies, including
  cryptography, Pillow, psycopg-binary, esbuild, and optional platform packages.
* tiktoken omits a license metadata field; its installed distribution includes
  an MIT LICENSE. The inventory records that inspected source.

No claim of complete license clearance is made. Confirm provenance of any copied
component source and add the required third-party notices before publication.
A project MIT license would not replace third-party obligations.

Provenance follow-up
--------------------

The prior private 357-section notice draft and audit records remain unchanged.
The new root MIT license supersedes only the proposed project license identity.

react-remove-scroll-bar 2.3.8: all 24 installed dist/ files match the
integrity-verified 2.3.7 npm tarball dist/ files. Its npm gitHead matches the v2.3.7 tag, and
all five source files match the pinned repository commit supplying the MIT
notice. The exact publisher notice is now preserved in THIRD_PARTY_NOTICES.txt.
The unavailable 2.3.8 gitHead and absent tag remain release-metadata limitations;
no signed build attestation or recovered 2.3.8 commit is claimed.

stackback 0.0.2: its release tag is available, and formatstack.js exactly matches
the installed file. The embedded V8 BSD-style notice is now preserved verbatim
in THIRD_PARTY_NOTICES.txt. Its package-level MIT metadata does not replace
that component's BSD obligations. A package-level MIT notice remains unlocated.

uri-js-replace 1.0.1: the pinned upstream README and package declare intent and
MIT metadata, but no full notice was located. Copied tests mentioned in the
README do not establish a license for all package code. Publisher clarification
remains necessary; no attribution or permission text has been invented.

Do not treat these checks as clearance for native/bundled subcomponents or
additional platform distributions. No dependency was replaced or upgraded to
avoid the provenance review.
