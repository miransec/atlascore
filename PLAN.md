# AtlasCore — Implementation Plan

> **Tagline:** Secure enterprise AI for knowledge, data and workflow automation
> **Version:** 0.2.0 — Phase 1B complete
> **Last updated:** 2026-08-04

---

## Purpose

This document is the single source of truth for the AtlasCore build plan.
Every phase begins by reading this file and TASKS.md.
Every phase ends by updating both files with actual results.

---

## Product Goal

AtlasCore allows an organisation to create a workspace, add users,
assign roles, connect approved data sources and configure AI workflows.
The system authenticates users, checks permissions, builds a typed execution
plan, retrieves authorised evidence, executes read-only analysis, pauses for
human approval on write actions and stores a complete audit trace.

---

## Monorepo Root Structure

```
atlascore/
├── backend/                   # FastAPI application
│   ├── app/
│   │   ├── api/               # HTTP routers (thin, no business logic)
│   │   ├── auth/              # JWT, sessions, pre-auth sessions, API keys, permissions.py
│   │   ├── organizations/     # Orgs, workspaces, memberships, invitations
│   │   ├── knowledge/         # Ingestion, chunking, embeddings, retrieval
│   │   ├── analytics/         # Safe SQL layer, schema inspection
│   │   ├── workflows/         # Workflow definitions, versioning, scheduling
│   │   ├── agents/            # Execution engine, plan builder, graph runner
│   │   ├── tools/             # Tool registry, handlers
│   │   ├── approvals/         # Approval objects, lifecycle, idempotency
│   │   ├── policies/          # Policy engine, guardrails
│   │   ├── providers/         # LLM/embedding/reranking provider interfaces
│   │   ├── evaluations/       # Eval datasets, runners, reports
│   │   ├── observability/     # Tracing, metrics, cost tracking
│   │   ├── audit/             # Append-only audit log
│   │   ├── database/          # Async SQLAlchemy engine, session factory
│   │   ├── models/            # ORM models
│   │   ├── schemas/           # Pydantic v2 schemas
│   │   └── main.py            # Application entry point
│   ├── tests/                 # pytest test suite
│   ├── alembic/               # Database migrations
│   └── pyproject.toml         # Backend dependencies and tooling config
├── frontend/                  # Next.js 16.2.x App Router application
│   ├── app/                   # Pages and layouts
│   ├── components/            # Accessible UI components
│   ├── lib/                   # Typed API client, hooks, utilities
│   └── package.json
├── worker/                    # Background task worker (ARQ)
├── mcp_server/                # MCP server exposing AtlasCore tools
├── evals/                     # Evaluation suites and datasets
├── sample_data/               # Seed SQL, seed documents, seed configs
├── docs/                      # Architecture, ADRs, security, API docs
├── scripts/                   # Setup, seed, migration helper scripts
├── infra/                     # Docker, compose, CI configs
├── docker-compose.yml         # Full-stack local dev environment
├── docker-compose.test.yml    # Isolated test environment
├── .env.example               # Required environment variables (no secrets)
├── .gitignore
├── .nvmrc                     # Node.js version pin: 24
├── .python-version            # Python version pin: 3.12
├── LICENSE                    # MIT
├── README.md
├── PLAN.md                    # This file
├── TASKS.md                   # Current task registry
└── SECURITY.md                # Threat model and security policy
```

**Note:** `uv.lock` and `package-lock.json` are generated files. `uv.lock` is
created by `uv lock` in Phase 1A task P1A-02. `package-lock.json` is created
by `npm install` in Phase 1A task P1A-54. Both are committed after generation.
Manually created stubs are not valid replacements.

---

## Technology Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.12 (`>=3.12,<3.13`) | Async, strong AI ecosystem; upper-bound avoids 3.13 incompatibilities during build |
| Package manager | uv | Fast resolver, lockfile support, pyproject.toml native |
| Web framework | FastAPI | Async, Pydantic v2 native, auto-docs |
| ORM | SQLAlchemy 2 async | Type-safe, async, with-criteria support for RLS session binding |
| Migrations | Alembic | Standard; RLS DDL in migrations |
| Database | PostgreSQL 16 + pgvector | Vector search + full-text + RLS all in one engine |
| Cache / broker | Redis 7 (single instance, key prefixes) | Session store, distributed locks, pub/sub |
| Node.js | 24 LTS | Active LTS; pinned in `.nvmrc` |
| Frontend | Next.js 16.2.x (Active LTS) | App Router, RSC, TypeScript |
| Styling | Tailwind CSS + shadcn/ui | Utility-first, accessible primitives |
| Auth — passwords | `pwdlib[argon2]` | Modern; Argon2id is OWASP-recommended; replaces passlib/bcrypt |
| Auth — tokens | `PyJWT` | Lightweight, actively maintained; replaces python-jose |
| Workflow engine | Custom typed graph + LangGraph (pause/resume only) | Thin boundary; swappable |
| AI providers | Interface-based, mock default | No vendor lock-in |
| Tracing | OpenTelemetry | Provider-agnostic |
| Linting | Ruff | Fast, Rust-based |
| Type checking | mypy strict | Enforced in CI |
| Testing | pytest + pytest-asyncio | Async support |
| Container | Docker + Compose v5 | Local dev and CI |

---

## Database Domains

| Domain | Tables (planned) |
|--------|-----------------|
| Identity | `users`, `sessions`, `refresh_tokens`, `pre_auth_sessions` |
| Organisation | `organisations`, `workspaces`, `organisation_memberships`, `workspace_memberships`, `teams`, `team_memberships`, `invitations`, `service_accounts`, `api_keys` |
| Knowledge | `documents`, `document_versions`, `chunks`, `connectors`, `connector_credentials` |
| Analytics | `data_connections`, `allowed_tables`, `query_logs` |
| Workflows | `workflows`, `workflow_versions`, `workflow_executions`, `workflow_steps`, `step_executions`, `schedules` |
| Tools | `tool_registry`, `tool_executions` |
| Approvals | `approval_requests`, `approval_decisions` |
| Providers | `provider_configs`, `model_usage_logs` |
| Evaluations | `eval_datasets`, `eval_cases`, `eval_runs`, `eval_results` |
| Observability | `traces`, `spans`, `cost_records` |
| Audit | `audit_events` |

**Tenant-scoped tables** — every table in Organisation, Knowledge, Analytics,
Workflows, Tools, Approvals, Providers, Evaluations, and Observability domains
has `organisation_id NOT NULL REFERENCES organisations(id)`.

**Non-tenant tables** — `users` is a global resource. RLS is not applied to
`users`. `pre_auth_sessions` holds ephemeral pre-login state; it is also global
(not org-scoped) because it exists before org selection.

**RBAC source of truth** — `backend/app/auth/permissions.py` is the single
authoritative fixed permission matrix. No mutable `roles`, `permissions`, or
`role_permissions` tables are created in Phase 1A. Role names are stored in
membership rows as enum values. Custom roles deferred to a later ADR.

---

## Multi-Tenancy Model

### Tenancy hierarchy

```
Organisation (global root)
  ├── organisation_memberships  (user ↔ org; org_role: owner | administrator | null)
  └── Workspace (1..N per org)
        ├── workspace_memberships  (user ↔ workspace; role: one of 5 workspace roles)
        ├── Knowledge base
        ├── Data connections
        ├── Workflows
        └── Audit log (workspace-scoped view)
```

### Membership design

**`organisation_memberships`** — one row per user per organisation.
`org_role` is nullable: `owner | administrator | NULL`.
NULL = ordinary member with no org-level privileges.
Every workspace member must also have an `organisation_memberships` row.

```
organisation_id   FK → organisations  NOT NULL
user_id           FK → users          NOT NULL
org_role          organisation_role   NULLABLE  -- owner | administrator | NULL
invited_by        FK → users          NULLABLE
joined_at         TIMESTAMPTZ         NOT NULL
PRIMARY KEY (organisation_id, user_id)
```

**`workspace_memberships`** — workspace-level roles only.
Composite FK enforces org-workspace consistency at the DB level.

```
organisation_id   FK → organisations  NOT NULL
workspace_id                          NOT NULL  -- part of composite FK
user_id           FK → users          NOT NULL
role              workspace_role      NOT NULL
invited_by        FK → users          NULLABLE
joined_at         TIMESTAMPTZ         NOT NULL
PRIMARY KEY (workspace_id, user_id)
FOREIGN KEY (workspace_id, organisation_id)
    REFERENCES workspaces(id, organisation_id)
```

`workspaces` carries `UNIQUE(id, organisation_id)` to support this composite FK.
This pattern applies to every future workspace-owned table.

### Composite FK pattern

```sql
-- On workspaces:
ALTER TABLE workspaces ADD CONSTRAINT workspaces_id_org_unique
    UNIQUE (id, organisation_id);

-- On every workspace-owned child table:
FOREIGN KEY (workspace_id, organisation_id)
    REFERENCES workspaces(id, organisation_id)
    ON DELETE CASCADE;
```

### Permission resolution

1. Verify JWT; extract `user_id`.
2. Re-fetch `organisation_memberships` row for `(user_id, organisation_id)`.
   Not found → 403 (immediate revocation, not at token expiry).
3. `org_role = 'owner'` or `'administrator'` → org-level permission set. Skip to 6.
4. `org_role` is NULL. Fetch `workspace_memberships` row for `(user_id, workspace_id)`.
5. Not found → 403.
6. Look up permission set in `permissions.py`.
7. Apply policy engine decision.

### Organisation ownership

No `owner_id` on `organisations`. Ownership = `organisation_memberships` row
where `org_role = 'owner'`. Enforced by deferred constraint trigger.
Transfer uses `SELECT FOR UPDATE` + atomic promote/demote in one transaction.

### Row-Level Security — three-layer strategy

**Layer 1 — PostgreSQL RLS**

Every tenant-scoped table has `FORCE ROW LEVEL SECURITY`. Each table has a
**single permissive `FOR ALL` policy** with both `USING` and `WITH CHECK`:

```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <table> FORCE ROW LEVEL SECURITY;

CREATE POLICY <table>_tenant_isolation ON <table>
FOR ALL
USING (
    organisation_id =
    NULLIF(
        current_setting('app.current_organisation_id', true),
        ''
    )::uuid
)
WITH CHECK (
    organisation_id =
    NULLIF(
        current_setting('app.current_organisation_id', true),
        ''
    )::uuid
);
```

**What each clause protects:**
- `USING` — evaluated against every existing row considered by SELECT, UPDATE
  (the pre-update row), and DELETE. A row whose `organisation_id` does not match
  the session's organisation is invisible to SELECT, un-updatable, and
  un-deletable.
- `WITH CHECK` — evaluated against the row as it will exist after an INSERT or
  UPDATE (the post-write row). An INSERT or UPDATE that would produce a row
  belonging to a different organisation is rejected.
- Together, `USING` + `WITH CHECK` on a `FOR ALL` policy protect all five DML
  operations: SELECT, INSERT, UPDATE (both the existing and new row), DELETE.

**Why a single permissive policy, not multiple restrictive:**
PostgreSQL requires at least one applicable permissive policy to allow access.
A restrictive-only policy without a permissive base always denies — which is
not the intent for the normal operating path. A single permissive `FOR ALL`
policy with both clauses is the correct pattern for standard tenant isolation.
Restrictive policies are reserved for additional constraints layered on top
(e.g. limiting access to specific columns).

**Fail-closed null guard:**
`NULLIF(current_setting('app.current_organisation_id', true), '')::uuid` returns
NULL when the setting is absent or empty. A NULL comparison with `=` evaluates
to NULL (not true), so the policy denies the operation. Absent context always
fails closed.

The trusted context is set inside every org-scoped transaction:

```python
# OrganisationScopedSession — immediately after BEGIN:
await session.execute(
    text("SELECT set_config('app.current_organisation_id', :org_id, true)"),
    {"org_id": str(organisation_id)},
)
# true = transaction-scoped; cleared automatically on COMMIT/ROLLBACK
```

The application role is denied BYPASSRLS. FORCE RLS ensures even the table
owner cannot bypass the policy.

**User-context RLS (pre-org-selection):**

```sql
CREATE POLICY org_memberships_user_read ON organisation_memberships
FOR SELECT
USING (
    user_id =
    NULLIF(
        current_setting('app.current_user_id', true),
        ''
    )::uuid
);
```

`app.current_user_id` is set after global JWT verification, before org
selection. Allows listing a user's available organisations without requiring
an org context. This policy is permissive FOR SELECT only; all write operations
on `organisation_memberships` require an org context through the main policy.

**Layer 2 — Explicit repository predicates**

Every repository method appends `.where(Model.organisation_id == org_id)`.

**Layer 3 — SQLAlchemy with-criteria / loader criteria**

`with_loader_criteria` registered for all tenant-scoped models. Relationship
loads also carry the organisation filter.

### RLS tests required

`tests/security/test_cross_tenant.py` must include:
- Cross-tenant SELECT: query from org A session returns zero rows for org B data
- Cross-tenant INSERT: INSERT of row with org B's `organisation_id` in an org A session is rejected by WITH CHECK
- Cross-tenant UPDATE (existing row): UPDATE on org B's row from org A session — USING clause makes row invisible; update silently affects zero rows or raises
- Cross-tenant UPDATE (change org_id): UPDATE that changes a row's `organisation_id` to a different org is rejected by WITH CHECK
- Cross-tenant DELETE: DELETE on org B's row from org A session — USING clause makes row invisible; deletes zero rows
- No context (absent `set_config`): SELECT returns zero rows; INSERT is rejected; verifies fail-closed behaviour

---

## Authentication and Organisation-Selection Flow

### Pre-authentication session

Between credential verification and organisation selection, a short-lived
**pre-auth session** prevents user_id from being transmitted in the request
body at org-selection time, eliminating user_id injection attacks.

**`pre_auth_sessions` table:**
```
id              UUID PK
session_hash    VARCHAR(128) NOT NULL UNIQUE  -- SHA-256(raw_token); raw never stored
user_id         FK → users   NOT NULL
created_at      TIMESTAMPTZ  NOT NULL
expires_at      TIMESTAMPTZ  NOT NULL         -- created_at + 5 minutes
consumed_at     TIMESTAMPTZ  NULLABLE         -- set atomically on use; NULL = valid
```

### Step 1 — `POST /api/v1/auth/login`

1. Verify credentials against global `users` table.
2. Generate a random pre-auth token (`secrets.token_urlsafe(32)`).
3. Store `SHA-256(raw_token)` in `pre_auth_sessions` linked to `user_id`.
   Raw token is discarded from server memory immediately after hashing.
4. Set `app.current_user_id` in a short-lived DB session.
5. Query `organisation_memberships` through user-context RLS policy.
6. Return `{ organisations: [...] }` in response body.
7. Set `pre-auth-session` cookie: `HttpOnly; Secure; SameSite=Lax;
   Path=/api/v1/auth/select-organisation; Max-Age=300` (5 minutes).
   Cookie value is the raw pre-auth token.

No JWT or refresh token is issued at this step.
Login failure: `auth.login_failed` is audited via independent insert.

### Step 2 — `POST /api/v1/auth/select-organisation`

Request body: `{ "organisation_id": "..." }` only.
`user_id` is **never** accepted from the request body — it is derived
exclusively from the server-side pre-auth session.

1. Read pre-auth cookie. Missing or invalid → 401.
2. Hash the raw cookie value. Look up `pre_auth_sessions` by hash.
   Not found → 401.
3. Check `expires_at > now()`. Expired → 401.
4. Check `consumed_at IS NULL`. Already consumed → 401 (reuse detected).
5. Set `consumed_at = now()` atomically (UPDATE … WHERE consumed_at IS NULL;
   verify one row affected).
6. Derive `user_id` from the `pre_auth_sessions` row.
7. Verify `organisation_memberships` row for `(user_id, organisation_id)`.
   Not found → 403.
8. Issue 15-minute org-scoped access token (in response body, for memory storage).
9. Issue refresh token, insert `refresh_tokens` row.
10. Set refresh cookie: `HttpOnly; Secure; SameSite=Lax;
    Path=/api/v1/auth; Max-Age=<REFRESH_EXPIRE>`
11. Set CSRF cookie (see CSRF design below).
12. Emit `org.organisation_selected` audit event (transactional).
13. Clear the pre-auth cookie (`Set-Cookie: … Max-Age=0`).

### Per-request membership verification

Every authenticated request: verify JWT → re-fetch `organisation_memberships`
row for `(user_id, org_claim)` → 403 if absent → set
`app.current_organisation_id` → execute query.

### Org count edge cases

| Count | Behaviour |
|-------|-----------|
| 0 | Empty list; client shows "contact administrator"; no cookies set |
| 1 | Client may auto-select and call step 2 immediately |
| 2+ | Client shows picker |

---

## CSRF Design

### Pattern: double-submit cookie

Two cookies are set at step 2:

| Cookie | HttpOnly | Secure | SameSite | Path | Purpose |
|--------|----------|--------|----------|------|---------|
| `refresh_token` | ✅ | ✅ | Lax | `/api/v1/auth` | Holds refresh token; not JS-readable |
| `csrf_token` | ❌ | ✅ | Lax | `/` | Holds CSRF token; JS-readable |

The `csrf_token` cookie is readable by JavaScript. The frontend reads it and
sends its value as the `X-CSRF-Token` request header on every state-changing
request. The backend compares header value to cookie value in constant time
(`hmac.compare_digest`).

### CSRF token binding

The CSRF token is bound to the current refresh session:
`csrf_token = HMAC-SHA256(CSRF_SECRET, refresh_jti)`.
`CSRF_SECRET` is an application secret (distinct from JWT keys).
A CSRF token from one session cannot be used with another session's refresh
cookie.

### Origin validation

For all cookie-authenticated state-changing requests, the backend also
validates the `Origin` header against `ALLOWED_ORIGINS`. A missing or
mismatched origin returns 403. This is a defence-in-depth measure alongside
the CSRF token.

### CSRF token rotation

The CSRF token is rotated (new value, new cookie) on:
- Successful org selection (step 2)
- Successful token refresh

### Endpoints requiring CSRF protection

All state-changing requests where a cookie could be in play require CSRF:
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `POST /api/v1/auth/logout-all`
- Any other state-changing endpoint reached with a cookie-backed session

Non-cookie endpoints (Bearer token only) do not require the CSRF header.

### Logout

1. Validate CSRF token and Origin.
2. Revoke the refresh token family server-side.
3. Clear refresh cookie: `Set-Cookie: refresh_token=; Path=/api/v1/auth;
   Max-Age=0; HttpOnly; Secure; SameSite=Lax`.
4. Clear CSRF cookie: `Set-Cookie: csrf_token=; Path=/; Max-Age=0;
   Secure; SameSite=Lax`.
5. Frontend discards in-memory access token.

Cookie-clearing uses the exact same attributes as the original Set-Cookie
(same Path, same domain, same SameSite) to ensure the browser deletes them.

---

## Role and Permission Model

### Roles (seven total)

| Role | Scope | Description |
|------|-------|-------------|
| `owner` | Organisation | Full control; sole owner of the org; can transfer ownership |
| `administrator` | Organisation | Full control within org; cannot transfer ownership or delete org |
| `workflow_builder` | Workspace | Create, edit, publish, test workflows; cannot manage members |
| `analyst` | Workspace | Run workflows; query data; read reports; cannot write data |
| `operator` | Workspace | Run approved workflows; approve low-risk actions; limited queries |
| `viewer` | Workspace | Read-only access to reports and executions |
| `auditor` | Workspace | Read-only access to audit log and execution traces; no operational access |

Ordinary org members (null `org_role`) are not a named role. They need a
workspace role to access workspace resources.

The full matrix lives in `backend/app/auth/permissions.py`.

---

## Authentication and Token Design

### Password hashing

- Library: `pwdlib[argon2]` (Argon2id variant)
- Pepper: `ARGON2_PEPPER` prepended before hashing; `ARGON2_PEPPER_VERSION` tracks rotation
- Re-hash on next login after pepper rotation

### Access tokens (JWT)

- Library: `PyJWT`; algorithm HS256; `JWT_SECRET_KEY` ≥ 64 random bytes
- Claims: `sub`, `jti`, `org`, `exp`, `iat`, `type=access`; lifetime 15 minutes
- `org` claim re-verified against DB on every request

### Refresh tokens

Family design with rotation and reuse detection. Schema:
```
id, family_id, jti, user_id, organisation_id, session_id,
token_hash (BLAKE2b(REFRESH_TOKEN_PEPPER + raw)), issued_at,
expires_at, used_at, revoked_at, revocation_reason
```

### Browser token storage

| Token | Storage | Transport |
|-------|---------|-----------|
| Access token (15 min) | JavaScript memory | `Authorization: Bearer` header |
| Refresh token | `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth` cookie | Sent by browser to `/api/v1/auth` endpoints only |
| CSRF token | JS-readable `Secure; SameSite=Lax; Path=/` cookie | Read by frontend; sent as `X-CSRF-Token` header |

No token is stored in localStorage or sessionStorage.

### Refresh cookie attributes

```
Name:     refresh_token
Value:    <raw refresh token>
HttpOnly: yes
Secure:   yes (production); relaxed on localhost only
SameSite: Lax
Path:     /api/v1/auth
Max-Age:  JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400
Domain:   (same as API domain; not set explicitly → browser defaults to exact host)
```

`Path=/api/v1/auth` means the browser sends this cookie to:
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `POST /api/v1/auth/logout-all`
- `POST /api/v1/auth/select-organisation` ← not used; the pre-auth cookie is used here

### CSRF cookie attributes

```
Name:     csrf_token
Value:    HMAC-SHA256(CSRF_SECRET, refresh_jti)
HttpOnly: no  (must be JS-readable)
Secure:   yes
SameSite: Lax
Path:     /
Max-Age:  same as refresh token
```

### Pre-auth cookie attributes

```
Name:     pre_auth_session
Value:    <raw pre-auth token>
HttpOnly: yes
Secure:   yes
SameSite: Lax
Path:     /api/v1/auth/select-organisation
Max-Age:  300 (5 minutes)
```

### API keys

`BLAKE2b(API_KEY_PEPPER + raw_key)` stored. Plaintext returned at creation only.

### Invitation tokens

`SHA-256(INVITATION_TOKEN_PEPPER + raw_token)` stored. Single-use.

---

## Ownership Constraint Design

Deferred constraint trigger (`DEFERRABLE INITIALLY DEFERRED`) fires at commit.
`SELECT FOR UPDATE` on org row serialises concurrent transfers.
Exactly-one-owner verified at commit, not mid-transaction.

---

## Audit Persistence Design

Security-critical Phase 1A events committed in same transaction as business
operation. Login failure audit uses independent synchronous insert on separate
connection. Audit write failure propagates — never silently swallowed. Phase 8
SIEM delivery via transactional outbox. App role INSERT-only on `audit_events`.

---

## RAG Access-Control Placement

Access-control filter applied **inside** SQL queries. Unauthorised chunks never
exit the database layer.

---

## Audit Log Design

**Phase 1A minimum event types (all transactional):**

| Event | Emitted when |
|-------|-------------|
| `auth.login` | Successful login (step 1) |
| `auth.login_failed` | Failed login attempt (independent insert) |
| `auth.logout` | Explicit logout |
| `auth.token_reuse_detected` | Refresh token reuse detected |
| `auth.pre_auth_session_expired` | Expired pre-auth session presented |
| `auth.pre_auth_session_reused` | Consumed pre-auth session presented again |
| `org.created` | Organisation created |
| `org.membership_added` | User added to organisation |
| `org.membership_removed` | User removed from organisation |
| `org.role_changed` | Org-level role changed |
| `org.ownership_transferred` | Ownership transfer completed |
| `org.organisation_selected` | Org selected (step 2 success) |
| `workspace.created` | Workspace created |
| `workspace.membership_added` | User added to workspace |
| `workspace.membership_removed` | User removed from workspace |
| `workspace.role_changed` | Workspace-level role changed |

---

## Credential Matrix

REPLACE_* placeholder values are rejected by settings validation at startup.

| Credential | Required when | Startup behaviour |
|------------|--------------|-------------------|
| `DATABASE_URL` | Always | Hard fail |
| `REDIS_URL` | Phase 1A+ | Hard fail |
| `JWT_SECRET_KEY` | Phase 1A+ | Hard fail if REPLACE_* |
| `ARGON2_PEPPER` | Phase 1A+ | Hard fail if REPLACE_* |
| `ARGON2_PEPPER_VERSION` | Phase 1A+ | Hard fail if absent |
| `REFRESH_TOKEN_PEPPER` | Phase 1A+ | Hard fail if REPLACE_* |
| `CSRF_SECRET` | Phase 1A+ | Hard fail if REPLACE_* |
| `PRE_AUTH_SESSION_PEPPER` | Phase 1A+ | Hard fail if REPLACE_* |
| `API_KEY_PEPPER` | Phase 1B (api_keys enabled) | Hard fail when module enabled |
| `INVITATION_TOKEN_PEPPER` | Phase 1B (invitations enabled) | Hard fail when module enabled |
| `ENCRYPTION_KEY` | Phase 2 (connectors enabled) | Hard fail when module enabled |
| `ENCRYPTION_KEY_VERSION` | Phase 2+ | Hard fail when module enabled |
| `MCP_SERVER_SECRET` | Phase 6 (MCP enabled) | Hard fail when module enabled |
| `OPENAI_API_KEY` | Phase 2+ optional | Mock provider used if absent |
| `ANTHROPIC_API_KEY` | Phase 2+ optional | Mock provider used if absent |
| `GEMINI_API_KEY` | Phase 2+ optional | Mock provider used if absent |
| `SMTP_HOST` / `SMTP_PASSWORD` | Phase 5+ | Mock mailer if absent |
| `OTEL_EXPORTER_ENDPOINT` | Phase 8+ optional | Stdout exporter if absent |

---

## Mock-Mode Matrix

| Feature | `LLM_PROVIDER=mock` behaviour |
|---------|-------------------------------|
| Chat completion | Deterministic canned response keyed on prompt hash |
| Structured output | Valid hardcoded schema-conforming object |
| Embeddings | Stable unit vector seeded by content hash |
| Reranking | Input list reversed (deterministic) |
| Moderation | Always `{"flagged": false}` |
| SQL execution | Runs against seeded local analytics database |
| Knowledge retrieval | Returns pre-indexed seed documents |
| Tool execution (read) | Executes against seed data |
| Tool execution (write) | Logs to stdout, returns `{"status": "mock_executed"}` |
| Email sending | Writes to `mock_mail/` directory |
| Approval | No auto-approve; must go through approval flow even in dev |

---

## Phase Acceptance Criteria

### Phase 0 — Foundation documents

- [x] PLAN.md created and corrected through revision 3
- [x] TASKS.md created and corrected through revision 3
- [x] README.md created and corrected
- [x] .env.example created and corrected
- [x] .gitignore created
- [x] .nvmrc created (Node 24)
- [x] .python-version created (3.12)
- [x] LICENSE created (MIT)
- [x] SECURITY.md created and corrected
- [x] docs/ARCHITECTURE.md created and corrected
- [x] docs/ADR.md created and corrected
- [x] docs/SECURITY.md created and corrected
- [x] uv.lock and package-lock.json stubs removed; generated in Phase 1A

### Phase 1A — Core multi-tenant foundation

- [ ] `pyproject.toml`: `requires-python = ">=3.12,<3.13"`, `pwdlib[argon2]`, `PyJWT`, no passlib, no python-jose
- [ ] `uv.lock` generated by `uv lock` and committed
- [ ] `package-lock.json` generated by `npm install` and committed
- [ ] `backend/app/auth/permissions.py` defines all 7 roles and full permission matrix; no DB role tables
- [ ] `organisations` has no `owner_id`; ownership via `org_role = 'owner'` membership row
- [ ] `organisation_memberships.org_role` is nullable (`owner | administrator | NULL`)
- [ ] `workspaces` has `UNIQUE(id, organisation_id)` composite constraint
- [ ] `workspace_memberships` uses `FOREIGN KEY (workspace_id, organisation_id) REFERENCES workspaces(id, organisation_id)`
- [ ] `pre_auth_sessions` table: session_hash (SHA-256 of raw token), user_id, expires_at (5 min), consumed_at
- [ ] Deferred constraint trigger enforces exactly-one-owner at commit
- [ ] Ownership transfer uses `SELECT FOR UPDATE` on the org row
- [ ] Alembic migration: all tables + composite FK + RLS DDL (single permissive FOR ALL with USING + WITH CHECK + NULLIF null guard) + deferred trigger + BYPASSRLS revocation
- [ ] `set_config('app.current_organisation_id', ...)` in every org-scoped transaction
- [ ] `set_config('app.current_user_id', ...)` after global JWT verification (pre-org-selection)
- [ ] `POST /api/v1/auth/login`: verify credentials; create pre-auth session (hash stored); set pre-auth cookie; return org list; no JWT
- [ ] `POST /api/v1/auth/select-organisation`: derive user_id from pre-auth cookie (never from body); verify membership; consume session atomically; issue JWT + refresh + CSRF; clear pre-auth cookie
- [ ] Refresh cookie: `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth`
- [ ] CSRF cookie: `Secure; SameSite=Lax; Path=/` (JS-readable; not HttpOnly)
- [ ] CSRF token bound to refresh session via HMAC-SHA256(CSRF_SECRET, refresh_jti)
- [ ] X-CSRF-Token header validated in constant time against CSRF cookie on all state-changing cookie-authenticated requests
- [ ] Origin header validated against ALLOWED_ORIGINS on all cookie-authenticated state-changing requests
- [ ] CSRF token rotated on org selection and token refresh
- [ ] Logout: validate CSRF; revoke refresh family; clear refresh cookie and CSRF cookie with exact original attributes; frontend discards access token
- [ ] CSRF required on: POST /auth/refresh, POST /auth/logout, POST /auth/logout-all
- [ ] Per-request org membership re-verification on every authenticated request
- [ ] Access token in memory; never in localStorage or sessionStorage
- [ ] Settings validation rejects REPLACE_* placeholders for CSRF_SECRET, PRE_AUTH_SESSION_PEPPER
- [ ] Audit events: 16 event types; transactional except auth.login_failed (independent); auth.pre_auth_session_expired and auth.pre_auth_session_reused also via independent inserts
- [ ] Cross-tenant RLS tests: SELECT, INSERT, UPDATE (existing row), UPDATE (change org_id), DELETE, absent context (≥ 20 scenarios total)
- [ ] Pre-auth session tests: missing cookie, expired, reused, org not belonging to user, user_id injection, successful selection
- [ ] CSRF tests: missing header, mismatched header, valid header; missing Origin, mismatched Origin; token rotation on refresh
- [ ] Multi-org login test; stale membership 403 test; concurrent ownership transfer test
- [ ] Composite FK rejection test; placeholder secret rejection test; transactional audit test
- [ ] FastAPI starts; health + readiness endpoints pass
- [ ] Docker Compose: postgres, redis, backend all healthy
- [ ] Frontend: login (step 1) and org-selector (step 2) render; auth flow functional
- [ ] ruff, mypy --strict, pytest ≥ 80% coverage on Phase 1A modules; pip-audit clean

### Phase 1B — Extended organisation management

- [ ] `invitations` model: token stored as SHA-256 hash
- [ ] Invitation flow: create, email (mock), accept by hash lookup, single-use enforcement
- [ ] Teams, service accounts, API keys, membership admin UI, org/workspace selector

### Phase 2 — Knowledge system

#### Phase 2A — Secure Enterprise Knowledge Foundation ✓ COMPLETE

- [x] Migration 0003: 6 knowledge tables with FORCE RLS, NULLIF fail-closed, composite FK
- [x] BlobStore: UUID-only key pattern, symlink root rejection, atomic write, path traversal defence
- [x] PlainTextParser, MarkdownParser (UNTRUSTED DATA model)
- [x] TextChunker: deterministic word-boundary split, SHA-256 per chunk
- [x] DeterministicTestEmbeddingProvider: no network, no API keys
- [x] KnowledgeService: upload_document transaction boundary, ingestion state machine, idempotency
- [x] 11 REST endpoints (sources CRUD, document upload/archive, versions, jobs) — no Phase 2B endpoints
- [x] RBAC: 6 Phase 2A permissions (KNOWLEDGE_READ, KNOWLEDGE_SOURCE_CREATE, KNOWLEDGE_SOURCE_UPDATE, KNOWLEDGE_DOCUMENT_UPLOAD, KNOWLEDGE_DOCUMENT_ARCHIVE, KNOWLEDGE_INGESTION_RETRY)
- [x] 96 tests across 6 suites (BlobStore, Parsers, Chunker, Embeddings, KnowledgeService, RLS)
- [x] Frontend knowledge admin page (workspace selector, source CRUD, document upload/archive, ingestion status, retry)
- [x] GLOBAL_EVENT_TYPES unchanged (exactly 4); 7 knowledge events in ALL_EVENT_TYPES only

#### Phase 2B — Retrieval and RAG (backlog)

- [ ] Vector similarity search with pgvector
- [ ] Hybrid retrieval (BM25 + HNSW)
- [ ] Injection scan before system prompt construction
- [ ] Citations and retrieved context
- [ ] Chat / ask / query endpoints

### Phases 3–9 — Planned; detailed breakdown before each phase begins

---

## Development Risks

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| RLS policy allows cross-tenant INSERT/UPDATE | Medium | FOR ALL policy with WITH CHECK; explicit tests for each DML operation |
| Absent RLS context grants access | Medium | NULLIF null guard; absent context = NULL comparison = policy denies |
| Pre-auth session reuse allows org impersonation | Low | Atomic consumed_at set; verified-one-row-affected check |
| user_id injection via org-selection body | Low | user_id derived from pre-auth session only, never from request |
| CSRF double-submit bypassed by XSS | Medium | Rotate CSRF on session change; Origin validation as defence-in-depth |
| Refresh cookie sent to wrong endpoints | Low | Path=/api/v1/auth limits cookie scope |
| Concurrent ownership transfer race condition | Low | SELECT FOR UPDATE + deferred trigger |
| Audit event lost on crash | Medium | In-transaction audit for all Phase 1A security-critical events |
| Stale JWT org claim | Medium | Per-request DB membership check |
| LangGraph API changes | Medium | Thin adapter; Postgres-queue fallback |
| Injection scan false-negative | High | Scan is advisory; tool registry is primary |
| Refresh token theft | Medium | Token-family reuse detection |
| Denial-of-wallet | Medium | Per-execution cost budget |
| Secret leakage in logs | Medium | Redaction middleware; SecretStr |
| REPLACE_* in production | Medium | Settings validation hard-fail at startup |
| Dependency vulnerabilities | Medium | Pinned lockfiles; pip-audit in CI |
