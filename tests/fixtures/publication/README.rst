Portable submission regressions
===============================

These are extracted, sanitized synthetic-incident state/output projections and
synthetic preparation templates. They are NOT independent holdouts, human review
records, paid approval manifests, provider credentials, or the historical ledger.
The original provider outputs, reviews, and append-only audits remain unchanged
in the private reports archive.

The unsafe credential-request strings in output-safety-state.json are deliberate
negative security inputs. They contain no credential values. Test code must
apply the existing safety boundary, not present those strings as instructions.

The manifest templates retain only public model/schema/prompt configuration,
synthetic incident IDs, and numeric reservation parameters. tests/conftest.py
constructs exclusive temporary mock-only bindings. Synthetic complete-review
flags do not claim a real person approved a model dispatch. Production code is
not patched outside pytest and still requires its original private proof chain
and explicit fresh user authorization.

v2-plan.json is a test template. Its ledger hash is replaced only in an isolated
unit-test copy, with a synthetic ledger, while running the real plan validator.
It is never used to authorize execution. No historical budget record is reset.

Private-archive tests use --private-audit. Their skips in a public checkout are
reported explicitly and are not counted as successful historical verification.
