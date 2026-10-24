Trusted diagnostic contracts: design and offline prototype
========================================================

Status and boundary
-------------------
This is a design, not a deployed trust service. prototypes/diagnostic_contracts.py
is a fixture-only in-memory authority with an explicitly enabled fixture mode.
Nothing imports it from agents, graph, registry execution, worker or HTTP routes.
It cannot authorize HTTP execution, collect evidence, access a database or invoke
an LLM. Synthetic contracts are NOT evidence for any benchmark's field semantics.
The existing epoch and cache-age routing holds remain unchanged.

Architecture and independent approvals
-------------------------------------
The endpoint registry governs where operations may execute. A separate diagnostic
registry governs what observed fields mean. Endpoint approval never approves a
semantic contract. Production activation must require both the exact currently
approved endpoint revision AND an independently approved semantic digest.

An authenticated API owner registers an immutable contract version in their tenant.
An ownership verifier binds it to the existing API owner and approved registry
revision. A separate security reviewer checks field semantics, producer identity,
ordering, confidentiality and scope; an administrator approves the reviewed digest.
Activation is a distinct explicit administrative action. An owner cannot self-review
or self-approve. Approval does not imply activation. Revision creates a new draft
and repeats verification/review/approval; it never edits an approved version.
Explicitly supersede an active version before activating its replacement.
Revocation immediately denies new evaluations, including previously issued permits.

The proposed lifecycle is draft -> owner_verified -> security_reviewed -> approved
-> active, with revoked/superseded terminal for that version. Every transition has
actor, tenant, previous/new status, version, digest, reason, authorization basis,
request/correlation ID, timezone-aware timestamp and chained event hash. Production
transactions must enforce optimistic revision checks, unique versions, a single
active version, separation of duties and append-only audit permissions. A hash
chain alone is not tamper-proof: use append-only DB grants and external immutable
anchors/signatures. No database tables or migrations are implemented here.

Contract schema and authoritative semantics
-------------------------------------------
The companion diagnostic-contract.schema.json is a proposed general representation.
The prototype deliberately executes only its narrower exported EpochContract schema.
A general production version contains:

* Schema version; immutable contract ID/version/digest; tenant and verified owner.
* API registry ID and exact approved endpoint revision/digest, separate diagnostic
  approval status, review records and activation/revocation/supersession metadata.
* Explicit field JSON pointers, semantic roles, scalar types, units and shared
  metric/fencing domain. Pointers must refer to allowed sanitized scalar evidence.
* Ordering direction and permitted type compatibility; no implicit string/boolean
  coercion, unit conversion, undocumented aliases or numeric epoch comparison.
* Term/reset/wraparound rules, valid ranges and equality/nonviolation semantics.
* Authoritative producer and provenance policy, freshness clock and allowed skew.
* Correlation/resource/tenant scope and required observation chronology.
* Exact rejection codes, meanings and required corroboration; explicit permitted
  hypotheses/conclusions and prohibited unsupported implications.
* Contradiction policy and evidence omission policy; fail closed on conflicting
  same-scope facts unless an approved chronology rule resolves them.

Descriptions and logs are data, never executable expressions or policy. No URLs,
headers, scripts, code templates, network fetches or LLM-authored rules are allowed.
A semantic administrator may select from a separately implemented operator allowlist;
arbitrary expressions or a contract claim of 'definitive' must not add engine powers.
An approved contract can limit an evaluator; it cannot bypass its maximum inference
boundary. The prototype always leaves definitive_confirmation=false and upstream_cause=null.

Incident, graph and recovery binding (proposed, NOT wired)
--------------------------------------------------------
A trusted ingestion layer verifies producer signatures or authenticated transport
and records an immutable sanitized snapshot digest and source attestations. A log's
self-reported producer ID, timestamp or correlation ID is not proof of provenance.
A contract authorization service obtains current tenant identity, ownership,
endpoint approval revision and active semantic version from authoritative services.
It issues a short-lived actor/snapshot-bound permit; tokens are opaque server-side
capabilities, not model output. Every evaluation rechecks current approval, revision,
revocation and permit scope. Deny if the registry or destination enforcement is down.

Future LangGraph state should store only contract ID/version/digest, endpoint
revision, snapshot digest, authorization decision ID, referenced observation IDs,
policy revision and evaluation result digest. Keep reusable capability tokens,
credentials and raw provider outputs OUT of graph state and checkpoints. At resume,
re-authorize from current authoritative policy. Historical approved versions remain
available for audit but cannot authorize current execution after revocation. Invalidate
cached decisions on policy epochs and use transactionally fenced authorization to
avoid check/use races. Routing consumes a verified evaluation result only after a
separately authorized integration, never the contract text or a model assertion.

Prototype mechanics
-------------------
FixtureAuthority requires fixture_mode=True. Its Actor and owner/ingester assertions
are synthetic trusted test inputs, NOT production authentication or ownership proof.
There is no API for untrusted users. fixture_endpoint is an administrative test
stand-in for a separate endpoint registry; it grants no diagnostic approval.

The fixed fencing template resolves one log record's grounded scalar leaves using
the existing incident-scoped catalog. It requires routed/current nonnegative integer
epochs in the SAME term, exact domain/resource/correlation, a known producer, a fresh
observation and the approved rejection string. It requires the full structured
context so selection cannot omit conflicting peers. It rejects equal/reversed
epochs and all reset/wrap cases; it never assumes modular arithmetic. A reachable
leader is not necessarily healthy, authoritative or unloaded. Either reachability
value can accompany a fencing hypothesis; it does not establish the upstream cause.

The owner-approved field semantics of this synthetic template are supplied by tests
only. This does not retroactively establish a production contract for V2-12-01.
Untrusted metadata can only supply observations; it cannot register, activate,
replace or revoke a contract. Responses are fixed policy text and references,
not copied untrusted action requests. Existing redaction/output safety stays intact.

Threat model and required controls
----------------------------------
SSRF: symbolic metadata and pure local evaluation; no fetch, execution or resolver.
Prompt injection: no eval/exec/template engine/LLM policy interpretation. Extra schema
keys and unsupported semantic modes fail; observed instructions cannot change rules.
Tenant crossing: every lookup/permit includes tenant; investigator identity is bound.
Forgery: production identity, producer signatures, service-owned approvals and audit
anchors are required; this in-memory fixture cannot resist a malicious Python host.
Stale permissions: recheck active version/revision and TTL every evaluation; no permit
survives revocation or supersession. Production needs atomic use-time fencing and
revocation-aware caches; the prototype is single-process and not a concurrency claim.
Ambiguity: missing peers, authority, terms, rejection, stale timestamps, conflicting
values or incomplete delivered context remain holds. Never equate counts with cause.
Secrets: use existing sanitized Incident/catalog; do not request credentials or put
raw provider responses in user-facing decisions. Restricted audits stay separate.

Remaining production work and acceptance gates
---------------------------------------------
Implement authenticated owner/security/admin services with independently verified
field documentation, signatures and audit identity; PostgreSQL migrations and
transactional lifecycle storage; tenant row-level controls; canonical contract
signatures and schema compiler/operator allowlist; ingestion provenance; permit
service and key rotation; revocation fencing/cache invalidation; verified clock/skew
and chronology policies; recovery authorization; safe HTTP endpoints and UI; and
separate integration into graph state/routing. None is enabled by this milestone.

Require adversarial concurrency, failover, replay, provenance-forgery and revoked
checkpoint tests against isolated databases before production integration. Review
synthetic semantics independently of benchmark labels. Preserve the $2 ledger and
$0.0550552 current estimated spending; this milestone makes no paid calls and creates
no paid manifest. GO is only for separately scoped implementation design/work.
