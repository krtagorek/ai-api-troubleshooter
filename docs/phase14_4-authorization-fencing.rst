Phase 14.4: durable execution-time diagnostic fencing
====================================================

Scope and authority
-------------------
The opt-in PermitService wraps a server-injected, pure synchronous local semantic
evaluator. It is not used by current agents, LangGraph, REST, checkpoint recovery,
or endpoint tools. No production contract is activated. Approved contracts may be
used by this offline service; this does not confer production activation. A future
LangGraph integration must additionally enforce active-contract policy at its
own entry point and must not treat checkpoint fields as authorization.

A Permit contains a random 256-bit secret in SecretStr and a UUID. Only its SHA-256
hash is persisted. Neither the secret nor JWT/private keys enter audit records.
The immutable permit row binds the exact authenticated actor and tenant, endpoint
revision, endpoint tenant-binding generation, contract/version/digest, parent and
version lifecycle revisions, contract revocation revision, actor/producer/reviewer
membership generations, producer grant, envelope/payload/snapshot hashes, key ID
and public-key fingerprint, resource/correlation scope and shared fence generation.
The sole permitted operation is qualified_semantic_evaluation_v1. Client-supplied
schemas, operation names, approval status, evaluator code or URLs are never used.

The maximum permit lifetime is five seconds and is further shortened by actor JWT,
fresh IdP/key status leases, ingestion key expiry, producer grant expiry, observation
freshness, memberships, endpoint proof and owner/security-review proof expirations.
PostgreSQL clock_timestamp() is checked before admission and publication; local
clock skew exceeding one second denies. Every use repeats Phase14.2 authorization
and Phase14.3 signature/provenance checks. Local cached principals or checkpoint
state never independently authorize a use. Recovery requires reauthentication;
a fresh current principal for the same actor can use a still-valid unconsumed
capability, but all checks repeat. Expired capabilities must be replaced.

Database and linearization
--------------------------
Additive migration 0008 creates app.diagnostic_authorization_fences,
app.diagnostic_authorization_permits and app.diagnostic_permit_consumptions.
There is no primary migration, historical row update, or backfill. Lazy fence
creation uses INSERT ON CONFLICT after locking the endpoint. A fence generation
must increment by exactly one; durable key-block sets may only grow. Fence deletion
and truncation are prohibited. Permit and consumption rows are append-only, with
foreign keys to endpoint/provenance and permanent unique single-use consumption.

The lock order is endpoint, contract/version and current binding/membership/proof
rows, producer grant, then endpoint fence. Existing lifecycle, membership, binding
and grant revocations conflict with those same row locks. The execution admission
linearization point is the final successful fresh check in the live PostgreSQL
transaction, while locks remain held. Pure computation, result digest, consumption
and audit commit in that transaction. The result is returned only after commit.

A revocation committed before admission is visible and rejects the old permit.
If admission locks win, a competing revocation waits, so evaluation commits first.
If revocation locks win, evaluation subsequently observes the changed generation
or authority and denies. Endpoint-wide revoke invalidates all outstanding permits;
it does not disable future freshly authorized issuance. Key-scoped revoke also
stores a permanent key-ID/fingerprint block, so stale remote caches cannot reissue
permits for that key. Retired keys require new key material/ID and new provenance.
Membership removal/regrant changes the generation and cannot restore old permits.
Supersession, lifecycle revisions and ownership transfer similarly invalidate them.

Failure and limits
------------------
No physical interruption of CPU work is claimed. A dropped connection releases
locks; a transaction liveness check before computation and a database check and
commit afterward prevent a disconnected worker from publishing a protected result.
There is no absolute physical-start guarantee across an asynchronous connection
loss between instructions. Only pure, side-effect-free computation is permitted;
external calls, tools, fixes, callbacks supplied by clients and irreversible effects
are outside this interface. Deployments must supply a reviewed deterministic
adapter. Returned structures also pass existing JSON-safe redaction/output safety.
This service does not itself prove a semantic conclusion or source-role authority.

A process/connection failure rolls back consumption and outcome/audit publication.
Recovery may repeat pure computation after fresh authorization; a committed outcome
is permanently single-use. Exactly-once physical CPU execution is not claimed.
Database unavailability is a safe PersistenceError, never a permissive fallback.
Remote issuer/key revocation cannot be made atomic with PostgreSQL: every admission
refreshes providers, but a remote change inside a provider's advertised lease can
remain unseen for at most that lease (<=30 seconds, with permits <=5 seconds).
Remote-provider outages deny. A durable key block overrides an otherwise valid
remote lease. Production providers, key custody, clock discipline, external audit
anchors and operational revocation delivery remain deployment requirements.

Audit and verification
----------------------
The existing protected append-only authority audit records permit issuance,
consumption, stale/context/replay denial and endpoint/key revocation with IDs,
revision/context hashes, correlation IDs and timestamps. Unauthenticated or forged
capabilities are not attributed to another user's permit. Database/process failure
cannot commit an in-transaction denial event; infrastructure failure telemetry and
external audit anchoring remain separate responsibilities. Historical hash-chained
audit records are not changed. A privileged database administrator can bypass DB
triggers; least-privileged roles and independent immutable audit sinks are required.

Disposable PostgreSQL tests exercise independent services and sessions, competing
consumption, both revocation orderings, a real terminated test database connection,
rollback/recovery, key cache override, scope isolation, supersession, ownership and
membership changes, expiry, denied evaluator entry and audit/immutability checks.
No paid providers, production identities or production signing keys are used.
