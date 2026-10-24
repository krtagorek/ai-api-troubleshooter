Phase 14.1 durable diagnostic registry
====================================

Scope
-----
This phase persists governance, not trusted comparisons. No graph, agent prompt,
semantic-selection/routing, HTTP route, UI or worker execution integration is added.
No synthetic FixtureAuthority identity is used by the new service. Creating an active
row requires explicit authenticated administrator action; application startup and
endpoint approval do not activate contracts. Signed ingestion and cryptographic key
management remain absent; active_binding is an authorization-time snapshot, NOT a
semantic execution permit. Phase 14.2 must not treat it as a durable authorization
that survives subsequent revocation, endpoint-policy changes or checkpoint recovery.

Schema and transactions
-----------------------
0005, parent 0004, creates three app-owned tables only. diagnostic_contracts stores
UUID identity, tenant, owner, endpoint FK, aggregate lifecycle revision and revocation
revision. diagnostic_contract_versions has a composite contract/version PK, tenant
FK, exact approved endpoint revision, immutable JSONB document and SHA-256, current
status/revision, provider verification/review UUID references, reviewer and approving/
activating/revoking identities, and optional successor-version FK. diagnostic_contract_events
stores ordered version-specific lifecycle events, actors, references, timestamps,
controlled transition data, predecessor hash and SHA-256 event hash.

Parent row locks serialize every version mutation. Lock order is endpoint first,
then contract; locked ORM rows are refreshed to avoid stale identity-map revisions.
Every mutation requires the client's expected aggregate revision. Versions are
allocated under the parent lock. A PostgreSQL partial unique index independently
prevents two active versions for an identity. Version triggers reject document,
digest, endpoint revision, tenant/key or creation-time mutation and all deletion.
Identity ownership/binding fields are immutable. Audit triggers forbid UPDATE,
DELETE and TRUNCATE; TRUNCATE is also forbidden for identity/version tables. All
successful actions and their audit events commit or roll back together. Read-only
history takes a shared lock for a consistent sequence and verifies its hash chain.

Authorization and providers
---------------------------
Principal is reused from registry.authorization and must come from trusted application
authentication; the service never accepts fixture Actor objects or bearer tokens.
TenantAuthorizer.authorize(actor, tenant, api, owner, action) must independently
verify authenticated membership AND the endpoint-to-tenant binding for every action.
The existing endpoint table has no tenant column: the provider is mandatory, not an
inferred tenant assignment. It must distinguish read, owner and administrative actions.
No provider is installed by default, and exceptions/timeouts deny access without
exposing provider detail. Providers are trusted application composition, not request
or LLM data. Tenant IDs alone confer no authority.

OwnershipVerifier.verify and SecurityReviewer.review return server-validated opaque
UUID evidence references bound to actor/tenant/API/document digest. A production
implementation must verify ownership and independent review in its authoritative
system; returning a client-provided UUID is NOT verification. Providers run with a
bounded two-second timeout inside the transaction; they should use local, durable
verification state, not long-running external challenges. Tenant authorization must
be fresh/revocation-aware and should retain stable authorization through commit.

Draft registration and revisions require the exact endpoint owner. Owner verification
requires that owner and a working verification provider. Security review requires a
different actor plus an independent working security provider. Approval/activation
require configured AdminAuthorization and an administrator different from BOTH owner
and security reviewer. Revocation/supersession require a nonowner administrator.
Draft -> owner_verified -> security_reviewed -> approved -> active is explicit.
Revoked and superseded versions are terminal. Revision creates a new draft, preserving
all previous document versions and repeating verification/review/approval. Supersede
an active version explicitly with a newer approved successor before activating the
successor. Endpoint status, revision and approved_revision must match on creation,
revision, verification, review, approval, activation and active-binding reads.
Revocation can still proceed if endpoint approval has become stale or suspended.

Document validation and confidentiality
--------------------------------------
The implemented production v1 is intentionally narrower than the general design.
It accepts one declarative fencing-epoch template with explicit log pointers, roles,
scalar types, ordinal units, a shared domain, authority, same-term ordering, same-record
scope, freshness, exact rejection code, contradiction hold policy and qualified-only
inference boundary. Unknown versions/operators/fields, fixture formats, arbitrary
URLs, expressions and client lifecycle status fields are rejected. No expression
interpreter, fetcher or diagnostic tool exists. Documents contain no free-form secret
or request-text fields. Audit contains only bounded state data and opaque references.

Audit boundary and remaining production dependencies
---------------------------------------------------
Service-level authorization is enforced; this phase does not implement PostgreSQL RLS
or tenant membership tables. Deployment requires trusted tenant/endpoint mapping,
real identity/ownership/reviewer providers, restricted runtime DB roles without DDL,
TRUNCATE, trigger-disable or superuser authority, separate migration credentials,
backups, and independent immutable audit anchors. Hash chains and triggers do not
protect against a malicious database superuser. Denied attempts are raised without
writing lifecycle events; authentication/security-monitoring audit integration remains
separate work. No external key-management or signed ingestion claim is made.

Only disposable test databases received migration 0005. Deployment migration review
and authorization, authenticated route composition, operational permission grants,
provider revocation semantics, signed source provenance, evaluation permits and graph
state binding remain future work. Existing checkpoint tables remain separately owned.
Current estimated spend remains $0.0550552 against $2. No paid calls or manifest are
part of this phase.
