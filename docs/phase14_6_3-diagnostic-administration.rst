Phase 14.6.3: diagnostic contract and signed-ingestion administration
====================================================================

Scope and default
-----------------

The FastAPI control plane and React /diagnostic-contracts page expose existing
Phase 14.1–14.4 services. They do not change agent prompts, graph routing,
execution approval, validation, checkpoint recovery, or the default semantic
feature flag. No migration is added: all tables already exist through 0008.

All new routes require authentication. Authenticated access returns 503 unless
the application is explicitly composed with trusted administration providers.
An environment flag cannot grant this authority. DemoAuthenticator is rejected
by this control plane even when demo mode is permitted elsewhere in development.
No production providers or credentials are installed by this phase.

Explicit server composition
----------------------------

A deployment must inject its real JWTAuthenticator (configured issuer, audience,
key rotation and revocation sources), global administrator allowlist, independent
ownership/security evidence provider, and producer public-key authority::

    from backend.app import create_app
    from backend.diagnostic_admin import Administration
    from diagnostic_contracts.authority import production_registry
    from diagnostic_contracts.ingestion import IngestionService

    def administration(database):
        registry, authority = production_registry(
            database, identities=identity_provider,
            global_admins=administrators, evidence_provider=independent_proofs)
        ingestion = IngestionService(database, authority,
                                     keys=producer_public_key_authority)
        return Administration(registry, ingestion, requests_per_minute=120)

    app = create_app(authenticator=identity_provider, admins=administrators,
                     diagnostic_admin_factory=administration)

These names are intentionally unconfigured dependencies, not fixture identities.
The same authenticator and database must be used throughout composition.
Membership provisioning remains a trusted identity/control-plane operation;
HTTP clients cannot create memberships, roles or owner identities. Historical
endpoints are never backfilled automatically. Explicit endpoint binding requires
current actor and owner membership, approved endpoint revision and an independent
server-provider attestation. The owner is derived from the endpoint registry.

Endpoint inventory
------------------

All paths below are relative to /api/v1/diagnostic-admin/tenants/{tenant}.
Tenant, contract and endpoint IDs are selectors, not authorization grants.

* GET /endpoints: current tenant endpoint bindings.
* POST /endpoint-bindings/{api}: independently verified binding or explicit
  transfer using expected binding revision and approved endpoint revision.
* GET, POST /contracts: paginated list and owner-only draft registration.
* GET /contracts/{id}: latest version and lifecycle status.
* GET /contracts/{id}/versions: immutable version history and recorded
  ownership/security-review flags.
* POST /contracts/{id}/revisions: new draft requiring independent reapproval.
* POST /contracts/{id}/verify-owner: request a current, digest-bound ownership
  attestation from the server-configured provider.
* POST /contracts/{id}/security-review: independent reviewer attestation.
* POST /contracts/{id}/approve, /activate, /revoke, /supersede: fixed actions;
  clients cannot submit trusted lifecycle status. Supersession requires an
  approved newer successor. Endpoint approval never implies contract approval.
* GET /contracts/{id}/audit: paginated lifecycle payloads after full chain
  integrity verification by the service.
* GET /contracts/{id}/authority-audit: paginated contract-scoped verification,
  producer-grant and accepted-ingestion event references, without provider details
  or key fingerprints.
* GET /contracts/{id}/authorization: current contract approval/read status,
  explicitly not a semantic execution permit.
* GET, POST /contracts/{id}/grants: producer-grant summaries and explicit issuance.
* POST /contracts/{id}/grants/{grant}/revoke: fenced grant revocation.
* GET, POST /contracts/{id}/envelopes: safe ingestion summaries and signed
  envelope submission. Signatures are checked against trusted producer keys.
* GET /contracts/{id}/envelopes/{envelope}: fresh provenance verification.
  A negative status can mean revoked, expired, invalid or unverifiable provenance;
  it never confers authority. Signature validity does not establish truth.

Mutations on existing contracts require version, canonical digest,
expected_revision and endpoint_revision. Registration computes the initial digest
and revision on the server; initial endpoint binding has no contract yet.
Lifecycle/revision checks execute under the existing transaction locks. HTTP
producer issuance additionally checks independent administrator identity, tenant
contract-admin permission and current producer membership. Grant revocation is
scope-bound and concurrent repeated revocation is rejected. Signed-envelope
submission checks the outer scope against the exact signed payload and validates
the lifecycle revision under the same transaction as ingestion.

Permission boundaries
---------------------

Draft registration and revision require the actual endpoint/contract owner.
Security review requires a different current security-reviewer identity.
Approval and activation require tenant contract-admin permission plus global
administrator authorization, excluding both owner and security reviewer.
Revocation and supersession reuse the independent administrator checks.
Explicit tenant reader/reviewer/admin roles can read authorized tenant records;
this is a tenant governance view, not incident-owner-only access. Incident APIs
retain their existing owner isolation.

Ownership/security submission is synchronous: only a valid independent provider
verdict advances the lifecycle. No queue or pending background verification is
fabricated. Request bodies cannot provide trusted proofs, provider references,
roles, owner identity or approval status. Provider outages fail closed.

Limits, redaction and audit
---------------------------

Requests are bounded to 256 KiB before parsing, versus the unchanged 1 MiB limit
for other APIs. List endpoints use limit 1–100 and offset 0–10000. Unknown fields,
unsupported schemas/operators, arbitrary URLs and secret-bearing contracts are
rejected. Envelope/payload models are validated in strict JSON mode so UUID and
canonical signature rules remain intact. Payload templates and envelopes accept
objects or bounded JSON text. The browser sends the original JSON text after
safety checks, so JavaScript serialization cannot change large integers or
floating-point scalar representations before server-side signature verification.

A PostgreSQL advisory transaction lock serializes per-actor quota checks across
API processes. At most 120 requests/minute are accepted by default (server-only
configuration, 1–600). Every accepted quota slot adds an append-only controlled
authority audit record. Rate denial returns 429 with Retry-After. There is no
local cache that independently authorizes requests. Production quota/audit index
capacity and retention planning remain operational dependencies.

Control audit queries pair the action/entity kind with exact tenant/contract
references; a UUID collision in a different table cannot import foreign events.

Response DTOs omit envelope documents, signatures, key fingerprints, provider
internals, bearer capabilities and private signing material. Structured redaction
runs before response serialization where free-form lifecycle data is returned.
Validation/provider/driver errors use safe messages and correlation headers.
No admin route issues a bearer execution permit or executes diagnostic actions.

React behavior
--------------

The dedicated section supports tenant selection, explicit endpoint ownership,
draft registration, immutable revisions, version selection, independent review,
approval, activation, supersession and revocation. Controls submit to the server;
visibility never substitutes for authorization. State-changing actions use
confirmation dialogs. Versions, grants, envelopes and audit views are paginated.
Recorded verification is distinguished from fresh authorization and provenance.

Producer scope and already-signed public envelope JSON can be submitted. Signing
occurs outside the browser. Private keys, reusable credentials and JWTs are
rejected client-side; the server independently validates all inputs. Submission
text is cleared on completion/error/close, and disconnect remounts the page and
clears query state. Nothing is placed in browser storage. Incomplete provider
configuration and rejected actions render controlled errors, never raw provider
messages. Existing dashboard/investigation/SSE flows remain available.

Examples and offline acceptance
-------------------------------

Use /docs or the versioned OpenAPI artifact for request schemas. With a privately
configured bearer identity, an existing authorized tenant and approved endpoint,
read a list without printing the credential::

    curl --fail-with-body -H "Authorization: Bearer $ADMIN_BEARER" \
      "$BASE/api/v1/diagnostic-admin/tenants/$TENANT/contracts?limit=20&offset=0"

Registration body is api_id, endpoint_revision and document. A lifecycle request
body is, for example::

    {"version":1,"digest":"<64-character current digest>",
     "expected_revision":4,"endpoint_revision":3}

Pass an exact reviewed body with --data-binary @request.json to the fixed
/approve or /activate path. The authenticated actor must independently meet
server policy. Never put private signing keys or reusable credentials in these
JSON files. Normal unconfigured application routes remain unavailable.

The isolated acceptance executable is::

    .venv-py311/bin/python -m tests.phase14_6_3_acceptance

It creates/removes a disposable local PostgreSQL database, migrates through
0008, uses real HTTP and Chromium over the compiled frontend, synthetic JWT
identities and real Ed25519 verification with synthetic keys. Server ownership,
security, identity-revocation and public-key services are test providers; this
is not production identity or ownership verification. A fixture creates an
unrelated bootstrap endpoint; the accepted endpoint and contract flow are
separately created and controlled through the real browser interfaces.

The browser establishes ownership, registers/verifies/reviews/activates a
contract, issues a grant, submits evidence, inspects provenance/audit, revokes
and confirms unavailable provenance. A fresh downstream semantic authorization
attempt is denied after revocation. The existing Troubleshooter creates an
incident; this admin acceptance does not execute an LLM investigation. Worker,
report and recovery coverage comes from the backend regression suites.

Remaining production dependencies
---------------------------------

Real IdP and tenant-membership provisioning, independent proof/review evidence,
producer key rotation/revocation, short-lived reference renewal/provisioning,
least-privilege DB roles, clocks, and audit retention must be configured and
accepted separately. Synthetic four-minute proof sources are not production
configuration. Fresh proof expiry and ownership transfer intentionally fence
old bindings; operational replacement/reapproval must respect those generations.
No primary migration, production activation, paid provider call, deployment or
next phase is performed by this implementation.
