Phase 14.3: signed diagnostic ingestion (opt-in, not activated)
================================================================

Trust boundary
--------------
``diagnostic_contracts.ingestion.IngestionService`` is an explicit Python service,
not an HTTP route, graph node, upload hook, or contract activation mechanism.
Existing incident ingestion remains untrusted and unchanged. No historical logs
are backfilled. A signature establishes attribution to an authorized producer;
it does not establish observation accuracy, source authority for a semantic role,
or an upstream cause. Future contract evaluation must independently authorize the
source and preserve qualified versus definitive conclusions.

An authenticated Phase 14.2 TrustedPrincipal must match the signed producer UUID.
Current tenant membership, independently verified endpoint ownership, exact
endpoint approval revision, binding generation, approved contract version/digest,
and current owner/security-review attestations are checked transactionally.
Endpoint approval alone cannot grant producer or diagnostic-contract authority.
A server control administrator must explicitly issue a bounded producer grant.
That grant binds tenant, endpoint, producer, source, resource, contract/version,
endpoint revision and binding generation, and expires in at most 120 seconds.
Grants are single-issue and may only be changed to revoked, never unrevoked.
Production key and identity providers are NOT configured by this implementation.

Wire contract and cryptography
-----------------------------
The strict ``signed-observations-1`` payload contains envelope/grant/producer IDs,
tenant/API/contract identifiers, immutable contract digest and revisions, source,
resource, correlation ID, capture timestamp, sanitized snapshot and SHA-256 digest.
The outer strict envelope contains only algorithm, key ID, payload and signature.
Only Ed25519 is accepted. A signature covers the complete outer algorithm/key ID
and payload using sorted compact UTF-8 JSON, ensure_ascii=False, allow_nan=False,
with Pydantic JSON-mode UUID/datetime serialization. This version's serialization
is explicit; producers must use the same canonical bytes (it is not RFC 8785).
No key discovery URLs, remote endpoint execution, client keys, executable rules,
or algorithm negotiation are supported. Metadata URLs have no execution path.
Snapshots containing labels, credential-like content, nonfinite numbers or a
mismatched digest are rejected, not silently rewritten. Payloads are bounded to
256 KiB. Validation errors suppress input contents; no raw envelope is logged.

ProducerKeyAuthority is server injected and must supply a public Ed25519 key with
exact tenant/API/producer bindings, validity interval, revocation status and a
fresh status lease of at most 30 seconds. Key lookup is bounded to two seconds;
missing/stale/unavailable providers deny. Private keys never enter the service or
database. Tests generate ephemeral synthetic private keys only. Rotation retains
old key IDs only while explicitly valid and unrevoked; saved fingerprints prevent
replacing a key under an old ID from validating historical attribution.

Freshness, replay, and storage
-----------------------------
Captures must be nonfuture, within 120 seconds, and inside key validity. No future
clock skew is accepted; delayed observations require separate reconciliation.
Verification repeats freshness, key, grant, membership, ownership, endpoint and
contract checks. Historical attribution rows remain immutable after expiry or
revocation, but they cannot supply a current trusted verification result.

Additive migration 0007 creates app.diagnostic_ingestion_grants and
app.diagnostic_ingestion_envelopes. Envelopes preserve the signed sanitized
payload, signature, key ID/fingerprint, exact payload and snapshot hashes, capture
and ingestion timestamps, context identifiers and signature-valid result.
Foreign keys bind existing endpoint and immutable contract version records.
Scope digest uniqueness and envelope UUID uniqueness prevent replay. One
producer/source/resource/correlation scope accepts exactly one immutable snapshot;
changed observations in that scope are conflicting and denied, even with a new
key or grant. Different correlations must have strictly increasing capture times
for that producer/API/resource. This intentionally conservative policy also
rejects equal timestamps and out-of-order delivery; it does not choose a winner
between contradictory diagnostic observations.

Endpoint row locks serialize ingestion and endpoint/binding lifecycle changes;
contract and grant locks prevent concurrent revoke/ingest lost updates. Accepted
envelopes and their authority audit event commit together. Existing append-only
authority events record grant issue, revocation and accepted payload/key hashes.
Database triggers prevent envelope UPDATE/DELETE/TRUNCATE and grant rewrite or
unrevocation. Rejected requests raise safe errors, create no accepted attribution,
and leave no partial grant/envelope/audit transaction. Denial telemetry and
external audit anchoring are separate deployment responsibilities. The existing
hash-chained diagnostic lifecycle and evaluation audits remain untouched.

Verification and deployment limits
----------------------------------
Run the existing test harness with TEST_DATABASE_URL pointing to a development
PostgreSQL database ending in _test and CREATE DATABASE permission. The fixture
creates and destroys a unique database per test and applies Alembic there only.
No primary migration is run by this phase. ``alembic check`` must pass on those
disposable databases. Production migration requires separate authorization.

Production still requires authenticated producer provisioning, real key rotation
and revocation adapters, private signing key custody outside this application,
Phase 14.2 identity and ownership/security attestation providers, operational
clock discipline, least-privileged database roles, retention policy, resource
limits, denial monitoring and external immutable audit anchors. A privileged DB
administrator can bypass triggers; they are not cryptographic external anchors.
A remote key/identity revocation can race the final check within its status lease;
no atomic cross-service revocation guarantee is claimed. Phase 14.4 must address
execution-time fencing before any production diagnostic-contract integration.
``verify_provenance`` returns a fresh snapshot verification, not an execution
permit. No graph routing, agent prompt, endpoint tool, UI, or activation is changed.
