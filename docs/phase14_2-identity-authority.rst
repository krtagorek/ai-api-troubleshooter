Phase 14.2: identity, tenant ownership and verification authority
================================================================

Offline implementation, not configured production identity
---------------------------------------------------------
JWTAuthenticator verifies RS256 signatures using PyJWT/cryptography, requires issuer,
audience, UUID subject, exp/iat/nbf/jti, forbids unsigned/HMAC/unknown algorithms and
token-controlled jku/jwk/x5u/critical header extensions. It accepts only current RSA
signing keys of at least 2048 bits from an injected KeyAuthority. Every authentication
and authorization rechecks bounded-fresh key and revocation snapshots. Removed or
replaced keys invalidate existing process-local identity sessions. Token tenant and
role claims never become permissions. Only opaque subject/session metadata is held
in memory; no JWT, password, bearer token or IdP credential is persisted.

Identity issuer/audience are explicit server configuration. KeyAuthority and
RevocationAuthority are trusted server adapters with bounded timeouts, not URLs or
claims supplied by users. Missing/unavailable/stale adapters fail closed. This phase
does not ship or configure a real IdP integration, issuer credentials, JWKS fetcher,
introspection transport or revocation subscription. Supply provider-specific adapters
that authenticate their source, constrain destinations and implement fresh revocation
semantics. No network discovery occurs in this implementation. Signing keys in tests
are ephemeral local RSA keys; mocked IdP responses are not production verification.

backend.auth.authentication_from_env accepts injected key/revocation providers when
IDENTITY_ISSUER/IDENTITY_AUDIENCE are configured; incomplete configuration fails.
Otherwise prior behavior is preserved. Demo authentication still requires explicit
development mode and is rejected in production. A demo/bare Principal cannot pass
the production diagnostic identity authority, which accepts only sessions it issued.
Existing FastAPI routes can authenticate through the existing dependency, but no new
diagnostic HTTP routes or automatic production provider composition are added.

Durable membership and endpoint mapping
--------------------------------------
Additive migration 0006 adds diagnostic_memberships, diagnostic_endpoint_tenants,
diagnostic_attestations and diagnostic_authority_events. It adds nullable
binding_revision to diagnostic_contracts, without defaults or backfill. Existing
endpoint rows, approvals, incidents, reports and jobs are untouched. Historical
contracts with NULL binding revision are quarantined by the production composition;
there is no automatic adoption or historical-record rewrite. Explicit separately
approved remediation/backfill is necessary if these rows must be migrated later.

A separately configured control-plane administrator, authenticated by the fresh
identity authority, provisions memberships and endpoint mappings. Ordinary tenant
roles cannot self-grant permissions. Roles/tenant/owner/status supplied in bodies or
tokens are not trusted. Membership and mapping changes require expected revisions,
row locks and an append-only authority event. Only supported server-owned roles are
stored. Concurrent existing-row changes serialize; unique keys reject duplicate
first creation without accepting both grants.

Mapping requires the existing endpoint owner, current endpoint revision, independently
verified service control and a new opaque evidence reference. Ownership transfer to a
new tenant increments binding generation. Existing contracts bind to that immutable
generation, so transferring away and back does not restore old authority. An actual
owner change in the original registry also invalidates the mapping; this phase does
not invent a Phase 9 owner-transfer API or silently alter its ownership records.
A legitimate original-registry transfer must precede explicit new mapping and fresh
contract verification. Endpoint revocation and revision changes deny access.

Verification and independent review
-----------------------------------
EvidenceRequest includes kind, tenant, endpoint, owner, verifying subject, contract,
version, canonical document digest, endpoint revision, binding generation and
membership generation. A trusted injected evidence provider must independently verify
service control or an authorized human review, then return an opaque UUID and the
exact request digest with short expiry. A submitted UUID, owner declaration or token
role is NOT control proof. No credentials or proof payloads are stored. Production
providers must authenticate the evidence source and implement their own control
challenge/review workflow; no self-attestation or default provider is installed.

Persisted provider references are unique: a committed proof cannot be reused across
contracts, versions, actors, tenants or endpoints. Proof records are immutable except
one-way revocation, with auditable creation/revocation. Before approval, activation
and active-binding reads, exact proof context and expiry/revocation are rechecked.
Owner and reviewer memberships must still be enabled, fresh and at the generation
recorded by the proof. Removing and regranting a reviewer role cannot revive a stale
review. An independent reviewer and nonowner/nonreviewer administrator remain required.
Endpoint approval never implies semantic approval. Production proof TTL is presently
bounded at five minutes; deployments need an explicit renewal/reapproval policy.
Renewing an ownership mapping increments its generation and intentionally invalidates
prior contract bindings; this implementation does not silently renew approvals.

Transaction boundaries and remaining limitations
-----------------------------------------------
Production composition uses authorize_in_transaction and proof hooks on the existing
registry. Endpoint rows are locked first, mapping/membership/proof rows are shared
locked until lifecycle commit, and contract mutations use refreshed parent locks.
Control-plane updates take exclusive locks, preventing a local membership change,
proof revocation or ownership transfer from committing through a concurrent protected
lifecycle operation. Remote IdP revocation cannot be made atomic with PostgreSQL;
it is checked on every action with a bounded freshness lease (up to 30 seconds).
Deployment must choose that risk window and authoritative revocation behavior.

Identity sessions are process-local and bounded. A new worker/request must authenticate
again; no raw identity session is stored in graph/checkpoints. Runtime DB roles must
lack superuser, trigger-disabling or untrusted DDL powers; authoritative provider
implementation/composition remains security-critical. Authority events are append-only
DB records; existing contract lifecycle history remains hash-chained. External immutable
audit anchors and denied-action monitoring remain deployment dependencies.

No diagnostic contracts were activated outside test fixtures. No graph routing,
agent prompt, ingestion provenance, external diagnostic execution or paid evaluation
is implemented. Migration 0006 was applied only to disposable test databases, with
Alembic schema checks. The primary database requires separate migration and explicit
mapping/backfill authorization. Carry-forward remains $0.0550552 against $2.
