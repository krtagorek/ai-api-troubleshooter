# AI API Integration Troubleshooter

A Week 3 GenAI Builder portfolio application for investigating API integration failures. It turns sanitized HTTP evidence and logs into grounded hypotheses, diagnostic checks, and reviewable reports. It can also stop and ask for more evidence when the observations do not support a useful diagnosis.

The problem: status codes alone rarely explain integration failures. Authentication, authorization, validation, throttling, schema, timeout, and dependency failures need different evidence. This project separates evidence collection, causal reasoning, proposed actions, and validation rather than treating an LLM answer as a verified fix.

This is a tested local submission candidate, **not a configured production deployment**. No diagnostic action or proposed fix runs automatically. Production identity, ownership, key management, and operations still require configuration and acceptance.

## Architecture

```mermaid
flowchart LR
  UI[React / TypeScript] --> API[FastAPI / authenticated REST]
  API --> PG[(PostgreSQL application tables)]
  API --> Q[Durable PostgreSQL job queue]
  Q --> W[Independent async worker]
  W --> C[Classifier]
  C --> I[Log investigator]
  I --> G{Grounding / sufficiency}
  G -->|qualified evidence| R[Root cause analyst]
  G -->|insufficient evidence| H[Evidence hold]
  R -->|qualified selected hypothesis| P[Resolution generator]
  R -->|inconclusive| H
  P --> V[Validator]
  V --> F[Safe final report / hold]
  W <--> CP[(LangGraph checkpoints)]
  W --> E[Persisted ordered progress events]
  E -->|Authenticated SSE / replay| UI
```

The five LangGraph-compatible agents use injected LangChain `ChatOpenAI` clients and Pydantic structured outputs:

| Agent | Responsibility |
| --- | --- |
| Error classifier | Classify the observed failure and cite evidence. |
| Log investigator | Select snapshot-bound evidence handles and identify diagnostic gaps. |
| Root cause analyst | Compare grounded hypotheses, uncertainty, and counterevidence. |
| Resolution generator | Propose diagnostic checks or a plan requiring human approval. |
| Validator | Report `NOT_TESTED` unless an authorized validation tool actually ran. |

Python 3.11, LangGraph, LangChain/OpenAI, FastAPI, SQLAlchemy 2 async, Alembic, PostgreSQL, and HTTPX form the backend. React, TypeScript, Vite, Tailwind, Radix/shadcn-style source components, React Router, and TanStack Query form the frontend. pytest, Vitest, and Playwright cover the application. Streamlit remains a compatibility dependency; the delivered UI is React.

### Grounding, security, and reliability

- Trusted Python builds deterministic incident-scoped evidence catalogs. Models select short handles; Python resolves references, scalar observations, and provenance. Unsupported, stale, fabricated, and cross-snapshot references fail validation.
- Logs, responses, and user text remain untrusted evidence. Redaction and a centralized output-safety boundary protect prompts, state, reports, API views, and follow-ups. Raw provider audit records are restricted and excluded from the public file set.
- Uploaded logs retain user-supplied origin; catalog provenance does not establish signed source authenticity.
- Qualified hypotheses remain tentative. Evidence coverage is not causal accuracy, and a completed validator wrapper is not successful validation. Actionable changes require human approval.
- Endpoint registration/ownership/approval controls are separate from diagnostic-contract approval. Execution tools constrain methods, paths, destinations, DNS resolution, connection-time enforcement, and redirects.
- Optional diagnostic contracts support independent review, immutable versions, signed ingestion, freshness, replay prevention, and fresh single-use authorization permits with revocation fencing. **The feature flag defaults OFF and grants no authority by itself.**
- Application tables and LangGraph checkpoints use separate PostgreSQL schemas. Durable jobs use leases, bounded attempts, recovery, cancellation, idempotent report publication, and persisted progress events.
- Authenticated SSE supports `Last-Event-ID` replay, reconnect, deduplication, and terminal-state reconciliation. The timeline reconstructs wrapper completion across attempts. Historical wrapper events do **not** prove a model invocation.

## Repository

```text
agents/                 Five structured-output agents
backend/                FastAPI authentication, REST, SSE, administration
graph/                 LangGraph state, routing, report policy, workflow
models/                 Pydantic domain contracts
worker/                 Durable queue, leases, recovery, execution
persistence/            Application storage and checkpoint boundaries
migrations/versions/       Additive migrations 0001–0008
registry/               Endpoint approval and constrained authorization
diagnostic_contracts/  Identity, lifecycle, provenance, permits, integration
tools/                 Grounding, sufficiency, redaction, safe HTTP utilities
data/                  Frozen synthetic V1/V2 inference data and separate labels
evaluation/            Offline metrics and explicitly gated paid-evaluation tools
frontend/               React application and browser tests
tests/fixtures/         Portable sanitized and synthetic regression inputs
docs/                   Design, security, and implementation documentation
reports/                Private/local generated evidence; not published
```

## Prerequisites and safe local setup

Use Python **3.11**, [uv](https://docs.astral.sh/uv/), Node.js **22.12+ within 22.x, or 24+**, npm, and Docker Desktop (or an existing local PostgreSQL server). The lock was generated from the tested Python environment; macOS ARM64 with Python 3.11 is verified. Other platforms need their own installation/test check.

Run all commands from the repository root unless a terminal says otherwise. These instructions create a **new local database**; do not apply them to an existing primary database without reviewing its migration state and taking a backup. Never upload `.env`, credentials, private signing keys, database dumps, or the private `reports/` archive.

### Terminal 1 — dependencies and PostgreSQL

```bash
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip sync --require-hashes requirements.lock
uv pip check
cp .env.example .env
chmod 600 .env
```

Edit `.env` privately before starting Docker. Set a new local password and use its URL-encoded form in both connection URLs. Keep the sample database name `troubleshooter` for a fresh Compose installation, and set:

```dotenv
POSTGRES_PORT=55432
POSTGRES_DB=troubleshooter
DATABASE_URL=postgresql+asyncpg://troubleshooter:YOUR_URL_ENCODED_PASSWORD@127.0.0.1:55432/troubleshooter
CHECKPOINT_DATABASE_URL=postgresql://troubleshooter:YOUR_URL_ENCODED_PASSWORD@127.0.0.1:55432/troubleshooter
TEST_DATABASE_URL=postgresql://troubleshooter:YOUR_URL_ENCODED_PASSWORD@127.0.0.1:55432/troubleshooter_test
APP_ENV=development
DEMO_AUTH_ENABLED=true
DURABLE_WORKER_ENABLED=false
DIAGNOSTIC_CONTRACTS_ENABLED=false
```

Do not use the placeholder password. Set `POSTGRES_PASSWORD` to the actual local password as well. Generate a subject UUID and random local login token, then save them only in `.env` as `DEMO_SUBJECT_ID` and `DEMO_AUTH_TOKEN`:

```bash
python -c 'import uuid; print(uuid.uuid4())'
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

The token is a reusable **local application login credential**, not an OpenAI key. Do not include it in an incident. For local registry administration only, set `REGISTRY_ADMIN_IDS` to that subject UUID. Production rejects this development authentication mode. Leave the OpenAI key unset for no-cost tests and incident creation.

```bash
docker compose up -d postgres
docker compose ps
```

Compose creates `troubleshooter_test` only on **initial volume initialization**. Changing `POSTGRES_PASSWORD` later does not rotate the password in an existing volume. Do not remove an existing volume to fix authentication. Use a separately provisioned disposable database instead.

### Explicit migrations — new local database only

The following commands are manual; application startup does not run them for you. Loading `.env` through Python avoids treating it as shell code.

```bash
python -c "from dotenv import load_dotenv; from alembic.config import main; load_dotenv(); main()" upgrade 0008
python -c "from dotenv import load_dotenv; from alembic.config import main; load_dotenv(); main()" current
python -c "from dotenv import load_dotenv; from alembic.config import main; load_dotenv(); main()" check
python -m persistence.manage
```

Migrations 0001–0004 provide the core incident/registry/worker schema. Migrations 0005–0008 add contract, identity/ownership, ingestion, and authorization persistence. Use all eight for the current complete application and test suite. They do not activate contracts or configure identity providers. Checkpoint setup is separate and uses the `langgraph` schema.

### Terminal 2 — backend

```bash
source .venv/bin/activate
python -m uvicorn main:app --env-file .env --host 127.0.0.1 --port 8000 --no-access-log
```

Check from another terminal:

```bash
curl --fail http://127.0.0.1:8000/api/v1/health
curl --fail http://127.0.0.1:8000/api/v1/health/ready
```

Readiness verifies database tables, not worker availability or model access. OpenAPI is at <http://127.0.0.1:8000/docs>.

### Terminal 3 — React

```bash
cd frontend
npm ci
npm run dev
```

Open <http://127.0.0.1:5173>, choose **Connect API**, and enter the private local demo token. Vite proxies `/api` to port 8000. Never put server credentials in `VITE_*` variables or frontend source. The connection UI keeps the login token in memory; reconnect after a full reload if required.

First save a harmless synthetic incident through Troubleshooter: a local inventory request to `/inventory/1`, HTTP 401, an observed “authentication missing” error, and no actual credentials. Inspect the dashboard/history. **Saving is separate from execution.** With durable execution disabled, execution returns 503 rather than claiming a job was queued. Sparse evidence can legitimately end in `awaiting_evidence`.

### No-cost deterministic demonstration

These tests inject explicit deterministic responses; they do not contact OpenAI:

```bash
python -m pytest tests/test_workflow.py tests/test_agents.py tests/test_legacy_tentative_reports.py -q
```

Database/HTTP/worker integration tests also use synthetic identities and deterministic providers. Browser fixture tests exercise React with mocked API responses and must not be described as live model results. Historical Phase 14.6 acceptance additionally used real local HTTP, Chromium, PostgreSQL, independent workers, and synthetic signed evidence. That evidence remains in the private archive.

The maintainer acceptance harnesses under `tests/phase14_6_*` have explicit local prerequisites, fixed local ports, private configuration preservation checks, and exclusive artifact directories; they are **not** a fresh-clone demo command. See their corresponding `docs/phase14_6*.rst` before running them.

### Optional local mock and real OpenAI execution

In a separate terminal, an optional deterministic mock API can be started with:

```bash
source .venv/bin/activate
python -m uvicorn mock_api.server:app --host 127.0.0.1 --port 8001 --no-access-log
```

It has a labeled demo-only scenario selector and fixed fixtures. Use only the existing development-mock registration/verification/approval flow; endpoint approval is never diagnostic-contract approval. The ordinary troubleshooting workflow does not automatically call external diagnostic APIs.

**Real execution incurs charges.** There is no general `--deterministic` worker switch and no application-wide manual-UI spend cap. Before deliberately executing a small manual test:

1. Configure `OPENAI_API_KEY` privately in `.env` and choose an account-accessible `OPENAI_MODEL`; never send the key to the browser. Check account spending controls and current model pricing separately.
2. Set `DURABLE_WORKER_ENABLED=true`, restart the backend yourself, and start the worker in Terminal 4:

   ```bash
   source .venv/bin/activate
   python -m worker
   ```

3. Confirm backend readiness and that the worker stays running without its sanitized startup error. There is no dedicated worker readiness HTTP endpoint. Only a deliberately authorized job can prove full execution; UI wrapper receipts alone cannot prove paid model invocation.
4. Submit one synthetic investigation, inspect the authoritative job status, timeline, evidence, report or evidence hold, and `NOT_TESTED` validation. Do not repeatedly resubmit a hold hoping to force a report. SDK calls disable retries, but durable worker attempts are a separate reliability mechanism.

Manual UI spending is separate from the historical evaluation ledger. The paid evaluation CLI requires a fresh manifest, accounting reconciliation, and explicit approval. Public test fixtures are **never** paid authorization.

### Diagnostic-contract administration

The React administration section and REST interfaces exist but default to unavailable. A local demo token cannot configure trusted production ownership, membership, independent security review, producer keys, or revocation services. Server composition must inject those providers. Do not enable the semantic flag to bypass missing configuration. Synthetic provider tests demonstrate the lifecycle; production providers and operations remain an integration dependency. See [administration boundaries](docs/phase14_6_3-diagnostic-administration.rst) and [trusted-contract design](docs/trusted-diagnostic-contracts.rst).

## Verification and evaluation limits

```bash
python -m pytest tests -q
# Explicit real PostgreSQL tests; .env is read only to obtain local test settings.
python -c "from dotenv import load_dotenv; load_dotenv(); import pytest; raise SystemExit(pytest.main(['tests','-q']))"
cd frontend
npm ci
npx playwright install chromium
npm test
npm run test:browser
npm run build
```

`TEST_DATABASE_URL` must reference a local development database ending in `_test` with a role allowed to create/drop databases. Integration fixtures create unique disposable databases, migrate them, and remove them. Without that variable, those tests explicitly skip. Browser tests require port 5173 free (or set `PLAYWRIGHT_PORT` to another local port); they do not reuse a running manual server.

Portable tests use sanitized regression snapshots and clearly synthetic preparation/ledger contexts. Authentic historical audit/review checks are separately marked `private_audit` and skip in public checkouts. Maintainers with the unchanged private archive can run `python -m pytest tests -q --private-audit`. A skip is not a passed archival integrity check. The public fixtures do not recreate a real human review or reset the original ledger.

Current security and licensing readiness is recorded in [publication review](docs/publication-security-licensing.rst); the preceding [submission baseline](docs/submission-readiness.rst) remains available. V1 has 120 synthetic incidents (80 development/40 test) and known family overlap; it is not an independent benchmark. V2 has 80 incidents split by family (40/40), separate labels, and semantic review rubrics. Prior real-model pilots used small, repeatedly examined samples and single-reviewer exploratory reviews. Neither lexical proxies nor those pilots establish independent diagnostic accuracy. Missing evidence, conservative holds, incomplete selection, counterevidence, and mechanism-versus-origin reasoning remain limitations.

No paid calls are part of this submission milestone. The historical evaluation record remains **$0.0550552 estimated spend against $2**, separate from any manual investigations. Historical artifacts are preserved privately; they are not required to run the public application.

## Publication and licensing

Do not publish the working directory wholesale. Run `python scripts/publication_audit.py` and follow [the publication checklist](docs/publication-checklist.rst). `.gitignore` cannot remove a secret already committed. This working directory currently has no Git metadata, so commit-history safety has not been established.

Project-owned code is licensed under the [MIT License](LICENSE), approved by the owner with `Copyright (c) 2026 Ravi Konduru`. Third-party rights remain separate: retain [third-party notices](THIRD_PARTY_NOTICES.txt) and review the [dependency licensing inventory and outstanding provenance gaps](docs/third-party-licenses.rst). See the [current licensing confirmation and publication checks](docs/licensing-owner-confirmation.rst). License approval does not authorize a GitHub push or deployment.
