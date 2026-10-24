Phase 14.5: opt-in trusted semantics in LangGraph
================================================

Configuration and boundaries
-----------------------------
DIAGNOSTIC_CONTRACTS_ENABLED defaults to false. The build_workflow argument
trusted_semantics_enabled can explicitly override it; malformed values reject.
With the flag off, the legacy nodes, edges, readiness comparator and outputs are
unchanged. A checkpoint's descriptive trusted_semantic field is never permission.
The flag supplies no tenant, identity, producer key, active contract or permit.
With it on, missing trusted server composition results in an evidence hold.
No production contracts, providers, primary migrations or default activation are
configured. This phase adds no database schema or HTTP/React administration API.

Pass a SemanticIntegration instance using build_workflow(trusted_semantics=...).
Existing Worker(workflow_options=...) and InvestigationService workflow options
already forward this dependency. Its asynchronous binding_resolver(thread_id,
snapshot_digest) must authenticate the current actor and select a server-owned
Binding(actor, tenant_id, api_id, envelope_id) for that exact job/thread. It must
not derive permissions from incident text, request headers, LLM output, checkpoint
metadata, or arbitrary user-selected endpoints. Real worker restart requires a
new authenticated principal from the configured production identity provider.
The process-local test identities are not a production identity service.

Producer labels are an additional server-configured restriction keyed by signed
producer UUID and source. They cannot grant permission: an explicit current durable
producer grant must already bind that producer to the exact contract/version,
resource and endpoint, whose approved document declares the authoritative producer
role. The server restriction must match that approved role and the grounded field.
A self-asserted log label alone never establishes authority. Revoking producer
permission must use durable grant/membership/key/fence controls, not local caches.

Evaluation and graph changes
------------------------------
The investigator's existing node first validates grounded evidence, then evaluates
the trusted path. There is no new SSE node name. PermitService(require_active=True)
requires current active-contract status inside its locked authorization transaction;
Phase14.4's default opt-in registry use remains unchanged for compatibility.
The exact sanitized Incident JSON must match the immutable signed snapshot digest.
The version-matched document is revalidated against the bounded contract schema.
All pointer values must exist in the canonical evidence catalog with strict types.
Only a fresh single-use permit can admit the pure evaluator; no permit is stored in
graph state. The evaluator never calls a URL, external tool, model, fix, or function
specified by incident data. The generic semantic comparator is unchanged.

The allowlisted fencing evaluator requires the approved shared domain, authoritative
producer role, resource and correlation, one bounded same-record pointer group,
matching outer correlation when present, exact capture/observation chronology,
contract freshness, nonnegative strict integer epochs/terms, equal terms and the
supported stale-fencing rejection. Booleans cannot substitute for integers. Missing
values, mixed scopes, changed terms/resets, stale observations and conflicting
correlated role values reject. Equal/reversed epochs do not promote. Wraparound
is unsupported, never normalized into a successful comparison. Reachability is
an observed boolean, not proof of leader health. A routed epoch below current,
with corroborating rejection, permits qualified mechanism analysis only; it never
establishes why upstream routing became stale or confirms a definitive cause.

The result references grounded scalar observations only. Trusted augmentation adds
those exact catalog entries to evidence without modifying the signed Incident.
An ephemeral ContextVar delivers committed readiness to RootCauseAnalystInput's
existing analysis_readiness field; live provider schemas and prompts do not change.
The context is node-local and reset in finally; it is never serialized as authority.
Root cause, resolution, validator and final-report entry repeat fresh acquisition.
Thus resume at a downstream node cannot reuse a previous node's authorization.
Supported diagnoses are capped to tentative. Without an actually invoked configured
validation_tool, the gated validator returns NOT_TESTED, including injected model
claims of passing checks. Tool composition must retain Phase7 endpoint approval.
Human approval and existing report/output-safety boundaries remain enforced.

Checkpoint and failure behavior
--------------------------------
The optional versioned TrustedSemanticResult contains descriptive status, consumed
permit UUID (not the secret), snapshot digest and result digest. The existing closed
checkpoint model registry explicitly allows this model; no dynamic imports or
pickle are added. Old checkpoints may omit the field. State validation checks its
shape and exact incident digest; new evidence clears a stale descriptive result.
No actor sessions, bearer permit, JWT, signing key, private authorization context or
provider exception enters checkpoints or SSE. Raw historic outputs/audits are not
rewritten. Result digests refer to immutable permit-consumption records.

Each graph boundary reacquires authorization, even if a checkpoint says qualified.
Failure produces a descriptive denied result and safe evidence hold, clears stale
diagnosis/fix/report/validation claims at guarded downstream entry, and does not
invoke the analyst or invent a successful diagnosis. On the enabled path there is
no silent fallback to legacy promotion after authorization failure. With the flag
off, the original legacy evidence policy still applies without reading this field.
No external validation tool is enabled by this feature. Existing queue lease and
checkpoint fencing still control publication; permits never extend worker leases.

Phase14.4's limitations remain: pure computation only, results after commit, no
physical CPU interruption, no exactly-once CPU claim after connection/process loss,
and fresh remote key/IdP status leases rather than atomic remote revocation.
Admission rechecks and row locks serialize committed revocation races. A completed
evaluation can precede a revocation, but the next guarded agent requires fresh
permission. Historic authorized output is not retrospectively rewritten.

Verification and next milestone
-------------------------------
Tests use synthetic active contracts in isolated disposable PostgreSQL databases,
real signatures and AsyncPostgresSaver, mocked providers and deterministic agents.
They cover flag-off legacy parity, missing authority, inactive/revoked contracts,
wrong tenant/endpoint/snapshot, provider outages, missing producer authority,
strict semantics, checkpoint recovery at analyst and resolution, truthful validation,
real worker lease reassignment, ordered event replay and revocation races.
Existing Phase9 API approval and worker security suites remain regression gates.
React routes, OpenAPI response schemas and SSE node/event codes are unchanged.

Production still needs trusted binding/authentication composition, actual IdP/key
and ownership/security-review providers, explicit producer provisioning and durable
revocation delivery, deployment migration authorization for prior phases, clock
and operational controls, least-privileged DB roles, and independent audit anchors.
Phase14.6 must run separately authorized application-level end-to-end acceptance;
this phase provides no production activation or paid evaluation approval.
