# AtlasCore — Security Policy

## Responsibility of this file

This file covers the public-facing security policy: supported versions,
vulnerability reporting, and design principles.

The full technical threat model — attack scenarios, mitigations, and test
requirements — is in [docs/SECURITY.md](docs/SECURITY.md).

---

## Supported versions

| Version | Security updates |
|---------|-----------------|
| 0.x (development) | Active development — all valid findings addressed |

---

## Reporting a vulnerability

Do **not** open a public GitHub issue for security vulnerabilities.

Email: security@atlascore.example *(replace with your real address before publishing)*

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested mitigation if known

You will receive an acknowledgement within 48 hours and a status update within
7 days. We will credit researchers who report valid findings.

---

## Security design principles

**1. Defence in depth.** Security checks exist at the network, application,
service, database, and AI layers. No single layer is the sole barrier.

**2. Zero trust within the platform.** Services authenticate each other;
no service is trusted solely by network location.

**3. Least privilege.** Every principal (user, service account, workflow, model)
operates with the minimum permissions required for its declared function.

**4. Deterministic authorisation.** Access control is enforced in application
code and at the database level, not in model prompts. Prompts are untrusted
user input and do not influence access decisions.

**5. Three-layer tenant isolation.** Every tenant-scoped database query passes
through: PostgreSQL Row-Level Security (with FORCE), explicit repository
predicates, and SQLAlchemy with-criteria. All three layers must fail
simultaneously for a cross-tenant leak to occur.

**6. Strengthened RLS — fail closed.** Every tenant-scoped table carries a
single permissive `FOR ALL` policy with both a USING clause (guards SELECT,
DELETE, and the pre-update existing row) and a WITH CHECK clause (guards INSERT
and the post-write row produced by UPDATE). Both clauses use a `NULLIF` null
guard: if the tenant context is not set, the null comparison fails closed —
no access is granted and no write is permitted. Absent context never grants
access.

**7. Untrusted retrieved content.** All content from knowledge bases, databases
and external tools is treated as potentially adversarial data. It is delivered
to the model inside a clearly delimited `<evidence>` block, never as system
instructions.

**8. Injection scanning is advisory.** The policy engine scans retrieved content
for injection patterns as an additional signal. This scan may reduce capabilities
or flag content for review, but it is not a reliable security boundary against
all injection techniques. Deterministic authorisation, the tool registry, and
access-controlled retrieval are the primary defences.

**9. Human approval for write actions.** Write tools with risk level above
`low` pause for human review. Arguments shown to the approver are
cryptographically bound (SHA-256 hash) to the arguments that will be executed.

**10. No generated write SQL.** The model cannot generate and execute INSERT,
UPDATE, DELETE or DDL statements. Write operations use explicit typed business
tools. Three independent layers enforce this.

**11. Credential hygiene.** No secret is hardcoded. All secrets are injected
from environment variables and redacted from logs. Argon2id passwords and
peppered token hashes are used throughout. Settings validation rejects
REPLACE_* placeholder values at startup.

**12. Immutable audit log.** Audit records are append-only. The application
database role has no UPDATE or DELETE on `audit_events`. This is a database
GRANT, not an application convention. Security-critical audit events are
committed transactionally with the business operation — never fire-and-forget.

**13. Access-controlled retrieval.** Knowledge retrieval access control is
applied inside SQL queries. Unauthorised chunks never reach score fusion,
reranking, external providers, or model context.

**14. Membership revocation is immediate.** The `org` claim in an access token
is re-verified against a live database membership row on every authenticated
request. A revoked membership takes effect on the next request, not at token
expiry.

**15. Browser token hygiene — double-submit CSRF cookie pattern.** The access
token is held in JavaScript memory and sent via the `Authorization: Bearer`
header. The refresh token is stored in an HttpOnly, Secure, SameSite=Lax,
Path=/api/v1/auth cookie. No tokens are stored in localStorage or
sessionStorage. CSRF protection uses the double-submit cookie pattern: the
backend sets a JS-readable (not HttpOnly) `csrf_token` cookie containing
`HMAC-SHA256(CSRF_SECRET, refresh_jti)`; the frontend reads the cookie and
sends the value as the `X-CSRF-Token` header; the backend compares header to
cookie in constant time. Mismatch, missing header, or missing cookie → 403.
Origin is also validated. CSRF token is rotated on org-selection and token
refresh. Logout clears both the refresh cookie and the CSRF cookie.

**16. Pre-authentication session prevents user_id injection.** Between login
step 1 (credential verification) and step 2 (org selection), `user_id` is
carried in a server-side pre-auth session stored by hash, issued as an HttpOnly
cookie scoped to the select-organisation endpoint only. The step 2 request body
contains `organisation_id` only — there is no `user_id` field. `user_id` is
derived exclusively from the server-side session row. This prevents an attacker
from claiming another user's identity by supplying their `user_id` in the
request body.

**17. Composite foreign keys for workspace-owned tables.** Tables that are
children of `workspaces` use a composite foreign key
(`FOREIGN KEY (workspace_id, organisation_id) REFERENCES workspaces(id, organisation_id)`)
to guarantee at the database level that workspace and organisation are consistent.

---

## Scope of the threat model

Threats addressed in [docs/SECURITY.md](docs/SECURITY.md):

Cross-tenant data leakage · Insecure direct-object references ·
Privilege escalation · Broken role checks · Direct prompt injection ·
Indirect prompt injection · Malicious MCP tools · Tool argument manipulation ·
Unrestricted SQL / SQL injection · Secrets leakage · Malicious file uploads ·
Poisoned knowledge documents · Unsafe model providers · Approval bypass ·
Replay attacks · Duplicate write actions · Excessive model cost ·
Sensitive data in logs · Insecure API keys · Session theft ·
CSRF on cookie-authenticated requests · Pre-auth session reuse and user_id
injection · Stale JWT org claim after membership revocation ·
Concurrent ownership transfer race condition ·
Placeholder secrets in production · SSRF · Dependency vulnerabilities

---

## Out of scope

- Physical security of infrastructure
- Client-side browser vulnerabilities unrelated to the application
- Social engineering attacks on end users
- Vulnerabilities in third-party LLM provider APIs themselves

---

## Phase 2C — Grounded answering security properties

**Evidence is UNTRUSTED DATA, never instruction.**
Retrieved document chunks are placed in structurally distinct `<EVIDENCE id="En">`
blocks in the provider prompt, labelled explicitly as UNTRUSTED DATA. The trusted
system instruction block is hardcoded in `app/answering/prompt.py`; it is NEVER
derived from user input or retrieved content.

**No general-knowledge fallback.**
The system prompt explicitly forbids the provider from using training data when
evidence is insufficient. The system abstains (`abstain_no_evidence` or
`abstain_weak_evidence`) rather than hallucinating an answer.

**Deterministic abstention before any LLM call.**
`EvidenceSufficiencyPolicy.assess()` runs before the provider is ever called.
If evidence is insufficient, the provider is not invoked — there is no LLM call.

**Fabricated citation IDs are rejected.**
`CitationValidator` cross-references every provider-returned citation ID against
the live `EvidencePacket`. IDs matching `E{n}` but not present in the packet
(e.g. E999) are rejected with `CitationValidationError`. IDs from prior requests
or sessions are implicitly rejected because the EvidencePacket is per-request.

**Provider-supplied provenance is NEVER used.**
All citation metadata (source_name, document_title, chunk_id, etc.) comes
exclusively from the server-controlled `EvidenceItem`. The provider can only
reference IDs; it cannot invent source names or URLs.

**Provider failures are safe.**
`AnswerProviderError` and unexpected exceptions are caught in
`GroundedAnswerService.answer()`. The caller receives a generic message:
"Unable to generate a grounded answer at this time." No API keys, stack traces,
system prompt content, or internal exception messages are returned.

**storage_key and embedding vectors are never returned.**
The `GroundedAnswerResponse`, `CitationResponse`, and `AnswerResponse` schemas
contain no `storage_key` field and no embedding vector data.

**Prompt injection heuristics.**
`_detect_injection_flags()` applies 16 lightweight pattern checks to retrieved
content. Flagged items are surfaced as warnings in the prompt (never executed)
and recorded in `suspicious_count` in the response. The score penalty reduces
the evidence confidence band for heavily-flagged packets.
