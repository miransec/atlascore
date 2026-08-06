# AtlasCore

**Secure enterprise AI for knowledge, data and workflow automation**

AtlasCore is a multi-tenant enterprise AI operations platform.
Organisations connect knowledge sources, company databases and approved tools,
then use controlled AI agents to answer questions, analyse data and execute
audited workflows — with human approval gates on every write action.

---

## What this is not

- A ChatGPT clone or basic chatbot
- A simple LangChain demonstration
- An interface that only wraps an LLM API
- A collection of disconnected agents
- A fake dashboard with static data

---

## What this is

A production-quality platform with:

- **Multi-tenant isolation** — three independent layers (PostgreSQL RLS with USING + WITH CHECK + fail-closed null guard, explicit repository predicates, SQLAlchemy with-criteria) ensure organisations never share data
- **Seven-role RBAC** — owner, administrator, workflow_builder, analyst, operator, viewer, auditor; permission checks enforced at router and service layers from a single hardcoded matrix; ordinary org members hold a nullable org role
- **Two-step login** — credentials return a pre-auth session cookie and the available organisations; the user selects one to receive an org-scoped JWT; org membership is re-verified on every request
- **Secure pre-auth session** — between login steps a single-purpose server-side session (stored by SHA-256 hash, issued as an HttpOnly cookie, 5-minute lifetime, consumed atomically) carries the authenticated user_id into step 2; the org-selection request body contains only `organisation_id` — user_id injection is structurally impossible
- **Secure browser tokens** — access token in JavaScript memory, refresh token in HttpOnly Secure SameSite=Lax Path=/api/v1/auth cookie, CSRF double-submit cookie pattern (JS-readable `csrf_token` cookie + `X-CSRF-Token` header, bound to refresh session via HMAC-SHA256, Origin validated); no localStorage usage
- **Knowledge retrieval** — hybrid vector + keyword search with access-control predicates applied inside queries; unauthorised chunks never reach reranking or model context
- **Safe analytics** — validated read-only SQL against an allow-listed schema; no generated SQL for writes
- **Typed workflow engine** — graph execution with checkpointing, retry budgets, cost limits and durable pause/resume
- **Human approval gates** — write actions pause for approval; arguments shown to the approver are cryptographically bound to the arguments executed
- **Tool registry** — every tool declares schema, risk level and approval requirement; unregistered tools cannot execute
- **Policy engine** — deterministic guardrails outside model prompts; injection scanning is advisory, not a security boundary
- **Complete audit trace** — append-only, INSERT-only at DB level; Phase 1A events written transactionally with the business operation; dashboards and exports in Phase 8
- **Secure token design** — Argon2id passwords with pepper versioning, JWT access tokens, refresh-token families with rotation and reuse detection
- **Composite FK pattern** — workspace-owned tables use `FOREIGN KEY (workspace_id, organisation_id) REFERENCES workspaces(id, organisation_id)` to enforce org-workspace consistency at the DB level
- **Startup secret validation** — REPLACE_* placeholder values in required secrets cause a hard startup failure
- **Evaluation suites** — real scores from real test cases; nothing fabricated

---

## Quick start (local development)

### Prerequisites

- Docker ≥ 24 and Docker Compose v2
- Git
- [nvm](https://github.com/nvm-sh/nvm) or Node.js 24 LTS directly (`.nvmrc` pins version 24)
- [uv](https://github.com/astral-sh/uv) ≥ 0.5 (Python package manager)
- Python 3.12 (`.python-version` pins this; `uv` will install it if needed)

### Clone and configure

```bash
git clone https://github.com/your-org/atlascore.git
cd atlascore

# Use Node 24 if using nvm
nvm use

# Copy env template — replace REPLACE_* placeholders before starting.
# At minimum, set: JWT_SECRET_KEY, ARGON2_PEPPER, REFRESH_TOKEN_PEPPER
# Startup will fail with a clear error if any REPLACE_* value is present.
cp .env.example .env
```

### Start services

```bash
docker compose up --build
```

Services started:

| Service | Port |
|---------|------|
| Backend API | http://localhost:8000 |
| Frontend | http://localhost:3000 |
| PostgreSQL (pgvector) | localhost:5432 |
| Redis | localhost:6379 |
| API Docs | http://localhost:8000/docs |

### Seed the database

```bash
docker compose exec backend python scripts/seed.py
```

### Run backend tests

```bash
docker compose exec backend pytest --cov=app tests/
```

### Run lint and type checks

```bash
docker compose exec backend ruff check app/
docker compose exec backend mypy --strict app/
```

### Install backend dependencies locally (for IDE support)

```bash
cd backend
uv sync
```

### Run frontend build check

```bash
# Frontend build runs inside Docker to use Node 24:
docker compose run --rm frontend npm run build

# Or locally with Node 24:
cd frontend && nvm use && npm ci && npm run build
```

---

## AI provider configuration

AtlasCore ships with a deterministic test provider that requires no credentials.
This is the default for all CI runs and evaluation suites.

```env
# Grounded Q&A provider
ANSWER_PROVIDER=deterministic-test   # default — no credentials, no network
ANSWER_PROVIDER=openai               # requires OPENAI_API_KEY
ANSWER_PROVIDER=anthropic            # requires ANTHROPIC_API_KEY

# Force deterministic provider regardless of ANSWER_PROVIDER (safe for staging)
ANSWER_DEMO_MODE=true
```

All deterministic responses are clearly labelled `"provider": "deterministic-test"`.
Provider failures never expose API keys, stack traces, or system prompts to callers.

The grounded answering pipeline:
1. Never calls the provider if evidence is empty or below the sufficiency threshold.
2. Never falls back to general model knowledge if evidence is insufficient.
3. Never exposes storage keys or embedding vectors in API responses.
4. Treats all retrieved evidence as untrusted data, never as instructions.

### Embedding provider

```env
EMBEDDING_PROVIDER=mock      # default — no credentials
EMBEDDING_PROVIDER=openai    # uses OPENAI_EMBEDDING_MODEL + OPENAI_API_KEY
```

---

## Repository structure

```
atlascore/
├── backend/          FastAPI application (Python 3.12, uv)
├── frontend/         Next.js 16.2.x App Router (Node 24)
├── worker/           Background task worker (ARQ)
├── mcp_server/       MCP protocol server
├── evals/            Evaluation suites and datasets
├── sample_data/      Seed SQL and seed documents
├── docs/             Architecture, ADRs, security
├── scripts/          Setup, seed, migration helpers
├── infra/            Docker and CI configuration
├── .nvmrc            Node.js version: 24
├── .python-version   Python version: 3.12
├── LICENSE           MIT
├── PLAN.md           Phased implementation plan
├── TASKS.md          Task registry and progress
└── SECURITY.md       Threat model and security policy
```

**Note:** `uv.lock` and `package-lock.json` are generated in Phase 1A by
`uv lock` and `npm install` respectively. They are committed after generation,
not before. The repository does not contain hand-written stubs for these files.

---

## Architecture overview

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full system design.

### Textual architecture diagram

```
┌────────────────────────────────────────────────────────────────────────┐
│  Browser (Next.js App Router)                                          │
│  JWT in JS memory · refresh token in HttpOnly cookie · CSRF tokens     │
└────────────────────┬───────────────────────────────────────────────────┘
                     │  HTTPS + CORS allowlist
┌────────────────────▼───────────────────────────────────────────────────┐
│  FastAPI (Python 3.12)  — /api/v1/                                     │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────────┐    │
│  │  auth/      │  │  knowledge/  │  │  teams / members / invites  │    │
│  │  2-step JWT │  │  CRUD+search │  │  RBAC (7 roles, hardcoded) │    │
│  └─────────────┘  └──────┬───────┘  └────────────────────────────┘    │
│                           │                                             │
│  ┌────────────────────────▼───────────────────────────────────────┐    │
│  │  Grounded Answering Pipeline (Phase 2C/2D)                     │    │
│  │                                                                 │    │
│  │  question → normalise → [Phase 2B retrieve] → EvidencePacket   │    │
│  │            → sufficiency gate → PromptBuilder (trusted+untrust) │    │
│  │            → AnswerProvider → CitationValidator → response      │    │
│  │                                                                 │    │
│  │  Providers: deterministic-test (default) · openai · anthropic   │    │
│  │  Provider NEVER called with zero evidence                       │    │
│  │  General knowledge fallback: NEVER                              │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │  Phase 2B Hybrid Retrieval                                     │    │
│  │  lexical (PostgreSQL FTS) + vector (pgvector cosine)           │    │
│  │  → RRF fusion → reranking → RetrievalResult[]                  │    │
│  │  Access-control predicates inside SQL (RLS + explicit WHERE)   │    │
│  └────────────────────────────────────────────────────────────────┘    │
└───────────────────────┬────────────────────────────────────────────────┘
                        │
          ┌─────────────▼──────────────────┐
          │  PostgreSQL 16 + pgvector       │
          │  RLS: FORCE + fail-closed null  │
          │  Composite FK workspace guard   │
          │  Append-only audit log          │
          └────────────────────────────────┘
          ┌─────────────────────────────────┐
          │  Redis                          │
          │  Pre-auth sessions · caches     │
          └─────────────────────────────────┘
```

Key principles:

- Business logic lives in service classes, never in API routers or model prompts
- Every tenant-scoped DB query is filtered by three independent layers: PostgreSQL RLS (USING + WITH CHECK, FORCE, fail-closed null guard), explicit repository WHERE clauses, and SQLAlchemy with-criteria
- Knowledge retrieval access control is applied inside the SQL query — unauthorised chunks never reach reranking or model context
- Workspace-owned tables use composite FKs to guarantee org-workspace consistency at the DB level
- Login is two steps: credential verification returns available orgs; org selection issues an org-scoped JWT; org membership is re-verified on every request
- Access tokens in JavaScript memory, refresh tokens in HttpOnly cookies, CSRF tokens required on state-changing requests
- Retrieved documents are data, never system instructions; injection scanning is advisory, not the primary security boundary
- Write actions require explicit authorisation and, where configured, human approval with argument-hash binding
- All credentials are injected from environment; startup fails on REPLACE_* placeholders
- Refresh tokens use a token-family design with rotation and reuse detection

### Verification numbers (Phase 2D baseline)

| Metric | Value |
|--------|-------|
| Evaluation cases | 46 (16 categories, A–P) |
| Evaluation pass rate (deterministic baseline) | measured by `python -m app.evaluations.run` |
| Grounding pipeline security properties | 10/10 verified (see Part F report) |
| API endpoints (Phase 2A–2D) | 35+ |
| Test files (backend) | 15+ |
| Frontend pages (knowledge) | Sources, Search, Ask a Question |

---

## Security

See [SECURITY.md](SECURITY.md) for the security policy and [docs/SECURITY.md](docs/SECURITY.md) for the full threat model and mitigations.

To report a vulnerability, email security@atlascore.example *(replace with real address)*.

---

## Phases

| Phase | Description | Status | Tag |
|-------|-------------|--------|-----|
| 0 | Foundation documents and architecture | ✅ Complete | phase-0-complete |
| 1A | Core multi-tenant foundation (auth, orgs, RBAC, RLS) | ✅ Complete | phase-1a-complete |
| 1B | Invitations, teams, service accounts, API keys | ✅ Complete | phase-1b-complete |
| 2A | Knowledge ingestion (sources, documents, chunking, embeddings) | ✅ Complete | phase-2a-complete |
| 2B | Hybrid retrieval (lexical + vector + RRF, secure predicates) | ✅ Complete | phase-2b-complete |
| 2C | Grounded Q&A (abstention, citation, injection resistance) | ✅ Complete | phase-2c-complete |
| 2D | Real LLM provider, evaluation framework, observability, UX polish | ✅ Complete | phase-2d-complete |
| 3 | Safe analytics database | Planned | — |
| 4 | Workflow engine | Planned | — |
| 5 | Tool registry and approvals | Planned | — |
| 6 | MCP integration | Planned | — |
| 8 | Observability and audit dashboards | Planned | — |
| 9 | Security hardening and deployment | Planned | — |

---

## Licence

MIT — see [LICENSE](LICENSE).
