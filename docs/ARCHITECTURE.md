# AtlasCore — System Architecture

> **Version:** 0.4.0 — Phase 2B complete
> **Last updated:** 2026-08-05

---

## 1. System overview

AtlasCore is a multi-tenant enterprise AI operations platform. It enables
organisations to securely connect knowledge sources and company databases,
then use controlled AI agents to answer questions, analyse data and execute
audited workflows with human approval on write actions.

```
┌────────────────────────────────────────────────────────────────────┐
│                         AtlasCore Platform                          │
│                                                                      │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐   │
│  │   Frontend   │   │  Backend API │   │   Background Worker  │   │
│  │ Next.js 16.2 │◄──│  FastAPI     │──►│        ARQ           │   │
│  │  Node 24 LTS │   │  Python 3.12 │   │   Python 3.12        │   │
│  └──────────────┘   └──────┬───────┘   └──────────────────────┘   │
│                             │                                        │
│         ┌───────────────────┼───────────────────────┐              │
│         │                   │                       │              │
│  ┌──────▼──────┐   ┌───────▼──────┐   ┌───────────▼───────────┐  │
│  │  PostgreSQL  │   │    Redis 7   │   │    MCP Server          │  │
│  │  16+pgvector │   │  key prefixes│   │    Python 3.12         │  │
│  │  RLS enabled │   │  single inst │   │    Phase 6             │  │
│  └─────────────┘   └──────────────┘   └───────────────────────┘  │
│                                                                      │
└────────────────────────────────────────────────────────────────────┘
```

---

## 2. Architectural layers

### 2.1 Transport layer (FastAPI routers)

Routers contain only:
- Input deserialization (Pydantic v2)
- Authentication dependency resolution (`get_current_user`)
- Permission dependency resolution (`require_permission`)
- Service method invocation
- Response serialization

No business logic, no database queries, no model calls. Routers are thin.

### 2.2 Service layer

Services contain all business logic:
- Resolve effective permissions (org membership → workspace membership → permission matrix)
- Enforce organisation scoping on every operation
- Call the policy engine before any sensitive action
- Orchestrate agent engine, knowledge system, analytics layer and tool registry
- Emit audit events **transactionally** with every security-critical operation
- Return typed Pydantic objects

### 2.3 Data access layer (SQLAlchemy 2 async)

Repositories wrap all database access. Three isolation layers operate simultaneously.

**Layer 1 — PostgreSQL Row-Level Security (strengthened)**

Every tenant-scoped table has RLS enabled with `FORCE ROW LEVEL SECURITY`.
Each table carries a single default permissive `FOR ALL` policy with both
`USING` and `WITH CHECK` clauses, both using a fail-closed null guard.

Two policy variants exist depending on the table:

**Organisation-isolation policy** (Phase 1A/1B tables) — checks
`organisation_id` only:

```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <table> FORCE ROW LEVEL SECURITY;

CREATE POLICY <table>_tenant_isolation ON <table>
AS PERMISSIVE FOR ALL TO atlascore
USING (
    organisation_id = NULLIF(
        current_setting('app.current_organisation_id', true), ''
    )::uuid
)
WITH CHECK (
    organisation_id = NULLIF(
        current_setting('app.current_organisation_id', true), ''
    )::uuid
);
```

**Workspace-isolation policy** (Phase 2A knowledge tables) — checks BOTH
`organisation_id` AND `workspace_id`. This is required because the composite
FK `(workspace_id, organisation_id) → workspaces(id, organisation_id)` only
prevents referential inconsistency; it does NOT prevent a session with the
correct organisation context from seeing rows belonging to a different workspace
within the same organisation. The RLS policy enforces workspace isolation at
the database level:

```sql
CREATE POLICY <table>_workspace_isolation ON <table>
AS PERMISSIVE FOR ALL TO atlascore
USING (
    organisation_id = NULLIF(
        current_setting('app.current_organisation_id', true), ''
    )::uuid
    AND
    workspace_id = NULLIF(
        current_setting('app.current_workspace_id', true), ''
    )::uuid
)
WITH CHECK (
    organisation_id = NULLIF(
        current_setting('app.current_organisation_id', true), ''
    )::uuid
    AND
    workspace_id = NULLIF(
        current_setting('app.current_workspace_id', true), ''
    )::uuid
);
```

**Fail-closed behaviour for the workspace predicate:**

| `app.current_workspace_id` value | Outcome |
|----------------------------------|---------|
| absent or `''`                   | NULLIF → NULL → zero rows |
| a UUID that does not match       | zero rows |
| correct workspace UUID           | own workspace rows only |
| correct workspace, wrong org     | zero rows (both predicates must match) |

**Policy clause semantics:**
- `USING` governs which rows are visible to SELECT and DELETE, and which
  existing rows may be targeted by UPDATE. A row whose context does not
  match the session GUCs is invisible and un-deletable.
- `WITH CHECK` governs what values INSERT and UPDATE may write. An INSERT or
  UPDATE that would produce a row with mismatched context is rejected.
- `NULLIF(..., '')::uuid` returns NULL when the setting is absent or empty.
  A NULL comparison always evaluates to NULL (not true), so absent context
  makes both clauses fail **closed** — no access is granted.

**Why a single permissive `FOR ALL` policy (not `AS RESTRICTIVE`):**
PostgreSQL requires at least one applicable permissive policy to allow access;
restrictive policies use AND logic on top of permissive ones. A table with
only restrictive policies and no permissive policy always denies all access.
The standard tenant-isolation pattern is a single default permissive policy.

The application database role is denied `BYPASSRLS`. Only the privileged
migration role (never used at runtime) may bypass RLS. `FORCE ROW LEVEL
SECURITY` ensures the table owner cannot bypass the policy.

Three GUC variables are used:

**`app.current_organisation_id`** — all tenant-scoped tables. Set by
`OrganisationScopedSession` inside every transaction.

**`app.current_workspace_id`** — Phase 2A knowledge tables. Set by
`OrganisationScopedSession` when a workspace_id is supplied. When absent
(None), set to `''` which NULLIF maps to NULL, causing the workspace RLS
predicate to hide all rows (fail-closed).

**`app.current_user_id`** — `organisation_memberships` (SELECT only), used
during the login flow before an organisation is selected. This allows a user
to list their available organisations without granting access to any
tenant-scoped resource. This is a separate permissive `FOR SELECT` policy;
absent user context fails closed for this policy too.

```sql
-- Set after global JWT verification, before org selection:
SELECT set_config('app.current_user_id', :user_id, true);

-- organisation_memberships user-context policy (permissive FOR SELECT):
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

All three GUC variables are set within each transaction before any DML, using
`is_local=true` (transaction scope):

```python
# OrganisationScopedSession — immediately after BEGIN:
await session.execute(
    text("SELECT set_config('app.current_organisation_id', :org_id, true)"),
    {"org_id": str(organisation_id)},
)
await session.execute(
    text("SELECT set_config('app.current_user_id', :user_id, true)"),
    {"user_id": user_id_str},
)
await session.execute(
    text("SELECT set_config('app.current_workspace_id', :ws_id, true)"),
    {"ws_id": workspace_id_str},  # '' when workspace_id is None
)
# true = transaction-scoped; cleared automatically on COMMIT/ROLLBACK
```

All three values are derived from the verified JWT claim and confirmed DB
membership checks — never from the request body.

**Layer 2 — Explicit repository predicates**

Every repository method appends an explicit ORM filter:

```python
stmt = select(Document).where(Document.organisation_id == organisation_id)
```

This is independent of RLS. If RLS is misconfigured on one table, the predicate
still applies. Both must fail simultaneously for a cross-tenant leak.

**Layer 3 — SQLAlchemy with-criteria / loader criteria**

A session-level event listener registers `with_loader_criteria` on all
tenant-scoped models so that relationships loaded via lazy or joined loading
also carry the organisation filter:

```python
@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_criteria(execute_state):
    if execute_state.is_select:
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                TenantMixin,
                lambda cls: cls.organisation_id == _current_org_id(),
                include_aliases=True,
            )
        )
```

**Non-tenant tables** — `users` is a global resource. RLS is not applied.
Service-layer access control governs who may read user records.

**RBAC source of truth** — `backend/app/auth/permissions.py` defines the full
permission matrix as Python enums and a dict. No `roles`, `permissions`, or
`role_permissions` tables exist. Membership rows store role enum values.

Alembic manages all schema changes, including RLS DDL and the deferred
ownership trigger.

### 2.4 Agent execution layer

The execution engine is a typed graph:

```
authenticate → authorise → normalise request → classify objective
→ load workspace policy → construct typed plan → validate plan
→ retrieve evidence (access-controlled) → execute approved read tools
→ generate proposed actions → run safety checks
→ pause for approval (if required)
→ execute approved write tools → verify result
→ generate cited response → store trace + audit events
```

LangGraph is used only for the pause/resume and checkpointing steps where
durable execution state provides genuine value. All business authorisation is
performed in AtlasCore services before entering LangGraph.

### 2.5 Policy engine

Deterministic rule evaluation, separate from model prompts:
- Input: role, tool, data sensitivity, workspace, model provider, action risk
- Output: allow | deny | require_approval | require_redaction | escalate
- Input checks: prompt injection patterns, sensitive data, malicious arguments
- Output checks: ungrounded claims, sensitive leakage, unsafe tool arguments

Injection scanning in the policy engine is an **advisory layer**. It reports
findings and may trigger review or reduced capabilities, but it is not a
reliable security boundary against all injection techniques. Deterministic
authorisation, tool registry enforcement, and access-controlled retrieval are
the primary defences.

---

## 3. Multi-tenancy and membership model

### Tenancy hierarchy

```
Organisation (global root — not itself tenant-scoped)
  ├── organisation_memberships  (user ↔ org; org_role: owner | administrator | null)
  └── Workspace (1..N per org — tenant-scoped)
        ├── workspace_memberships  (user ↔ workspace; role: one of 5 workspace roles)
        ├── Knowledge base (tenant-scoped)
        ├── Data connections (tenant-scoped)
        ├── Workflows (tenant-scoped)
        └── Audit log (workspace-scoped view)
```

### Membership design

**`organisation_memberships`** — one row per user per organisation.
`org_role` is nullable: `owner | administrator | NULL`.
NULL means an ordinary organisation member with no org-level privileges.
Every workspace member must also have an `organisation_memberships` row.

```
organisation_id   FK → organisations  NOT NULL
user_id           FK → users          NOT NULL
org_role          organisation_role   NULLABLE  -- owner | administrator | NULL
invited_by        FK → users          NULLABLE
joined_at         TIMESTAMPTZ         NOT NULL
PRIMARY KEY (organisation_id, user_id)
```

**`workspace_memberships`** — one row per user per workspace.
The composite FK ensures `workspace_id` and `organisation_id` are consistent.

```
organisation_id   FK → organisations  NOT NULL
workspace_id                          NOT NULL  -- part of composite FK
user_id           FK → users          NOT NULL
role              workspace_role      NOT NULL  -- workflow_builder | analyst | operator | viewer | auditor
invited_by        FK → users          NULLABLE
joined_at         TIMESTAMPTZ         NOT NULL
PRIMARY KEY (workspace_id, user_id)
FOREIGN KEY (workspace_id, organisation_id)
    REFERENCES workspaces(id, organisation_id)  -- composite FK
```

`workspaces` carries `UNIQUE(id, organisation_id)` to support this composite FK.
This pattern applies to every workspace-owned table (documents, workflows, etc.).

### Permission resolution

1. Verify JWT; extract `user_id`. Reject expired or invalid tokens.
2. Fetch `organisation_memberships` row for `(user_id, organisation_id)`.
   Not found → 403.
3. If `org_role = 'owner'` or `'administrator'` → use org-level permission set
   from `permissions.py`. Skip to step 6.
4. `org_role` is NULL. Fetch `workspace_memberships` row for `(user_id, workspace_id)`.
5. Not found → 403. Ordinary members without workspace membership have no access.
6. Look up permission set for the role in `permissions.py`.
7. Apply policy engine decision.

### Ownership — single source of truth

`organisations` has no `owner_id` column. Ownership is the
`organisation_memberships` row with `org_role = 'owner'`. Exactly one such row
must exist per organisation at all times, enforced by a **deferred database
constraint trigger**.

Ownership transfer locks the organisation row with `SELECT FOR UPDATE`, then
promotes the new owner and demotes the old owner in one transaction. The
deferred trigger verifies exactly-one-owner at commit.

---

## 4. Authentication and organisation-selection flow

### Step 1 — Global login (pre-auth session issued)

`POST /api/v1/auth/login` — verifies credentials against `users`. Creates a
single-purpose pre-authentication session: generates a 32-byte random token,
stores `SHA-256(PRE_AUTH_SESSION_PEPPER + raw_token)` in `pre_auth_sessions`
with a 5-minute expiry and `consumed_at = NULL`. Sets the raw token as an
`HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth/select-organisation;
Max-Age=300` cookie (`pre_auth_session`). Returns the user's available
organisations in the response body. No JWT is issued at this step.

The pre-auth session cookie is scoped narrowly to the select-organisation
endpoint so it cannot be replayed anywhere else.

### Step 2 — Organisation selection (JWT issued, pre-auth session consumed)

`POST /api/v1/auth/select-organisation` — reads the `pre_auth_session` cookie,
hashes it with the pepper, looks up the hash in `pre_auth_sessions`:

1. Not found → 401 (invalid session).
2. `expires_at < now()` → 401; emit `auth.pre_auth_session_expired` audit event
   via independent connection.
3. `consumed_at IS NOT NULL` → 401; emit `auth.pre_auth_session_reused` audit
   event via independent connection. Potential session theft — log for review.
4. Atomically set `consumed_at = now()` via `UPDATE … WHERE consumed_at IS NULL
   RETURNING *`. If zero rows returned → treat as already consumed (race
   condition) → 401.
5. Derive `user_id` from the session row. The request body contains only
   `organisation_id`; no `user_id` field is accepted. A `user_id` field in the
   request body is ignored entirely. This prevents user_id injection.
6. Verify `organisation_memberships` row for `(user_id, organisation_id)`.
7. Issue org-scoped access token, refresh token, and CSRF token.
8. Clear the `pre_auth_session` cookie (`Max-Age=0`, same attributes).

### Cookie attributes after successful org-selection

```
Refresh token cookie:
  Name:     refresh_token
  HttpOnly: yes           (not readable by JavaScript)
  Secure:   yes
  SameSite: Lax
  Path:     /api/v1/auth  (covers refresh, logout, logout-all)
  Max-Age:  JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400

CSRF token cookie:
  Name:     csrf_token
  HttpOnly: no            (must be readable by JavaScript)
  Secure:   yes
  SameSite: Lax
  Path:     /             (accessible from all frontend routes)
  Max-Age:  same as refresh token
```

### CSRF protection — double-submit cookie pattern

The CSRF token is bound to the refresh session:
`HMAC-SHA256(CSRF_SECRET, refresh_jti)`.

- The backend sets this value in the `csrf_token` cookie (not HttpOnly, so
  JavaScript can read it).
- The frontend reads the `csrf_token` cookie value and attaches it as the
  `X-CSRF-Token` header on all state-changing requests.
- The backend compares the header value to the cookie value in constant time
  using `hmac.compare_digest`. Mismatch → 403.
- Missing header or missing cookie → 403.
- The `Origin` header is also validated on all cookie-authenticated
  state-changing requests. Unexpected origin → 403.

CSRF token is rotated (new cookie set) on:
- Successful org-selection
- Successful token refresh

CSRF is required on: `POST /auth/refresh`, `POST /auth/logout`,
`POST /auth/logout-all`.

### Logout

`POST /auth/logout` and `POST /auth/logout-all` both:
1. Validate CSRF header.
2. Revoke refresh-token family (or all families).
3. Clear the `refresh_token` cookie (`Max-Age=0`, same `Path=/api/v1/auth`
   and other attributes as when it was set).
4. Clear the `csrf_token` cookie (`Max-Age=0`, same `Path=/` and attributes).
5. Return success; frontend discards the in-memory access token.

### Per-request membership verification

On every authenticated request, after verifying the JWT, the server re-fetches
the `organisation_memberships` row for `(user_id, org_claim)`. If the row is
gone (membership revoked), the request returns 403 immediately — regardless of
token expiry. The `org` claim in the JWT is never trusted without a current DB
check.

### Browser token storage

| Token | Storage | Sent via |
|-------|---------|----------|
| Access token (15 min) | JavaScript memory | `Authorization: Bearer` header |
| Refresh token | HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth cookie | Automatic (cookie) |
| CSRF token | JS-readable; Secure; SameSite=Lax; Path=/ cookie | `X-CSRF-Token` header |

No token is stored in localStorage or sessionStorage.

### Org count edge cases

- 0 organisations: empty list returned; no JWT issued; user cannot proceed.
- 1 organisation: client may auto-select and call step 2 immediately.
- 2+ organisations: client shows picker; user selects before JWT is issued.

---

## 5. Role and permission model

### Seven roles

| Role | Scope |
|------|-------|
| `owner` | Organisation |
| `administrator` | Organisation |
| `workflow_builder` | Workspace |
| `analyst` | Workspace |
| `operator` | Workspace |
| `viewer` | Workspace |
| `auditor` | Workspace |

Ordinary organisation members (null `org_role`) are not a named role.
They need a workspace role to access workspace resources.

The permission matrix is the single authoritative source in
`backend/app/auth/permissions.py`. RBAC tests are generated from this matrix.
No `roles`, `permissions`, or `role_permissions` tables are created in Phase 1A.

---

## 6. Authentication and token design

### Password hashing

- Library: `pwdlib[argon2]` (Argon2id)
- Server-side pepper (`ARGON2_PEPPER`) prepended before hashing
- `ARGON2_PEPPER_VERSION` tracks active pepper version; supports rotation
- Hash scheme version stored per user row; re-hashed on next login if scheme changes

### Access tokens (JWT via PyJWT)

- Algorithm: HS256; `JWT_SECRET_KEY` ≥ 64 random bytes
- Claims: `sub` (user_id), `jti`, `org` (org_id), `exp`, `iat`, `type=access`
- Lifetime: 15 minutes; not stored server-side
- `org` claim re-verified against DB on every request

### Refresh token family design

The `refresh_tokens` table:

```
id              UUID PK
family_id       UUID         -- same across all rotations of one login session
jti             UUID UNIQUE  -- matches JWT jti claim
user_id         FK → users
organisation_id FK → organisations
session_id      UUID         -- ties to originating session record
token_hash      VARCHAR      -- BLAKE2b(REFRESH_TOKEN_PEPPER + raw_token)
issued_at       TIMESTAMPTZ
expires_at      TIMESTAMPTZ
used_at         TIMESTAMPTZ  -- set when rotated; NULL = still valid
revoked_at      TIMESTAMPTZ  -- set on revocation or reuse detection
revocation_reason TEXT
```

Rotation: present token → validate hash + `used_at IS NULL` + `revoked_at IS NULL`
→ mark `used_at = now()` → issue new token with same `family_id`, new `jti` and hash.

Reuse detection: if `used_at IS NOT NULL` on presentation, revoke entire family
immediately (`revocation_reason = 'reuse_detected'`). User must re-authenticate.

### Invitation tokens

`SHA-256(INVITATION_TOKEN_PEPPER + raw_token)` stored. Raw token sent to invitee
only. Acceptance looks up the stored hash. Single-use (`accepted_at` set atomically).

### API keys

`BLAKE2b(API_KEY_PEPPER + raw_key)` stored. Plaintext returned at creation only.
Prefix (8 chars) stored in clear for display. Scoped to declared permissions.

---

## 7. Ownership constraint design

A deferred constraint trigger enforces exactly-one-owner at transaction commit:

```sql
CREATE CONSTRAINT TRIGGER enforce_single_owner
    AFTER INSERT OR UPDATE OR DELETE ON organisation_memberships
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_single_owner();
```

Ownership transfer uses `SELECT FOR UPDATE` to lock the organisation row and
serialise concurrent transfer attempts. The deferred trigger fires at commit
and will raise if the transfer leaves zero or two owners.

---

## 8. Audit persistence design

Security-critical audit events are committed **in the same transaction** as the
business operation. The audit row and the business change commit together or
roll back together. An audit write failure propagates and rolls back the
business operation — it is never silently swallowed.

Login failure audits, pre-auth session expiry audits, and pre-auth session
reuse audits use an independent synchronous insert on a separate connection,
since the request transaction may be rolled back in those error paths.

For Phase 8 SIEM delivery, a transactional outbox pattern is used: the audit
row is committed with the business op; a background worker delivers to the
external system asynchronously.

The application database role has INSERT on `audit_events`, no UPDATE or DELETE.
This is a database GRANT, not application logic. No soft-delete flag exists.

---

## 9. Knowledge upload pipeline (Phase 2A)

The Phase 2A knowledge pipeline handles document ingestion from upload to
stored, queryable chunk embeddings. No retrieval, search, or RAG endpoints
exist in Phase 2A — those are Phase 2B concerns.

### 9.1 Pipeline stages

```
HTTP multipart upload (UploadFile)
    │  max 50 MB enforced at FastAPI dependency level
    │  media_type checked against parser allowlist before write
    ▼
BlobStore.put()
    │  key: {org_id}/{workspace_id}/{document_id}/{version_id}  ← server-generated UUIDs only
    │  atomic write via .tmp rename
    │  SHA-256 of raw bytes computed and returned
    │  max_bytes enforced inside put() — raises BlobStoreSizeError
    │  root must not be a symbolic link (checked before resolve())
    ▼
DB transaction: document + version + ingestion_job(queued) + audit
    │  committed atomically; blob orphaned on DB failure (best-effort cleanup)
    │  storage_key NEVER returned to clients — absent from all response schemas
    ▼
_run_ingestion() — separate transaction
    │
    ├── Parser (untrusted document model)
    │     PlainTextParser : text/plain  — UTF-8 decode, latin-1 fallback,
    │                                      null-byte removal, line normalisation
    │     MarkdownParser  : text/markdown, text/x-markdown  — strips fenced
    │                        code blocks, inline code, HTML tags, headings,
    │                        bold/italic markers; images keep alt text;
    │                        links keep label text only
    │     UnsupportedMediaTypeError for all other types (application/pdf etc.)
    │     Content is UNTRUSTED DATA — parsers extract text only.
    │     Extracted text never executes, never modifies system prompts,
    │     never invokes tools, never alters permissions.
    │
    ├── TextChunker
    │     word-boundary chunking with overlap
    │     chunk_size and overlap are token counts (word approximation)
    │     overlap < chunk_size enforced at construction (ChunkerConfigError)
    │     each Chunk: chunk_index, chunk_text, content_sha256, token_count
    │     content_sha256 = SHA-256(chunk_text UTF-8) — not password hashing
    │     deterministic: identical text+config → identical output
    │
    ├── EmbeddingProvider
    │     Phase 2A: DeterministicTestEmbeddingProvider (no network, no API key)
    │     model_id = "deterministic-test-v1" — stable, persisted in DB
    │     SHA-256 seed expansion → L2-normalised float vector
    │     build_embedding_provider() rejects unknown provider names
    │
    └── DB write: knowledge_chunks + knowledge_chunk_embeddings
          ingestion_job status → succeeded / failed
          audit event emitted transactionally

```

### 9.2 Security invariants

- **Storage key isolation:** server-generated `{org}/{ws}/{doc}/{ver}` UUID path; no
  filename component; validated with UUID-only allowlist; candidate path verified
  with `Path.relative_to(root)` to prevent traversal; symlinked roots rejected.
- **Content SHA-256:** raw-bytes hash for integrity and workspace-scoped deduplication.
  SHA-256 is correct here — this is content integrity, not password storage.
- **No cross-org deduplication:** deduplication is scoped to `(organisation_id, workspace_id)`.
  Global deduplication across organisations is explicitly prohibited.
- **Secret config rejection:** `_sanitise_source_config` rejects source `configuration`
  dicts whose keys contain token/secret/password/key/api_key/credential/etc.
  substrings. OAuth tokens and API secrets must not be stored in configuration JSON.
- **Phase 2B search endpoint:** `POST /api/v1/knowledge/workspaces/{workspace_id}/search`
  is now implemented. It uses the same `ValidatedWorkspaceId` dependency as Phase 2A
  endpoints. No LLM answer generation; ranked evidence only.

### 9.3 Ingestion state machine

```
queued ──► running ──► succeeded
                  └──► failed ──► queued  (retry only from failed)
```

Retry is gated on `status == "failed"`. Only `KNOWLEDGE_INGESTION_RETRY` permission
holders may trigger a retry. Idempotency key is `UNIQUE(organisation_id, workspace_id,
idempotency_key)` — a duplicate upload within the same workspace is rejected.

---

## 10. Phase 2B — Hybrid retrieval engine (implemented)

Phase 2B delivers ranked evidence retrieval.  No LLM answer generation.

### 10.1 Retrieval pipeline

```
POST /api/v1/knowledge/workspaces/{workspace_id}/search
    │
    ▼  ValidatedWorkspaceId (3-step trust chain — same as Phase 2A)
    ▼
┌─────────────────────────────────────────────────────────────┐
│  KnowledgeRetrievalService.retrieve()                        │
│                                                              │
│  1. normalise_query()    — strip, collapse whitespace, NFC   │
│  2. EmbeddingProvider.embed()  — may fail; falls back        │
│  3. lexical_search()     — PostgreSQL plainto_tsquery        │
│     WHERE org_id + workspace_id + status='succeeded'         │
│         + is_archived + is_active + source/doc filters       │
│  4. vector_search()      — cosine similarity (Python scan)   │
│     WHERE model_id + dimensions + org_id + workspace_id      │
│         + same ready/archived/active/filter predicates       │
│  5. reciprocal_rank_fusion()   — k=60, dedup by chunk_id     │
│  6. Reranker.rerank()    — IdentityReranker in Phase 2B      │
│                                                              │
│  Returns: RetrievalResponse (ranked chunks, no answers)      │
└─────────────────────────────────────────────────────────────┘
```

### 10.2 Security invariants

- `workspace_id` is validated via `ValidatedWorkspaceId` before reaching the service.
- `organisation_id` comes exclusively from the JWT payload — never the request body.
- Both SQL modules use bound parameters only; SQL injection via query text is impossible
  (`plainto_tsquery` treats its input as plain text, not SQL).
- Unknown `source_ids` / `document_ids` from another workspace produce empty results;
  they do not disclose the existence of objects in other workspaces.
- Retrieved chunk content is **UNTRUSTED DATA**; it is never executed, never used to
  modify system prompts, and never passed to an LLM in Phase 2B.
- `storage_key`, `embedding`, and `organisation_id` are never serialised to the API response.
- The GIN index (migration 0005) supports `plainto_tsquery` without changing the SQL
  security model — it is a performance optimisation only.

### 10.3 Retrieval modules

| Module | Responsibility |
|--------|---------------|
| `app/retrieval/query.py` | Query normalisation: strip, collapse, NFC, reject empty, truncate |
| `app/retrieval/lexical.py` | PostgreSQL FTS via `plainto_tsquery` |
| `app/retrieval/vector.py` | Cosine similarity over stored JSON embeddings (exact scan) |
| `app/retrieval/hybrid.py` | RRF fusion with k=60; dedup; deterministic tie-break |
| `app/retrieval/ranking.py` | `Reranker` ABC + `IdentityReranker` (Phase 2B default) |
| `app/retrieval/service.py` | Pipeline orchestration; fallback handling; observability logging |
| `app/retrieval/schemas.py` | `RetrievalRequest`, `RetrievalResult`, `RetrievalResponse` Pydantic models |

Documents, chunks, and connectors use the composite FK pattern:
`FOREIGN KEY (workspace_id, organisation_id) REFERENCES workspaces(id, organisation_id)`.

---

## 11. Safe analytics architecture

```
Model or workflow requests analysis
        │
        ▼
┌─────────────────────┐
│  Typed tool call     │  Parameters declared in tool input schema
└──────────┬──────────┘
           ▼
┌──────────────────────┐
│  SQL Builder          │  Parameterised query from typed parameters; no free-form SQL
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  SQL Validator        │  Blocks DML, DDL, stacked statements, comment bypasses
│                        │  Enforces allowlisted tables
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  Read-only Connection │  Postgres role: SELECT only
│  Row limit + timeout  │  Hard limits enforced at query level
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  Result + Explanation │  Structured result, EXPLAIN output, chart-ready format
└──────────────────────┘
```

---

## 12. Workflow engine architecture

Node types (eleven):

| Node | Description |
|------|-------------|
| `trigger` | Manual, webhook, or scheduled entry point |
| `classify` | AI classification of input type/intent |
| `extract` | Structured extraction from unstructured text |
| `knowledge_retrieval` | Access-controlled knowledge base search |
| `sql_analysis` | Approved analytics tool execution |
| `condition` | Deterministic branch on typed value |
| `tool` | Registered tool execution |
| `human_approval` | Pause; resume only after explicit approval |
| `transform` | Pure function transformation |
| `notification` | Approved channel notification |
| `report` | Cited final report generation |

Execution guarantees: step count, tool call count, model call count, cost budget,
and timeout budget all enforced. State checkpointed in PostgreSQL after each step.
Paused state survives service restarts. Cancellation checked at every step boundary.
Durable state TTL (`WORKFLOW_STATE_TTL_SECONDS`) is longer than approval expiration
(`APPROVAL_EXPIRATION_HOURS`) so no paused execution expires before it can be approved.

---

## 13. Approval system

```
Write action proposed by agent
        │
        ▼
┌─────────────────────┐
│  Approval Request    │  tool, version, args, SHA-256(args), summary, expiry, approver
└──────────┬──────────┘
           │  workflow pauses — Postgres-backed durable state
           ▼
┌──────────────────────┐
│  Approver reviews     │  UI shows human-readable summary derived from stored args
│                        │  Approver cannot modify arguments
└──────────┬───────────┘
           │
           ├── Approve → verify current_arg_hash == stored_arg_hash → single-use → resume
           ├── Reject  → terminate execution, emit audit event
           └── Modify  → return to agent with modification note (new approval required)
```

---

## 14. Provider interface design

```python
class LLMProvider(Protocol):
    async def complete(self, request: ChatRequest) -> ChatResponse: ...
    async def complete_structured(self, request: ChatRequest, schema: type[T]) -> T: ...

class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

class RerankingProvider(Protocol):
    async def rerank(self, query: str, documents: list[str]) -> list[RankedDocument]: ...
```

Routing considers: task type, org provider policy, model availability, cost limit,
latency target, data classification. Confidential data never sent to a non-approved provider.

---

## 15. Audit log design

The audit log begins in Phase 1A with sixteen minimal event types. It expands
through Phases 2–9 as new capabilities are added. Dashboards, export and
advanced observability are added in Phase 8.

**Immutability:** Application database role has INSERT on `audit_events`, no
UPDATE or DELETE. This is a database GRANT, not application logic.

**Transactional persistence:** All Phase 1A security-critical events are written
in the same transaction as the triggering business operation.

**No soft delete.** There is no `deleted_at` or `is_deleted` flag on audit rows.

**Retention:** In development, rows are retained indefinitely. In production,
a privileged maintenance role manages retention via partition DROP or DELETE.

**Partitioning (Phase 8):** Monthly range partitions on `created_at`.

---

## 16. Observability design

Every request produces a root trace span with `request_id`, `user_id`, `org_id`,
`workspace_id`. Child spans cover every service boundary, tool call, model call,
and DB query. Structured log entries at INFO level carry the same IDs. A cost
record is created per model call.

Sensitive fields (message content, document content, credentials) are never
included in traces or metrics. Only counts, latency, and categorical metadata
are exported.

---

## 17. Deployment topology (Docker Compose — development)

```
┌──────────────────────────────────────────────────────┐
│  Docker network: atlascore_net                        │
│                                                        │
│  postgres:5432   (pgvector/pgvector:pg16 image)       │
│  redis:6379      (single instance, key prefixes)      │
│  backend:8000    (FastAPI + uvicorn, Python 3.12)     │
│  worker:—        (ARQ background tasks)               │
│  frontend:3000   (Next.js 16.2.x, Node 24)           │
│  mcp_server:9000 (MCP protocol, Phase 6)             │
└──────────────────────────────────────────────────────┘
```

Production additions (Phase 9): TLS termination, read replica for analytics,
cloud secret manager, container image scanning in CI.

---

## 18. Phase 2C — Grounded answering architecture

Phase 2C adds a grounded Q&A pipeline on top of the Phase 2B retrieval layer.
The pipeline is strictly layered; each boundary enforces a hard security constraint.

### 18.1 Pipeline stages

```
User question (untrusted)
  │
  ├─ 1. Normalise question (strip/collapse whitespace, enforce 2000-char cap)
  │      → question_norm (still untrusted, never placed in system instructions)
  │
  ├─ 2. Phase 2B retrieval (OrganisationScopedSession, RLS-enforced)
  │      → list[RetrievalResult]
  │
  ├─ 3. Build EvidencePacket
  │      → server-assigned E1/E2/… IDs
  │      → injection flag detection (heuristic, 16 patterns)
  │      → evidence confidence band (HIGH/MEDIUM/LOW/NONE)
  │        Formula: 0.40·top_score + 0.25·diversity + 0.20·agreement + 0.15·breadth
  │                 − 0.20·suspicious_fraction
  │        THIS IS DETERMINISTIC — not model/LLM confidence.
  │
  ├─ 4. EvidenceSufficiencyPolicy (deterministic, no LLM)
  │      NONE band or empty   → ABSTAIN_NO_EVIDENCE  (AnswerProvider NEVER called)
  │      LOW band + require_medium → ABSTAIN_WEAK_EVIDENCE
  │      MEDIUM or HIGH       → proceed
  │
  ├─ 5. PromptBuilder
  │      Trusted system instructions (hardcoded) + untrusted evidence in
  │      structurally separate <EVIDENCE id="En"> blocks.
  │      Evidence is labelled UNTRUSTED DATA in the prompt header.
  │      question_norm placed only in the QUESTION section at the end.
  │
  ├─ 6. AnswerProvider.generate() (DeterministicTestAnswerProvider / real LLM)
  │      Returns ProviderAnswer { answer_text, citation_ids: ["E1", …] }
  │      Provider output is NEVER trusted directly.
  │
  ├─ 7. CitationValidator
  │      Each provider-returned ID must:
  │        a. Match ^E\d+$ pattern
  │        b. Exist in the current EvidencePacket (prevents E999 / stale IDs)
  │      All citation metadata comes from server-controlled EvidenceItems.
  │      Provider-supplied source names/URLs are NEVER used.
  │
  ├─ 8. rewrite_citations_in_answer
  │      [E1] → [1], [E2] → [2], unknown Ids → removed
  │
  └─ 9. GroundedAnswerResponse
         status / answer_text / citations / evidence_band / limitations
```

### 18.2 Security guarantees

- **AnswerProvider is never called with zero evidence.** Sufficiency check is
  deterministic and runs before the provider.
- **Provider failures are safe.** AnswerProviderError and unexpected exceptions
  are caught; the caller receives a generic "Unable to generate" message. No API
  keys, stack traces, or system prompt content are ever surfaced.
- **Fabricated citation IDs are rejected.** CitationValidator cross-checks every
  ID against the live EvidencePacket. IDs from other requests or sessions are
  implicitly rejected because the EvidencePacket is per-request.
- **Citation provenance is server-controlled.** Source name, document title, and
  all provenance fields come from EvidenceItem (server-assigned), never from the
  provider response.
- **Evidence is UNTRUSTED DATA.** Prompt injection heuristics flag suspicious
  content; the system prompt instructs the provider to treat all evidence as
  quoted data to read and cite — never to follow instructions found within it.
- **General LLM knowledge is forbidden.** The system prompt explicitly prohibits
  fallback to training data.
- **storage_key is never returned.** Not in EvidenceItem, not in Citation, not
  in any API response schema.
- **Embedding vectors are never returned.** Not in any API response.

### 18.3 Module structure

```
app/answering/
  __init__.py
  evidence.py       EvidenceItem, EvidencePacket, build_evidence_packet,
                    _detect_injection_flags, _calculate_evidence_band
  sufficiency.py    EvidenceSufficiencyPolicy, SufficiencyOutcome
  prompt.py         PromptBuilder (_SYSTEM_INSTRUCTIONS hardcoded here)
  provider.py       AnswerProvider ABC, DeterministicTestAnswerProvider,
                    build_answer_provider, AnswerProviderError
  citation.py       Citation, CitationValidator, CitationValidationError,
                    rewrite_citations_in_answer
  service.py        GroundedAnswerService (pipeline orchestration)
  schemas.py        AnswerRequest, CitationResponse, GroundedAnswerResponse

tests/answering/
  test_evidence.py      20 tests — EvidencePacket, injection flags, band calc
  test_sufficiency.py   11 tests — ANSWER/ABSTAIN_*/require_medium variants
  test_citation.py      17 tests — validation, dedup, rewrite, sort order
  test_prompt.py        14 tests — structure, truncation, injection warnings
  test_provider.py      9 tests  — DeterministicTestAnswerProvider, factory
  test_service.py       13 tests — full pipeline, safe failure, citation rewrite
  evaluation_fixtures.py  10 named scenario fixtures for offline evaluation
```

### 18.4 API endpoint

```
POST /api/v1/knowledge/workspaces/{workspace_id}/answer
  Auth: JWT Bearer, Permission.KNOWLEDGE_READ
  Body: { question: str (max 2000), top_k: int (1–50, default 10) }
  Response: GroundedAnswerResponse
    status: "answer" | "abstain_no_evidence" | "abstain_weak_evidence" | "provider_failure"
    answer_text: str (grounded answer or safe abstention message)
    citations: list[CitationResponse]  (empty if abstained/failed)
    evidence_band: "high" | "medium" | "low" | "none"
    limitations: list[str]
    suspicious_count: int
```
