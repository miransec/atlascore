# AtlasCore — Architecture Decision Records

> Records are append-only. Superseded decisions carry a "Superseded by" note.
> Each record states context, options considered, decision, and consequences.
> **Version:** 0.2.1 — Phase 1B durability final fix · **Last updated:** 2026-08-05

---

## ADR-001: Python 3.12 with upper-bound pin

**Date:** 2026-08-03
**Status:** Accepted (revised)

### Context

The backend requires Python 3.12. The original `requires-python = ">=3.12"`
had no upper bound, which could allow silent installation on Python 3.13 where
some transitive dependencies may have compatibility issues during the early
build phase of this project.

### Decision

`requires-python = ">=3.12,<3.13"` in `pyproject.toml` and `.python-version`
pins `3.12`. The upper bound will be raised deliberately after 3.13 compatibility
is verified for all dependencies. The Dockerfile pins `python:3.12-slim`.

### Consequences

- uv and CI are restricted to 3.12; no accidental 3.13 installs
- Upper bound must be explicitly lifted when 3.13 support is confirmed
- All type annotations use 3.12 syntax

---

## ADR-002: PostgreSQL 16 + pgvector instead of a dedicated vector database

**Date:** 2026-08-03
**Status:** Accepted

### Context

The knowledge system requires relational storage and vector similarity search.
Options: PostgreSQL + pgvector, or PostgreSQL + a dedicated vector database.

### Options

| Option | Pros | Cons |
|--------|------|------|
| PostgreSQL + pgvector | Single DB, ACID, FK integrity, RLS applies to chunks | Lower ANN throughput at very large scale |
| PostgreSQL + Qdrant | Best ANN performance | Dual-DB, no cross-DB transactions, separate RLS model |
| PostgreSQL + Pinecone | Managed | External SaaS, data sovereignty concerns |

### Decision

**PostgreSQL 16 + pgvector.** A `VectorStoreInterface` abstraction allows
migration to Qdrant with a new implementation. RLS applies uniformly to all
tables in one engine.

### Consequences

- `pgvector/pgvector:pg16` Docker image
- HNSW index on `chunks.embedding` in Phase 2 migration
- `EMBEDDING_DIMENSIONS` configurable for future model changes

---

## ADR-003: LangGraph used only for durable pause/resume

**Date:** 2026-08-03
**Status:** Accepted

### Context

LangGraph provides durable execution state and interrupt/resume. Deep coupling
risks framework lock-in and business logic leaking into graph config.

### Decision

Custom typed graph engine; LangGraph used only for checkpoint persistence and
interrupt/resume mechanics. Business authorisation lives in AtlasCore services.
An `ExecutionEngine` interface can be satisfied by a Postgres-queue implementation.

### Consequences

- Chain-of-thought not stored; only structured plan, tool selections, and outcomes
- Business logic is testable without LangGraph
- LangGraph is an optional dependency

---

## ADR-004: Deterministic mock provider as the default AI backend

**Date:** 2026-08-03
**Status:** Accepted

### Decision

`LLM_PROVIDER=mock` is the default. Responses are keyed on a content hash.
CI, evals, and development require no credentials. All mock responses are
labelled `"provider": "mock"` in API responses and logs.

### Consequences

- No evaluation score is computed against the mock and reported as a real-provider result
- Provider interface is identical; switching is a config change

---

## ADR-005: Append-only audit log in PostgreSQL

**Date:** 2026-08-03
**Status:** Accepted (revised)

### Context

Audit records must be tamper-evident. The original design mentioned a
soft-delete flag for retention, which contradicted append-only immutability.

### Decision

**Append-only `audit_events` table in PostgreSQL.** Application database role
has INSERT, no UPDATE or DELETE (enforced by GRANT, not application logic).
No soft-delete flag. Retention in production is managed by a privileged
maintenance role via partition DROP or privileged DELETE, not by the application.
In development, rows are retained indefinitely (`AUDIT_LOG_RETENTION_DAYS=0`).
Phase 8 adds monthly range partitioning for performance.

### Consequences

- Application code cannot delete audit rows under any circumstances
- Partition maintenance requires a separate privileged role
- SIEM export supported via the auditor API

---

## ADR-006: No free-form model-generated SQL

**Date:** 2026-08-03
**Status:** Accepted

### Decision

The model cannot generate SQL. Analytics queries are built from typed parameters
by the SQL Builder. Write operations use named business tools. The SQL Validator
rejects DML/DDL even if a builder bug produces it. Three independent layers:
tool schema, SQL validator, database role.

---

## ADR-007: Pydantic v2 strict mode on tool input schemas

**Date:** 2026-08-03
**Status:** Accepted

### Decision

Tool input schemas use `model_config = ConfigDict(strict=True)`. Response schemas
use default mode for client convenience. Coercion in API deserialization is handled
in the router, not the schema.

---

## ADR-008: Next.js 16.2.x (Active LTS) on Node 24 LTS

**Date:** 2026-08-03
**Status:** Accepted (supersedes original Next.js 15 / Node implicit version)

### Context

The original plan specified Next.js 15 and did not pin Node.js. Node 24 is
the current Active LTS. Next.js 16.2.x is the Active LTS patched release.

### Decision

**Next.js 16.2.x** pinned in `package.json`. **Node 24 LTS** pinned in `.nvmrc`.
Frontend build runs inside Docker to ensure the correct Node version regardless
of the developer's local environment. `package-lock.json` is committed at the
monorepo root.

### Consequences

- `.nvmrc` contains `24`; `nvm use` activates the correct version
- `npm run build` fails fast if lockfile is out of sync
- shadcn/ui and Recharts verified compatible with Next.js 16.2.x before Phase 1A build

---

## ADR-009: pwdlib[argon2] and PyJWT replace passlib and python-jose

**Date:** 2026-08-03
**Status:** Accepted

### Context

`passlib` is no longer actively maintained. `python-jose` has known CVEs and
is also unmaintained. Enterprise-quality password and token handling requires
actively maintained libraries.

### Options

| Library | Status | Notes |
|---------|--------|-------|
| `passlib` | Unmaintained | Known compatibility issues with newer bcrypt |
| `pwdlib[argon2]` | Active | Modern; Argon2id (OWASP-recommended); supports scheme versioning |
| `python-jose` | Unmaintained | CVEs present |
| `PyJWT` | Active | Lightweight; well-maintained; used by major frameworks |
| `Authlib` | Active | More feature-rich; chosen if OAuth2 server support is needed later |

### Decision

**`pwdlib[argon2]`** for password hashing (Argon2id). **`PyJWT`** for JWT
issue and verification. `PyJWT` is sufficient for AtlasCore's auth model;
if OAuth2 server capabilities are required in a future phase, `Authlib` will
be evaluated and a new ADR written.

### Consequences

- `passlib` and `python-jose` must not appear in `pyproject.toml` or `uv.lock`
- Argon2id with OWASP parameters is the only supported password scheme
- Server-side pepper (`ARGON2_PEPPER`) is mandatory; startup fails without it

---

## ADR-010: Refresh token family design with reuse detection

**Date:** 2026-08-03
**Status:** Accepted

### Context

A simple rotate-on-use refresh token prevents replay but does not detect
token theft. If an attacker steals a refresh token, they can silently maintain
access by rotating it themselves. The legitimate user would get an invalid-token
error on their next refresh and likely just log in again, never knowing a
session was compromised.

### Decision

**Token family design.** Every refresh token belongs to a `family_id`. When a
refresh token is presented and it is already `used_at IS NOT NULL`, this indicates
either a network retry (legitimate) or a stolen token replay. Because network
retries receive a new token in the response, the client should not present the
old token again. Presenting a used token is treated as evidence of theft.

Response: immediately revoke all tokens in the family, terminate all derived
sessions, and return a 401 with `WWW-Authenticate: Bearer error="token_reuse"`.
The user must re-authenticate.

### Consequences

- Refresh token schema includes `family_id`, `jti`, `session_id`, `used_at`, `revoked_at`, `revocation_reason`
- Reuse detection adds one DB read per refresh; acceptable cost for the security benefit
- Legitimate clients that retry a refresh request receive a 401 and must re-authenticate; this is the intended behaviour

---

## ADR-011: Three-layer multi-tenancy — RLS + repository predicates + with-criteria

**Date:** 2026-08-03
**Status:** Accepted

### Context

A single application predicate is insufficient: a bug, an ORM edge case, or a
missing predicate in one repository method could silently leak cross-tenant data.

### Decision

Three independent layers:
1. **PostgreSQL RLS with FORCE ROW LEVEL SECURITY** — database rejects queries that would return another tenant's rows, regardless of application behaviour
2. **Explicit `.where(Model.organisation_id == org_id)` in every repository method** — application-level defence-in-depth
3. **SQLAlchemy `with_loader_criteria` on all tenant-scoped models** — relationship loads also carry the filter

All three layers use the same `organisation_id` value derived from the verified
JWT, never from the request body. The RLS setting context (`set_config`) is
transaction-scoped (trusting the server-side session, not a user-supplied header).

### Consequences

- RLS DDL is part of Alembic migrations; adding a new tenant-scoped table requires RLS policy in the same migration
- CI tests verify RLS by connecting with a test user and confirming cross-tenant queries return zero rows even when the application predicate is removed
- `users`, `roles`, `permissions`, `role_permissions` are not tenant-scoped; RLS not applied

---

## ADR-012: Separate organisation_memberships and workspace_memberships tables

**Date:** 2026-08-03
**Status:** Accepted

### Context

The original design had a single ambiguous `memberships` table with both
organisation-level and workspace-level roles in the same row. This created
confusion about which role applied to which scope and made permission resolution
non-deterministic.

### Decision

Two separate tables with non-overlapping role sets:
- `organisation_memberships`: roles `owner`, `administrator`
- `workspace_memberships`: roles `workflow_builder`, `analyst`, `operator`, `viewer`, `auditor`

Permission resolution is a deterministic two-step lookup.

### Consequences

- A user must have an `organisation_membership` row before they can have a `workspace_membership` row
- Removing a user from an organisation must cascade-delete their workspace memberships
- The API distinguishes org-level and workspace-level invite/remove operations

---

## ADR-013: Organisation ownership via membership row (no owner_id column)

**Date:** 2026-08-03
**Status:** Accepted

### Context

The original plan placed `owner_id` on the `organisations` table alongside an
`owner` role in memberships. This created two sources of truth for ownership,
which could diverge.

### Decision

**Single source of truth: `organisation_memberships.role = 'owner'`.**
`organisations` has no `owner_id` column. Ownership transfer is a single
atomic transaction. A DB constraint (or application-layer invariant enforced
on every role-change transaction) guarantees exactly one owner per organisation.

### Consequences

- Ownership queries are a join: `SELECT om.user_id FROM organisation_memberships om WHERE om.organisation_id = :id AND om.role = 'owner'`
- Ownership transfer endpoint is the only path that may change the owner role
- No risk of `owner_id` and membership row diverging

---

## ADR-014: RAG access-control inside retrieval queries, not as a post-filter

**Date:** 2026-08-03
**Status:** Accepted

### Context

A post-filter approach (retrieve top-K globally, then remove unauthorised chunks)
has a critical flaw: unauthorised chunks are fetched from the DB and potentially
scored, reranked, or even sent to an external reranking provider before the filter
runs. This is a data leakage risk.

### Decision

Access-control predicates are applied **inside** the SQL queries for both vector
and keyword retrieval. The `WHERE` clause includes `organisation_id`, `workspace_id`,
and the document access policy check before the `ORDER BY embedding <=>` and
`LIMIT`. Only authorised rows exit the database.

Score fusion, reranking, external provider calls, and model context assembly
operate on already-filtered, already-authorised result sets.

### Consequences

- The `KnowledgeAccessPolicy` service must generate the SQL predicate before every retrieval call
- The predicate is a parameterised ORM expression, never a string from user input
- pgvector HNSW approximate search may return slightly different results than an exact post-filter; this is an accepted trade-off for correctness and privacy

---

## ADR-015: Invitation tokens stored as hashes (never plaintext)

**Date:** 2026-08-03
**Status:** Accepted

### Context

The original design stored invitation tokens as plaintext. A database read by
any attacker with DB access would expose all pending invitation tokens, allowing
them to accept invitations as any pending invitee.

### Decision

`SHA-256(INVITATION_TOKEN_PEPPER + raw_token)` is stored. The raw token is
generated, sent to the invitee's email, and immediately discarded from server
memory. Acceptance looks up the stored hash of the presented token. The pepper
prevents precomputed rainbow-table attacks against the hash.

### Consequences

- Tokens cannot be recovered from the database by anyone, including DBAs
- Lost tokens require re-invitation (no "resend token" that reads the DB value)
- `INVITATION_TOKEN_PEPPER` is a required startup secret in Phase 1B

---

## ADR-016: uv as the Python package manager

**Date:** 2026-08-03
**Status:** Accepted

### Context

`pip` + `pip-tools` or `poetry` are the common alternatives. `uv` is
significantly faster, has native pyproject.toml support, and produces a
`uv.lock` lockfile that is committed to the repository for reproducible installs.

### Decision

**uv** for all Python dependency management. `uv.lock` is committed. `uv sync`
installs the exact locked versions. CI installs uv in the pipeline before
running any Python steps. The Dockerfile installs uv in the build stage.

### Consequences

- Developers must install uv locally (`pip install uv` or via the installer script)
- `uv lock` must be re-run and `uv.lock` committed after any `pyproject.toml` change
- `uv.lock` is the single source of truth for Python dependency versions

---

## ADR-017: Redis key-prefix namespacing instead of multiple logical databases

**Date:** 2026-08-03
**Status:** Accepted

### Context

The original design used multiple Redis logical databases (DB 0, 1, 2) for
session, lock, and rate-limit namespacing. Logical databases add operational
complexity and cannot be used with Redis Cluster.

### Decision

Single Redis instance, single logical database (DB 0). All keys namespaced
by prefix: `sess:`, `lock:`, `rate:`, `cache:`. This is compatible with Redis
Cluster and Sentinel without configuration changes.

### Consequences

- `REDIS_KEY_PREFIX_*` variables in `.env.example`
- Key enumeration (`SCAN`) must include the prefix to avoid returning keys from other namespaces
- No operational difference for the application; simpler Redis topology

---

## ADR-018: Nullable org_role for ordinary organisation members

**Date:** 2026-08-03
**Status:** Accepted

### Context

Every workspace member must also have an organisation membership row, so that
removing a user from an organisation cascades correctly and so that the org
membership table is the single list of "who belongs to this organisation."
The original design only stored owner and administrator rows in
`organisation_memberships`, leaving ordinary workspace members without an org
membership row.

### Decision

`organisation_memberships.org_role` is **nullable**. A NULL value represents
an ordinary organisation member — someone who belongs to the organisation but
holds no org-level administrative privileges. `owner` and `administrator` are
the only non-null values. This does not add a new eighth RBAC role; NULL is
the absence of an org-level role, not a role itself.

### Consequences

- Every workspace member must have a corresponding `organisation_memberships`
  row (org membership is a prerequisite for workspace membership)
- Removing a user from an organisation must cascade-delete their workspace
  memberships
- Permission resolution checks `org_role IS NOT NULL` before treating a user
  as having org-level privileges
- The org membership list is the authoritative "who is in this org" list

---

## ADR-019: Composite foreign key pattern for workspace-owned tables

**Date:** 2026-08-03
**Status:** Accepted

### Context

A cross-table CHECK constraint cannot enforce that a `workspace_id` in a child
table references a workspace belonging to the same `organisation_id` as the
child row. CHECK constraints cannot reference other tables. The only reliable
relational enforcement is a foreign key.

### Decision

All tables that are children of `workspaces` and must stay within the same
organisation carry a composite foreign key:

```sql
-- On the parent (workspaces):
ALTER TABLE workspaces ADD CONSTRAINT workspaces_id_org_unique
    UNIQUE (id, organisation_id);

-- On each child table (workspace_memberships, documents, chunks, workflows, ...):
FOREIGN KEY (workspace_id, organisation_id)
    REFERENCES workspaces(id, organisation_id)
    ON DELETE CASCADE;
```

This guarantees at the database level that `workspace_id` and `organisation_id`
in a child row cannot belong to different organisations.

### Consequences

- Every workspace-owned table must include `organisation_id NOT NULL` and the
  composite FK, not just a plain FK on `workspace_id`
- New workspace-owned tables added in future phases must follow this pattern
  in the same Alembic migration that creates them
- The `UNIQUE(id, organisation_id)` constraint on `workspaces` must be created
  before any child FK is created

---

## ADR-020: Strengthened RLS policies with USING + WITH CHECK and fail-closed null guard

**Date:** 2026-08-03
**Status:** Accepted

### Context

The original RLS policy template only covered SELECT (USING clause). INSERT and
UPDATE were not protected by a WITH CHECK clause. Additionally, `current_setting`
raises an error if the variable is not set unless the two-argument `missing_ok`
form is used; without a null guard, a misconfigured session could raise an
exception rather than failing securely.

### Decision

Every tenant-scoped table carries two policies:

1. A USING policy (guards SELECT and DELETE) with `AS RESTRICTIVE`
2. A WITH CHECK policy (guards INSERT and UPDATE) with `AS RESTRICTIVE`

Both use `NULLIF(current_setting('app.current_organisation_id', true), '')::uuid`
as the safe null guard. When the setting is absent or empty, NULLIF returns NULL,
the UUID cast produces NULL, and the comparison evaluates to false — the row is
rejected or invisible. The system fails **closed** on absent context.

### Consequences

- All Alembic migrations creating tenant-scoped tables must include both policy
  declarations
- Tests must verify RLS rejects cross-tenant SELECT, INSERT, UPDATE, and DELETE
- A test with no `set_config` call (simulating a missing session context) must
  verify that queries return zero rows and inserts are rejected

---

## ADR-021: Two-step login — global authentication then organisation selection

**Date:** 2026-08-03
**Status:** Accepted (see also ADR-027 for the pre-auth session mechanism
added in revision 3)

### Context

When a user may belong to multiple organisations, issuing an org-scoped JWT
immediately on credential verification requires guessing which organisation the
user intends to work in. Including all organisation IDs in a single JWT is
unworkable because the token would need to be re-issued on every org switch
and the claims structure would not be clean.

### Decision

Login is split into two steps:

1. `POST /api/v1/auth/login` — verifies credentials; returns user info and the
   list of organisations the user belongs to (via the user-context RLS policy).
   No JWT is issued. Issues a short-lived pre-auth session (see ADR-027) to
   carry identity securely into step 2.

2. `POST /api/v1/auth/select-organisation` — reads pre-auth session cookie;
   verifies DB membership; issues an org-scoped access token and refresh token.
   `user_id` is derived from the server-side session, never from the request body.

The `org` claim in the JWT is **re-verified against the DB on every subsequent
request**. A revoked membership invalidates access immediately, not at token
expiry.

### Consequences

- Two API endpoints replace the single `/login` endpoint
- Frontend must handle the 0-org, 1-org, and multi-org flows
- User-context RLS policy on `organisation_memberships` is required for step 1
- Per-request membership check adds one DB read per request; this is the
  correct trade-off for immediate revocation

---

## ADR-022: Deferred constraint trigger for single-owner invariant

**Date:** 2026-08-03
**Status:** Accepted

### Context

Enforcing "exactly one owner per organisation" in application code only is
insufficient — a concurrent request or a direct DB write could violate the
invariant. A unique constraint on `(organisation_id, org_role) WHERE org_role = 'owner'`
would prevent a transfer (it would need to set the new owner first, violating
uniqueness, or demote the old owner first, leaving zero owners mid-transaction).

### Decision

A **deferred constraint trigger** fires at transaction commit, not at each row
operation. This allows an ownership transfer to promote the new owner and demote
the old owner in the same transaction without a mid-transaction violation.
`SELECT FOR UPDATE` on the organisation row serialises concurrent transfer
attempts.

### Consequences

- The trigger adds one aggregate SELECT per membership row operation; acceptable
- Concurrent transfer tests must verify that two simultaneous transfers for the
  same organisation result in exactly one success and one error
- The migration must create the trigger function and the constraint trigger in
  the correct order

---

## ADR-023: Transactional audit emission for security-critical events

**Date:** 2026-08-03
**Status:** Accepted

### Context

A fire-and-forget background audit emit risks losing the audit record if the
application crashes between committing the business change and executing the
background task. For security-critical events (auth, membership changes,
ownership transfers), losing the audit record is unacceptable.

### Decision

All Phase 1A security-critical audit events are written **in the same database
transaction** as the business operation. The `AuditEvent` row is added to the
SQLAlchemy session unit of work before commit. The business change and the audit
row commit together or roll back together. An audit write failure propagates
and rolls back the business operation.

Login failure audits use an independent synchronous insert on a separate
connection, since the login transaction itself may be rolled back on failure.

For Phase 8 external delivery (SIEM), a transactional outbox is used: the
audit row is committed with the business op; background delivery to the
external system can fail independently without affecting the stored record.

### Consequences

- `AuditService.emit_transactional(session, event)` takes the current session
- `AuditService.emit_independent(event)` opens a short-lived separate session
- Non-critical informational events (e.g. report views) may use background tasks
- Audit INSERT failure on a security-critical event causes the business op to fail

---

## ADR-024: Browser token storage — memory access token, HttpOnly refresh cookie, CSRF header

**Date:** 2026-08-03
**Status:** Superseded by ADR-028 (CSRF double-submit cookie pattern; refresh
cookie Path corrected). Core access token and refresh token storage decisions
remain unchanged.

### Context

Storing tokens in localStorage or sessionStorage exposes them to XSS. Storing
the access token in a cookie and relying on SameSite alone for CSRF protection
is insufficient — SameSite=Lax allows GET requests and top-level navigation
cross-site POSTs. A token stored in a cookie and sent automatically is vulnerable
to CSRF without an additional explicit token check.

### Decision (superseded — see ADR-028)

- **Access token:** stored in JavaScript memory only. Sent via
  `Authorization: Bearer <token>` header. Not in any storage API.
- **Refresh token:** stored in an `HttpOnly; Secure; SameSite=Lax;
  Path=/api/v1/auth/refresh` cookie. *(Path corrected to `/api/v1/auth`
  in ADR-028.)*
- **CSRF token:** a short-lived signed value returned in the login step 2
  response body. Stored in memory alongside the access token. *(Superseded
  by the double-submit cookie pattern in ADR-028.)*
- Logout clears the cookie (`Set-Cookie: Max-Age=0`) and revokes the refresh
  token server-side. Frontend discards in-memory tokens.

### Consequences

See ADR-028 for current consequences.

---

## ADR-025: RBAC source of truth in permissions.py — no mutable DB role tables in Phase 1A

**Date:** 2026-08-03
**Status:** Accepted

### Context

Mutable `roles`, `permissions`, and `role_permissions` tables create a runtime
source of truth that can be edited by mistake or by a compromised admin.
For Phase 1A, all roles and permissions are known at build time. Custom roles
are not required until a later phase.

### Decision

`backend/app/auth/permissions.py` is the single authoritative source of the
permission matrix. It defines role enums and a dict mapping role → frozenset
of permissions. No `roles`, `permissions`, or `role_permissions` tables are
created in Phase 1A. Membership rows store role names as enum values validated
against this module. RBAC tests are generated from this module.

Custom role support (if needed) is deferred to a later ADR that will design
mutable role tables, migration path, and additional security controls.

### Consequences

- The permission matrix cannot be changed without a code deploy
- This is an explicit security feature for Phase 1A — runtime permission
  changes require a deployment and code review
- If custom roles are required, a new ADR and a migration adding the role
  tables will be written before implementation

---

## ADR-026: RLS policy type — single permissive FOR ALL, not AS RESTRICTIVE

**Date:** 2026-08-03
**Status:** Accepted — corrects ADR-020 RLS implementation detail

### Context

ADR-020 documented the three-layer tenant isolation model with USING and WITH
CHECK clauses. The revision 2 implementation plan described creating two
separate `AS RESTRICTIVE` policies — one for SELECT/DELETE (USING) and one for
INSERT/UPDATE (WITH CHECK) — without a permissive base policy.

PostgreSQL's RLS model: a row is accessible if at least one permissive policy
allows it AND all restrictive policies allow it. A table with only restrictive
policies and no permissive policy always denies all access, because there is no
permissive policy to satisfy the first condition.

### Decision

Each tenant-scoped table carries a single default **permissive** `FOR ALL`
policy with both `USING` and `WITH CHECK` clauses:

```sql
CREATE POLICY <table>_tenant_isolation ON <table>
FOR ALL
USING (
    organisation_id =
    NULLIF(current_setting('app.current_organisation_id', true), '')::uuid
)
WITH CHECK (
    organisation_id =
    NULLIF(current_setting('app.current_organisation_id', true), '')::uuid
);
```

`AS RESTRICTIVE` is not used for the primary tenant isolation policy. The
`NULLIF` null guard ensures absent context fails closed: NULL ≠ any UUID, so
both USING and WITH CHECK evaluate to false when context is unset.

The `organisation_memberships` user-context policy (for listing orgs before
org selection) is also a permissive `FOR SELECT` policy — same rationale.

### Consequences

- The Alembic migration must create permissive policies, not restrictive ones
- A migration verification test must confirm that policy type is `PERMISSIVE`
  in `pg_policies` for all tenant-scoped tables
- Tests that simulate absent context (no `set_config`) must verify that both
  SELECT returns zero rows and INSERT is rejected
- Developers adding new tables must follow this pattern; code review must verify

---

## ADR-027: Pre-authentication session between login steps

**Date:** 2026-08-03
**Status:** Accepted

### Context

The two-step login in ADR-021 needs to carry the authenticated `user_id` from
step 1 to step 2. Without a server-side session, step 2 would need to either:

- Accept `user_id` in the request body — vulnerable to user_id injection (an
  attacker can claim to be any user by supplying their `user_id`)
- Issue a partial JWT at step 1 — complex, and a JWT at step 1 contradicts the
  intent of the two-step design

### Options

| Option | Risk |
|--------|------|
| `user_id` in request body | User_id injection; attacker selects org on behalf of another user |
| Partial JWT at step 1 | Complexity; partial authority token |
| Signed opaque cookie | Requires sharing JWT secret or a separate signing key |
| Server-side pre-auth session | Single-purpose; short-lived; consumable once |

### Decision

A **server-side pre-auth session** is created at login step 1:

1. Generate 32 cryptographically random bytes (raw token).
2. Compute `SHA-256(PRE_AUTH_SESSION_PEPPER + raw_token)`.
3. Store `(session_hash, user_id, expires_at = now() + 5 min, consumed_at = NULL)`
   in the `pre_auth_sessions` table.
4. Set the raw token as an `HttpOnly; Secure; SameSite=Lax;
   Path=/api/v1/auth/select-organisation; Max-Age=300` cookie.

At step 2, the raw token is read from the cookie, hashed, looked up by hash,
validated (expiry, not consumed), consumed atomically (`UPDATE WHERE consumed_at
IS NULL`), and `user_id` is derived from the session row only. The step 2
request body contains `organisation_id` only — no `user_id` field exists.

### Consequences

- A `pre_auth_sessions` table is required (not tenant-scoped; looked up before
  org context is established)
- `PRE_AUTH_SESSION_PEPPER` is a required Phase 1A secret
- `auth.pre_auth_session_expired` and `auth.pre_auth_session_reused` audit
  events are written via independent connection (like login failures)
- Tests must cover: missing cookie (401), expired session (401 + audit), reused
  session (401 + audit), `user_id` in request body is ignored, concurrent
  consume race (only one succeeds)

---

## ADR-028: CSRF double-submit cookie; refresh cookie Path=/api/v1/auth

**Date:** 2026-08-03
**Status:** Accepted — supersedes ADR-024 CSRF and refresh cookie design

### Context

ADR-024 described storing the CSRF token in JavaScript memory and returning it
in the response body, and setting the refresh cookie `Path=/api/v1/auth/refresh`.

Two problems with that design:

1. **Memory-only CSRF token:** the CSRF token is lost on page refresh. The
   frontend must execute a silent refresh to restore it, coupling CSRF to the
   token refresh flow. Any network failure during refresh leaves the app without
   a CSRF token.

2. **Narrow refresh cookie Path:** `Path=/api/v1/auth/refresh` means the
   refresh cookie is not sent to `POST /auth/logout` or `POST /auth/logout-all`,
   which also need to read the refresh token in order to revoke it.

### Decision

**CSRF — double-submit cookie pattern:**

The CSRF token is `HMAC-SHA256(CSRF_SECRET, refresh_jti)`, binding it to the
refresh session. The backend sets it as a separate cookie:

```
Name:     csrf_token
HttpOnly: no   ← must be readable by JavaScript
Secure:   yes
SameSite: Lax
Path:     /
Max-Age:  same as refresh token
```

The frontend reads this cookie value and sends it as the `X-CSRF-Token` header.
The backend compares header to cookie in constant time (`hmac.compare_digest`).
Mismatch, missing header, or missing cookie → 403. The `Origin` header is also
validated on all cookie-authenticated state-changing requests.

CSRF token is rotated on org-selection and token refresh. Both the new CSRF
cookie and the new refresh cookie are set in the response.

CSRF is required on: `POST /auth/refresh`, `POST /auth/logout`,
`POST /auth/logout-all`.

**Refresh cookie Path — widened to `/api/v1/auth`:**

```
Name:     refresh_token
HttpOnly: yes
Secure:   yes
SameSite: Lax
Path:     /api/v1/auth   ← covers refresh, logout, logout-all
Max-Age:  JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400
```

Logout clears both the refresh cookie and the CSRF cookie using `Max-Age=0`
with the same attributes as when they were set.

`CSRF_SECRET` is a required Phase 1A secret (rejected if `REPLACE_*`).

### Consequences

- The CSRF cookie survives page refresh; no coupling to the token refresh flow
- The refresh cookie Path being `/api/v1/auth` is slightly wider than strictly
  necessary, but all endpoints under that prefix require a valid refresh token,
  so the exposure is minimal and controlled
- Tests must verify: CSRF cookie is not HttpOnly; CSRF header mismatch → 403;
  unexpected Origin → 403; CSRF rotation after refresh; logout clears both
  cookies with correct attributes
- Frontend reads `csrf_token` cookie on load; no separate in-memory storage needed
  - Frontend reads `csrf_token` cookie on load; no separate in-memory storage needed

---

## ADR-029: BLAKE2b keyed mode for API key and invitation token hashing

**Date:** 2026-08-04
**Status:** Accepted — corrects earlier concatenation design; pepper rotation strategy revised 2026-08-04

### Context

Phase 1B API key and invitation token hashes were initially designed using
BLAKE2b with the pepper *concatenated as data*:
`BLAKE2b(pepper_bytes + raw_secret)`. This is functionally equivalent to
HMAC-BLAKE2b only if the input is length-delimited, which it is not here.
BLAKE2b provides a native keyed mode (`key=` parameter) specifically for
MAC use cases. The concatenation approach bypasses this and loses the security
guarantee that the pepper is a true cryptographic key.

### Decision

Both `API_KEY_PEPPER` and `INVITATION_TOKEN_PEPPER` are used as the BLAKE2b
cryptographic `key=` parameter:

```python
hashlib.blake2b(raw_secret.encode(), key=pepper_bytes[:64], digest_size=32).hexdigest()
```

The pepper is truncated to 64 bytes before use (BLAKE2b key maximum). This
truncation is explicit and documented; the pepper must be ≥ 32 bytes per the
startup validator.

The `_hash_key()` method in `ServiceAccountService` and the corresponding
method in `InvitationService` both use keyed mode.

### Consequences

- Secret hashes stored in the database are produced by a properly keyed MAC,
  not a length-extension-vulnerable concatenation scheme.
- `API_KEY_PEPPER` and `INVITATION_TOKEN_PEPPER` are validated at startup to
  be non-placeholder and ≥ 32 bytes.
- Tests verify: same input → same hash; keyed hash ≠ concatenation hash for the
  same pepper; different inputs → different hashes; all three properties hold
  for both API keys and invitation tokens.

**Pepper rotation strategy — correction:**
A rotation strategy that described "a background job re-hashing stored rows with
the new pepper" is **cryptographically impossible** for one-way hashes: the raw
secret is never stored (only its BLAKE2b digest), so there is no plaintext
available to re-hash. Two viable strategies are:

1. **Rotation-invalidates policy** (Phase 1B default): Rotating the pepper
   immediately invalidates all existing API keys and invitation tokens — holders
   must reissue. This is operationally acceptable for API keys (regenerable on
   demand) and for invitations (already short-lived, can be reissued). This is
   the selected strategy for Phase 1B: pepper rotation is a planned maintenance
   event, announced in advance, that requires key reissuance.

2. **Versioned pepper with opportunistic upgrade** (future option): Store a
   `secret_hash_version` integer alongside each hash row. At authentication
   time, after verifying the plaintext against the stored hash, re-hash with the
   new pepper and update the stored hash and version. Old-version hashes remain
   valid until touched. This requires a schema migration and is deferred to
   Phase 2+ if pepper agility is required.

No background rehash job will be implemented. Any such job would be
cryptographically incoherent.

---

## ADR-030: invitation.expired audit durability via independent transaction

**Date:** 2026-08-04
**Status:** Accepted — updated 2026-08-05 (durability final fix)

### Context

`fn_audit_insert_global` (SECURITY DEFINER) exists for events that occur
*before* an organisation context is established — specifically the four Phase 1A
`auth.*` pre-login failures. The original Phase 1B design added
`invitation.expired` to this allowlist on the assumption that a background
expiry job might run without org context. This was rejected (see below).

A subsequent correction used `emit_transactional()` followed by
`session.flush()` before raising `InvitationExpiredError`. This also fails:
`flush()` writes within the SAME transaction. When `InvitationExpiredError`
propagates up to the request handler and the transaction rolls back, the audit
row is rolled back too. `flush() != commit`. `flush() != durable`.

### Decision

`invitation.expired` is NOT added to `GLOBAL_EVENT_TYPES` and is NOT emitted
via `fn_audit_insert_global`. The reasoning:

1. Invitation expiry is *detected* inside `InvitationService.accept()`, which
   is called by an authenticated user holding a valid JWT. The JWT contains
   `organisation_id`, so org context (tenant identity) is always available —
   this is NOT a pre-auth global event.
2. `fn_audit_insert_global` allowlist must stay at exactly 4 types (`auth.*`
   only). Expanding it unnecessarily widens the SECURITY DEFINER attack surface.
3. A broad SECURITY DEFINER escape hatch is not acceptable.

Instead, `invitation.expired` is emitted via the new
`AuditService.emit_tenant_independent()` path, which:

- Opens a SEPARATE `AsyncSession` (new DB connection, new transaction).
- Inserts the `AuditEvent` row and commits atomically.
- Returns BEFORE raising `InvitationExpiredError`.
- Cannot be rolled back by any subsequent action in the caller's session.

The `organisation_id` for the audit row is sourced from
`invitation.organisation_id` (a trusted server-side DB value loaded earlier in
`accept()`), never from client-supplied request data.

`GLOBAL_EVENT_TYPES` in `audit.py` remains exactly 4 types:
`{auth.login_failed, auth.pre_auth_session_expired, auth.pre_auth_session_reused, auth.token_reuse_detected}`.

A new `TENANT_INDEPENDENT_EVENT_TYPES` frozenset holds the allowlist for the
independent-commit path, currently `{invitation.expired}`.

### Consequences

- The migration (`0002_phase_1b_admin_identity.py`) does NOT extend
  `fn_audit_insert_global` with `invitation.expired`.
- `emit_independent(event_type="invitation.expired")` raises `ValueError`.
- `emit_transactional(event_type="invitation.expired")` followed by `flush()`
  is no longer used in the expiry path — `emit_tenant_independent()` replaces it.
- `invitation.expired` audit rows are durable regardless of caller transaction state.
- Tests assert: `invitation.expired not in GLOBAL_EVENT_TYPES`;
  `GLOBAL_EVENT_TYPES` has exactly 4 members;
  `TENANT_INDEPENDENT_EVENT_TYPES` contains `invitation.expired`;
  scenario 28 proves durability via a new independent session after rolling back
  the original session (the only valid durability proof — same-session queries
  are insufficient).

---

## ADR-031: Org/workspace runtime selector — new access token, preserve refresh token

**Date:** 2026-08-04
**Status:** Accepted — workspace switching and jti correction added 2026-08-04

### Context

Multi-tenant users belong to multiple organisations and multiple workspaces.
After initial login (`POST /auth/login` → `POST /auth/select-organisation`) the
JWT is scoped to one org with no workspace context. Users need to switch between
orgs and activate a workspace context without a full re-login.

An initial implementation of `POST /me/switch-org` preserved `jti=payload.jti`
in the new access token (reusing the refresh token's jti). This violates RFC 7519
§4.1.7 (jti must uniquely identify the token). See ADR-034 for the jti
correction and CSRF rebinding.

### Decision

`POST /api/v1/me/switch-org` provides the runtime org selector:

1. Validates the bearer token (existing org JWT).
2. Performs a live DB membership check for the target org.
3. Issues a new access token via `JWTService.issue()` with the new `org` and
   `role` claims. A fresh `jti` is generated per issue() call (RFC 7519 §4.1.7).
   CSRF binding is preserved via the `fid` (family_id) claim — see ADR-034.
4. Clears workspace context — the new token carries no workspace claims.
5. Does NOT rotate the refresh token or set a new refresh cookie.
6. Returns 400 if switching to the same org (no-op).
7. Returns 403 if the user is not a member of the target org.
8. Emits an `org.context_switched` transactional audit event.

`POST /api/v1/me/switch-workspace` provides workspace context activation:

1. Validates the bearer token. Org context must be established first.
2. Verifies the workspace exists AND belongs to `payload.organisation_id`
   (cross-org isolation — explicit WHERE on organisation_id).
3. Verifies the workspace is active (is_active=True).
4. Verifies the user has a `WorkspaceMembership` in this workspace
   (filtered by both workspace_id and organisation_id).
5. Issues a new access token with `workspace` and `ws_role` claims added.
   Fresh `jti`; `fid` preserved.
6. Returns 404 if workspace not found in the current org.
7. Returns 403 if workspace is inactive or user has no membership.
8. Emits a `workspace.context_switched` transactional audit event.

Cross-org isolation is enforced by two independent layers:
  a. Explicit WHERE `workspace.organisation_id == payload.organisation_id`.
  b. DB-level: `WorkspaceMembership` composite FK `(workspace_id, organisation_id)`
     → `workspaces(id, organisation_id)` — a membership row can never reference
     a workspace from a different org at the database level.

`GET /api/v1/me/context` returns the current org + workspace context (user_id,
organisation_id, org_role, slug, display_name, workspace_id, workspace_role,
workspace_slug) without state mutation. workspace fields are null when no
workspace context is active.

### Consequences

- The selector is a runtime convenience distinct from the login-time
  `/select-organisation` endpoint.
- After `POST /me/switch-org`, workspace context is cleared — the user must call
  `POST /me/switch-workspace` to activate a workspace in the new org.
- workspace_role in the new token is always loaded from the live DB membership
  row, never from the request body — role escalation via the endpoint is not
  possible.
- Tests cover 19 scenarios: context GET, switch-org (6 cases), switch-workspace
  (12 cases including cross-org isolation, inactive workspace, role from DB).

---

## ADR-032: API key scope enforcement via required_scopes parameter

**Date:** 2026-08-04
**Status:** Accepted

### Context

Phase 1B API keys carry a `scopes` JSONB list. Without enforcement, a key
with scope `["analytics:read"]` could be presented at an endpoint requiring
`org:admin` and would pass authentication.

### Decision

`ServiceAccountService.authenticate_api_key()` accepts an optional
`required_scopes: list[str] | None` parameter. When provided, the method
checks that every scope in `required_scopes` is present in the key's `scopes`
list. Any missing scope raises `ApiKeyScopeError`. The check occurs after
hash verification and status checks but before updating `last_used_at` —
a scope-rejected request does not update the timestamp.

Route handlers pass `required_scopes` appropriate to the protected operation.

### Consequences

- A key with insufficient scopes is rejected with a clear `ApiKeyScopeError`
  (mapped to 403 at the route layer).
- `required_scopes=None` (default) skips enforcement — useful for routes that
  accept any authenticated service account regardless of declared scopes.
- Tests cover: missing scope raises; all scopes present passes; None skips.

---

## ADR-033: API key prefix collision retry

**Date:** 2026-08-04
**Status:** Accepted

### Context

`key_prefix` (first 8 chars of the raw key) has a unique constraint
(`uq_api_keys_prefix`). With a 48-character base64url secret pool, prefix
collision probability is extremely low but non-zero across millions of keys.

### Decision

`ServiceAccountService.create_api_key()` retries key generation up to 3
times on `IntegrityError` referencing `uq_api_keys_prefix`. After 3 failed
attempts, the `IntegrityError` is re-raised (triggers a 500 at the route
layer, which is appropriate — a triple collision indicates a systemic issue
rather than a user error).

### Consequences

- Normal operation: zero retries (collision is astronomically unlikely).
- The retry loop is a correctness guarantee for long-running multi-tenant
  deployments with millions of API keys.
- Tests mock the prefix generation to force collision and verify the retry
  loop exhausts correctly.

---

## ADR-034: JWT jti uniqueness per access token + family_id CSRF binding

**Date:** 2026-08-04
**Status:** Accepted — Phase 1B architectural correction

### Context

AtlasCore's original CSRF design bound the CSRF double-submit token to the
*refresh token's jti*:

```
CSRF_TOKEN = HMAC-SHA256(CSRF_SECRET, refresh_jti)
```

Access tokens carried `jti = refresh_token.jti` so the CSRF service could
recompute the expected CSRF value from the JWT. This was intentional (see
`tokens.py` docstring pre-Phase-1B) but violated RFC 7519 §4.1.7, which
requires `jti` to *uniquely identify the JWT* — i.e., the access token itself,
not some parent token.

Additionally, `POST /me/switch-org` reused `jti=payload.jti` (copying the old
access token's jti into the new access token), producing two access tokens with
the same jti. This directly violates the uniqueness requirement.

### Decision

**jti**: `JWTService.issue()` now generates `jti = str(uuid.uuid4())` internally.
The caller no longer supplies jti. Each call to `issue()` produces a distinct
access token identifier. This is not configurable.

**family_id (fid claim)**: A new required claim `fid` is added to all access
tokens. `fid` carries the `RefreshToken.family_id` — a UUID assigned at first
login and preserved across all rotations of that login session (each rotation
shares the same family_id). `fid` is the new CSRF binding identifier:

```
CSRF_TOKEN = HMAC-SHA256(CSRF_SECRET, family_id)
```

**CSRF stability**: Because `family_id` is stable across context switches (org
switch, workspace switch) that mint new access tokens with new jtis, the CSRF
cookie does not need to be updated after a context switch. The cookie only
rotates when a new refresh token *family* is created (new login) or cleared on
logout.

**TokenPayload**: Extended with `family_id: str` and optional `workspace_id`,
`workspace_role` fields.

**verify()**: Now requires the `fid` claim in the `required` list — tokens
without `fid` are rejected (backward-incompatible; existing tokens from before
this change are invalid on upgrade).

**CSRFService**: `generate_token()`, `set_csrf_cookie()`, and `verify()` now
accept `family_id: str` instead of `refresh_jti: str`.

**RequireCSRF** (`deps.py`): Uses `payload.family_id` for CSRF verification.

**Auth endpoints** (`auth.py`): `select_organisation` and `refresh` pass
`family_id=str(rt.family_id)` to `issue()` and `csrf_service.set_csrf_cookie()`.

### Consequences

- All minted access tokens have a distinct jti (RFC 7519 §4.1.7 compliance).
- CSRF protection is preserved with equivalent security — an attacker who steals
  a CSRF cookie from session A cannot use it in session B (different family_id).
- CSRF cookie remains valid across the lifetime of a login session (multiple
  org/workspace switches), reducing unnecessary cookie re-reads by the frontend.
- All existing access tokens from before this change are invalid on upgrade
  (missing `fid` claim → DecodeError). This is acceptable: Phase 1B has no
  production deployment.
- Tests: `test_tokens.py` verifies jti uniqueness per call, workspace claims,
  and `fid` round-trip. `test_csrf.py` verifies CSRF stability within a family
  and rejection across families. `test_concurrency.py` C4 updated to verify
  family-based CSRF semantics.

---

## ADR-035: Live workspace membership re-verification on every workspace-scoped request

**Date:** 2026-08-05
**Status:** Accepted — Phase 1B durability final fix

### Context

Phase 1B introduced workspace-scoped JWTs carrying `workspace_id` and
`workspace_role` claims. The dependency `get_current_membership()` in
`deps.py` re-verified `OrganisationMembership` from the live database on every
authenticated request (not just from JWT claims). However, it did NOT re-verify
`WorkspaceMembership`. A user whose workspace membership was revoked could
continue making workspace-scoped requests until their access JWT expired (up to
15 minutes by default).

This is the "stale workspace membership" gap.

### Decision

`get_current_membership()` performs a second live database check when
`payload.workspace_id` is not `None`:

```python
if payload.workspace_id is not None:
    ws_result = await db.execute(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == payload.workspace_id,
            WorkspaceMembership.user_id == payload.user_id,
            WorkspaceMembership.organisation_id == payload.organisation_id,
        )
    )
    if ws_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=403, detail="Workspace membership not found or revoked.")
```

The check is in the SAME dependency that every authenticated route already
uses — no per-route special-casing is required. Revocation takes effect on the
NEXT request after the `WorkspaceMembership` row is deleted.

### Consequences

- Revoking a workspace membership causes the next request with a workspace-
  scoped JWT to fail with HTTP 403 (not at JWT expiry).
- Existing org-only JWTs (no `workspace_id` claim) are unaffected — the extra
  query only runs when the claim is present.
- One additional `SELECT` per workspace-scoped request. This is acceptable:
  the org membership check already incurs a SELECT; the workspace check adds
  one more. Both use the primary key / indexed columns.
- Test: scenario 20 in `test_selector.py` proves the behaviour end-to-end —
  it deletes the `WorkspaceMembership` row and asserts the next request returns
  403.

---

## ADR-036: BlobStore storage key design — server-generated UUID path, never filename-derived

### Status: Accepted

### Context

Uploaded documents must be stored on disk (or in object storage). The naive approach of using
the original filename as a storage path is dangerous: filenames can contain path traversal
sequences (`../`), null bytes, absolute paths, or excessively long strings. Filenames are also
tenant-chosen and therefore untrusted.

### Decision

The `LocalFilesystemBlobStore` uses a server-generated storage key of the form:

```
{organisation_id}/{workspace_id}/{document_id}/{version_id}
```

All four segments are UUID v4 values generated by the server. The key is validated by
`_KEY_SAFE_PATTERN` (exactly 4 UUID segments, `/`-separated) before any filesystem operation.
The `original_filename` is stored in the database as display metadata only — it never
contributes to the storage key.

Additional defences:
- `_resolve_key()` joins the key to the root, resolves the result, and calls
  `candidate.relative_to(self._root)` — any path escape raises `BlobStorePathError`.
- The blob root is validated to be a non-symlink absolute path at construction time.
- Writes are atomic: bytes go to `{key}.tmp`, then `os.rename()` to the final path.
- `storage_key` is stored in the database but never returned in any API response.

### Consequences

- No filename-based path traversal is possible.
- `storage_key` must be stored in `knowledge_document_versions` for blob lifecycle management,
  but is absent from all response schemas.
- The UUID path format provides deterministic cleanup: if a blob write succeeds but the DB
  transaction fails, the blob key is derived from the version UUID and can be deleted.

---

## ADR-037: Untrusted document model — content never used as system prompt or tool input

### Status: Accepted

### Context

In RAG systems it is common to construct LLM system prompts that include document content.
This creates a prompt injection vulnerability: a document containing text like
`Ignore all previous instructions and...` can manipulate the LLM's behaviour.

### Decision

Phase 2A establishes the "untrusted document" boundary:

1. Document bytes are parsed by `PlainTextParser` or `MarkdownParser` into Unicode text.
   The parsers strip formatting but never evaluate code.
2. Text is chunked by `TextChunker` into immutable `Chunk` dataclasses. SHA-256 is computed
   per chunk for deduplication within the tenant.
3. Chunks are embedded by `EmbeddingProvider.embed()`. The test provider makes no network calls.
4. Chunk text and embeddings are stored in PostgreSQL under FORCE RLS.

**Phase 2A contains NO retrieval, NO RAG, NO system-prompt construction.** Document content
never reaches an LLM context in Phase 2A. Phase 2B (if implemented) MUST establish an
injection scan before any content is placed in a system prompt.

### Consequences

- Phase 2A cannot be used to answer questions from documents — that is intentional.
- The boundary simplifies the security model: no prompt injection risk in Phase 2A.
- Phase 2B must treat retrieved chunks as untrusted interpolations, not trusted content.

---

## ADR-038: Deterministic test embedding provider — SHA-256 seed expansion, no network

### Status: Accepted

### Context

Production embedding providers (OpenAI, Anthropic, etc.) require API keys and network access.
In the development and CI environment, network access is restricted and API keys should not
be required.

### Decision

`DeterministicTestEmbeddingProvider` generates embeddings without any network calls:

1. For each chunk text, compute SHA-256 of the UTF-8 bytes.
2. Expand the 32-byte hash into `dim` floats using the deterministic hash as a PRNG seed.
3. L2-normalise the resulting vector to unit length.

The resulting vectors are deterministic (same text → same vector) and real-valued (suitable
for pgvector). They have no semantic meaning but allow the ingestion pipeline to complete
end-to-end without external dependencies.

Production providers must be reviewed to ensure they do not send document content to external
services without authorisation. Provider API keys must not be stored in `source.configuration`.

### Consequences

- Full pipeline tests run without API keys or network access.
- Test embeddings have no semantic meaning; similarity search results are not meaningful in tests.
- Production deployment requires replacing the provider and securing its credentials.

---

## ADR-039: TextChunker word-boundary splitting — deterministic, overlap, SHA-256 per chunk

### Status: Accepted

### Context

Embedding models have token limits. Documents must be split into chunks before embedding.
The split algorithm should be deterministic (same input → same chunks), produce overlapping
chunks to preserve context at boundaries, and produce an integrity hash per chunk for
deduplication.

### Decision

`TextChunker` uses the following algorithm:

1. Split text into tokens by whitespace (`text.split()`).
2. Walk tokens in steps of `chunk_size - overlap`, taking `chunk_size` tokens per step.
3. Join tokens with single spaces to form the chunk text.
4. Compute SHA-256 of the chunk text UTF-8 bytes.
5. Return frozen `Chunk` dataclasses with `text`, `index` (position in token list), and `sha256`.

Parameters: `chunk_size=256` tokens (default), `overlap=32` tokens (default).

The `Chunk` dataclass is frozen (`@dataclass(frozen=True)`) and therefore hashable and
immutable. Chunk SHA-256 values are stored in `knowledge_chunks.content_sha256`.

Within a tenant, `content_sha256` enables deduplication of identical chunks. Cross-tenant
deduplication is prohibited (ADR-037 / cross-tenant isolation requirement).

### Consequences

- Chunking is fully deterministic; tests can assert exact chunk counts and SHA-256 values.
- Overlap preserves context at chunk boundaries; retrieval precision is improved at boundaries.
- Chunk SHA-256 values are computed with `hashlib.sha256` — NOT argon2/bcrypt. These are
  content checksums, not password hashes.

---

## ADR-040: Workspace RLS — dual-GUC policy enforces same-org cross-workspace isolation

**Date:** 2026-08-05
**Status:** Accepted — Phase 2A workspace RLS security fix (migration 0004)

### Context

Migration `0003_phase_2a_knowledge_foundation.py` introduced RLS policies for the six
knowledge tables that checked `organisation_id` only:

```sql
USING (
    organisation_id = NULLIF(
        current_setting('app.current_organisation_id', true), ''
    )::uuid
)
```

The composite FK `(workspace_id, organisation_id) → workspaces(id, organisation_id)`
prevents a child row from referencing a workspace belonging to a different organisation.
However, it does NOT prevent a session whose `app.current_organisation_id` matches the
correct org from reading, writing, updating, or deleting rows belonging to a different
workspace within the same organisation at the PostgreSQL level.

Example: org A has workspaces W1 and W2. A session with
`app.current_organisation_id = A` and NO workspace context (or W1 as workspace context)
could select rows from W2 — a same-org cross-workspace data leakage.

This is a genuine RLS gap. The service-layer `WHERE workspace_id = :wid` predicate
provided application-level isolation, but defence-in-depth requires both layers.

### Options considered

| Option | Pros | Cons |
|--------|------|------|
| Extend existing org-only policy to AND workspace_id | Correct; fail-closed for absent workspace context | New GUC required; all callers must supply workspace_id |
| Add a separate restrictive workspace policy | Works without modifying existing policy | PostgreSQL restrictive policies apply before permissive; logic is more complex |
| Rely on service-layer WHERE clause only | No migration needed | Violates defence-in-depth; a service bug or missing predicate leaks data at DB level |

### Decision

**Replace the six `{table}_tenant_isolation` policies with six `{table}_workspace_isolation`
policies that check BOTH `organisation_id` AND `workspace_id`** using the same NULLIF
fail-closed pattern:

```sql
USING (
    organisation_id = NULLIF(
        current_setting('app.current_organisation_id', true), ''
    )::uuid
    AND
    workspace_id = NULLIF(
        current_setting('app.current_workspace_id', true), ''
    )::uuid
)
```

The fail-closed semantics extend to the workspace dimension:
- `app.current_workspace_id` absent or `''` → NULLIF → NULL → zero rows.
- `app.current_workspace_id` = wrong UUID → zero rows.
- correct org + correct workspace → own workspace rows only.
- correct workspace + wrong org → zero rows (both predicates must match).

`OrganisationScopedSession` is extended to accept `workspace_id: uuid.UUID | None`.
When `None`, `app.current_workspace_id` is set to `''` (which NULLIF maps to NULL),
so callers that do not supply workspace context get zero knowledge rows — fail-closed.

The corrective migration is `0004_phase_2a_workspace_rls_hardening.py` (down_revision =
`0003_phase_2a`). It drops the old policies and recreates them with the dual predicate.

### Consequences

- All callers of knowledge-table queries MUST supply `workspace_id` to `OrganisationScopedSession`,
  or they will see zero rows (fail-closed). This is the desired behaviour.
- The 11 knowledge API endpoints already carry `workspace_id` as a URL path parameter;
  each is updated to pass it through to the session context.
- `app.current_workspace_id` is cleared by the pool checkin hook alongside the existing
  GUCs — no additional cleanup is required.
- Workspace isolation is now enforced at BOTH the application authorisation layer (live
  WorkspaceMembership query in `get_current_membership()`) AND the PostgreSQL RLS layer
  (dual-GUC `{table}_workspace_isolation` policy). Both must fail simultaneously for a
  cross-workspace leak.
- RLS tests RLS2A-24 through RLS2A-32 verify the isolation matrix for all six tables
  and all four DML operations.
- **Note (superseded by ADR-041):** The statement "each is updated to pass it through to
  the session context" was accurate at the time but understated a remaining gap: the URL
  path `workspace_id` was passed without first validating that it equalled the JWT claim.
  ADR-041 closes that gap by introducing `ValidatedWorkspaceId` as the mandatory
  dependency for all knowledge endpoints.

---

## ADR-041: Workspace path parameter validation — `ValidatedWorkspaceId` dependency

**Date:** 2026-08-05
**Status:** Accepted — Phase 2A requested-workspace authorization fix

### Context

After ADR-040 established workspace-level RLS (migration 0004), a second trust-boundary
gap was identified: all 11 knowledge endpoints accepted `workspace_id` as a raw URL path
parameter (`uuid.UUID`) and passed it directly to `OrganisationScopedSession` — setting
`app.current_workspace_id` in PostgreSQL — without verifying it against the JWT claim.

The vulnerability: a user holding a valid W1-scoped JWT could issue a request to
`/api/v1/knowledge/workspaces/{W2}/sources`. `get_current_membership()` would pass
(it validates `payload.workspace_id=W1`), but `workspace_id=W2` from the URL path
would become `app.current_workspace_id=W2` in the database session — without any
live W2 membership check. This is an IDOR (insecure direct object reference) at the
application layer, not caught by RLS because RLS only enforces the GUC value, not
how the GUC was set.

### Decision

Introduce `get_validated_workspace_context` in `deps.py`, exposed as
`ValidatedWorkspaceId = Annotated[uuid.UUID, Depends(get_validated_workspace_context)]`.

The dependency enforces three sequential checks before returning the trusted workspace_id:

1. **JWT must carry a workspace claim.** `payload.workspace_id is None` → HTTP 403.
   A fresh login token (before any switch-workspace call) may not access knowledge
   workspace routes.

2. **Path workspace must match JWT workspace.** `path_workspace_id != payload.workspace_id`
   → HTTP 403. This closes the IDOR gap: a W1-scoped token cannot be used on a W2 URL.

3. **Live WorkspaceMembership check.** A `WorkspaceMembership` row must exist for
   `(workspace_id, user_id, organisation_id)` in the live database. Row absent → HTTP 403.
   Membership revocation takes effect on the next request, not at JWT expiry.

All 11 knowledge endpoints are updated to use `ValidatedWorkspaceId` instead of
`workspace_id: uuid.UUID`. The raw path parameter is never used directly.

### Trust chain

```
URL path workspace_id  (CLIENT-SUPPLIED — untrusted)
    → path == JWT workspace_id          [step 2 — 403 if differs]
    → live WorkspaceMembership exists   [step 3 — 403 if revoked]
    → trusted workspace_id              [Depends return value]
    → OrganisationScopedSession         [sets app.current_workspace_id GUC]
    → PostgreSQL RLS                    [rows filtered by workspace_id]
```

### Relationship to `get_current_membership`

`get_current_membership` also validates `payload.workspace_id` (the JWT claim) against a
live `WorkspaceMembership` row. After this ADR, `get_validated_workspace_context` provides
a second independent check — but on the URL path workspace rather than the JWT workspace.
Both check the same table row when `path == JWT`, so the live-revocation guarantee is
doubly enforced. Keeping `get_current_membership`'s workspace check provides defence-in-depth
and is retained.

### Consequences

- The PostgreSQL workspace GUC is only ever set from an authenticated, live-membership-
  verified value.
- A W1-scoped JWT presented on a W2 route returns 403 at the dependency layer, before
  any knowledge DB query executes.
- Membership revocation takes effect on the next request for both the JWT-workspace path
  (`get_current_membership`) and the URL-path workspace (`get_validated_workspace_context`).
- `tests/knowledge/test_workspace_auth.py` covers scenarios A-I including the IDOR
  regression test (Scenario B) and structural test (Scenario I — no bare `uuid.UUID`
  workspace_id on any knowledge endpoint).
- The `ValidatedWorkspaceId` alias makes the dependency visible in endpoint signatures;
  a code reviewer can verify the fix is present without reading the dependency body.
