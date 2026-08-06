"""
PostgreSQL RLS isolation tests — Phase 2A knowledge tables.

Verifies that the NULLIF fail-closed RLS policy on all six knowledge tables
actually enforces cross-tenant boundaries at the database level, including
same-organisation cross-workspace isolation enforced by the workspace_id GUC.

Tables under test:
  knowledge_sources
  knowledge_documents
  knowledge_document_versions
  knowledge_ingestion_jobs
  knowledge_chunks
  knowledge_chunk_embeddings

Scenarios:
  RLS2A-01  Tenant A cannot SELECT knowledge_sources owned by tenant B
  RLS2A-02  Tenant A cannot INSERT knowledge_sources with Tenant B's org_id (WITH CHECK)
  RLS2A-03  Tenant A cannot UPDATE knowledge_sources owned by tenant B
  RLS2A-04  Tenant A cannot DELETE knowledge_sources owned by tenant B
  RLS2A-05  Empty context (fail-closed) → zero rows in knowledge_sources
  RLS2A-06  NULL context (NULLIF fail-closed) → zero rows in knowledge_sources
  RLS2A-07  Correct context → SELECT returns own knowledge_sources
  RLS2A-08  Tenant A cannot SELECT knowledge_documents owned by tenant B
  RLS2A-09  Correct context → SELECT returns own knowledge_documents
  RLS2A-10  Tenant A cannot SELECT knowledge_document_versions owned by tenant B
  RLS2A-11  Correct context → SELECT returns own knowledge_document_versions
  RLS2A-12  Tenant A cannot SELECT knowledge_ingestion_jobs owned by tenant B
  RLS2A-13  Correct context → SELECT returns own knowledge_ingestion_jobs
  RLS2A-14  Tenant A cannot SELECT knowledge_chunks owned by tenant B
  RLS2A-15  Correct context → SELECT returns own knowledge_chunks
  RLS2A-16  Tenant A cannot SELECT knowledge_chunk_embeddings owned by tenant B
  RLS2A-17  Correct context → SELECT returns own knowledge_chunk_embeddings
  RLS2A-18  Cross-org workspace reference is rejected by composite FK
  RLS2A-19  Storage keys are UUID paths — no filename or path traversal
  RLS2A-20  GLOBAL_EVENT_TYPES unchanged: still exactly 4 members after Phase 2A
  RLS2A-21  Tenant A cannot INSERT knowledge_documents with Tenant B's org_id (WITH CHECK)
  RLS2A-22  Tenant A cannot UPDATE knowledge_documents owned by tenant B (USING)
  RLS2A-23  Tenant A cannot DELETE knowledge_documents owned by tenant B (USING)
  RLS2A-24  Same-org workspace W1: SELECT W2 rows → zero (all 6 knowledge tables)
  RLS2A-25  Same-org workspace W1: INSERT into W2 → IntegrityError (all 6 knowledge tables)
  RLS2A-26  Same-org workspace W1: UPDATE W2 rows → 0 affected (all 6 knowledge tables)
  RLS2A-27  Same-org workspace W1: DELETE W2 rows → 0 affected (all 6 knowledge tables)
  RLS2A-28  Workspace context unset → zero knowledge rows (org set, workspace absent)
  RLS2A-29  Workspace context empty string → zero knowledge rows (NULLIF fail-closed)
  RLS2A-30  Wrong workspace UUID → zero knowledge rows (correct org, wrong workspace)
  RLS2A-31  Correct org + correct workspace → own rows returned
  RLS2A-32  Correct workspace but wrong org → zero knowledge rows
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.services.audit import GLOBAL_EVENT_TYPES

pytestmark = pytest.mark.asyncio

# Test-only mapping used by legacy org-context assertions.  Phase 2A hardening
# requires BOTH org and workspace GUCs for knowledge rows.
_ORG_DEFAULT_WORKSPACE: dict[uuid.UUID, uuid.UUID] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _set_org_context(conn: AsyncConnection, org_id: uuid.UUID) -> None:
    await conn.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(org_id)},
    )
    workspace_id = _ORG_DEFAULT_WORKSPACE.get(org_id)
    if workspace_id is not None:
        await conn.execute(
            text("SELECT set_config('app.current_workspace_id', :wid, true)"),
            {"wid": str(workspace_id)},
        )


async def _set_workspace_context(
    conn: AsyncConnection,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> None:
    """Set both organisation and workspace GUCs for the hardened workspace RLS policy."""
    await conn.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(org_id)},
    )
    await conn.execute(
        text("SELECT set_config('app.current_workspace_id', :wid, true)"),
        {"wid": str(workspace_id)},
    )


async def _clear_org_context(conn: AsyncConnection) -> None:
    await _clear_all_context(conn)


async def _clear_all_context(conn: AsyncConnection) -> None:
    """Clear both organisation and workspace GUCs."""
    await conn.execute(
        text(
            "SELECT set_config('app.current_organisation_id', '', true), "
            "       set_config('app.current_workspace_id', '', true)"
        )
    )


async def _select_source_ids(conn: AsyncConnection, org_id: uuid.UUID) -> list[uuid.UUID]:
    await _set_org_context(conn, org_id)
    result = await conn.execute(
        text("SELECT id FROM knowledge_sources WHERE organisation_id = :oid"),
        {"oid": str(org_id)},
    )
    return [r[0] for r in result.fetchall()]


# ---------------------------------------------------------------------------
# Fixtures: two fully isolated tenants with workspaces and one source each
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def two_tenants(engine: AsyncEngine, tables: None):
    """Create two complete tenants using the restricted FORCE-RLS app role."""
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    ws_a, ws_b = uuid.uuid4(), uuid.uuid4()
    src_a, src_b = uuid.uuid4(), uuid.uuid4()

    # Users are global rows.
    async with engine.begin() as conn:
        for uid in (user_a, user_b):
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, full_name, password_hash, pepper_version) "
                    "VALUES (:id, :email, 'Test', 'hash', 1) ON CONFLICT DO NOTHING"
                ),
                {"id": uid, "email": f"{uid.hex[:8]}@test.example"},
            )

    for oid, uid, wid, sid in (
        (org_a, user_a, ws_a, src_a),
        (org_b, user_b, ws_b, src_b),
    ):
        _ORG_DEFAULT_WORKSPACE[oid] = wid
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_organisation_id', :oid, true)"),
                {"oid": str(oid)},
            )
            await conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(uid)},
            )
            slug = f"org2a-{oid.hex[:8]}"
            await conn.execute(
                text(
                    "INSERT INTO organisations (id, slug, display_name) "
                    "VALUES (:id, :slug, :name) ON CONFLICT DO NOTHING"
                ),
                {"id": oid, "slug": slug, "name": slug},
            )
            await conn.execute(
                text(
                    "INSERT INTO organisation_memberships "
                    "(id, user_id, organisation_id, org_role) "
                    "VALUES (:mid, :uid, :oid, 'owner') ON CONFLICT DO NOTHING"
                ),
                {"mid": uuid.uuid4(), "uid": uid, "oid": oid},
            )
            await conn.execute(
                text(
                    "INSERT INTO workspaces (id, organisation_id, slug, display_name) "
                    "VALUES (:id, :oid, :slug, :name) ON CONFLICT DO NOTHING"
                ),
                {
                    "id": wid,
                    "oid": oid,
                    "slug": f"ws-{wid.hex[:8]}",
                    "name": f"Workspace {wid.hex[:8]}",
                },
            )
            await _set_workspace_context(conn, oid, wid)
            await conn.execute(
                text(
                    "INSERT INTO knowledge_sources "
                    "(id, organisation_id, workspace_id, source_type, display_name) "
                    "VALUES (:id, :oid, :wid, 'manual_upload', :name) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"id": sid, "oid": oid, "wid": wid, "name": f"src-{sid.hex[:8]}"},
            )

    yield org_a, org_b, user_a, user_b, ws_a, ws_b, src_a, src_b

    for oid, uid in ((org_a, user_a), (org_b, user_b)):
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_organisation_id', :oid, true)"),
                {"oid": str(oid)},
            )
            await conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(uid)},
            )
            await conn.execute(text("DELETE FROM organisations WHERE id = :id"), {"id": oid})
        _ORG_DEFAULT_WORKSPACE.pop(oid, None)

    async with engine.begin() as conn:
        for uid in (user_a, user_b):
            await conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": uid})


# ---------------------------------------------------------------------------
# RLS2A-01  Tenant A cannot SELECT knowledge_sources owned by tenant B
# ---------------------------------------------------------------------------


async def test_rls2a_01_cross_tenant_source_invisible(
    engine: AsyncEngine, two_tenants: Any
) -> None:
    org_a, org_b, *_ = two_tenants
    _src_a, src_b = two_tenants[6], two_tenants[7]
    async with engine.connect() as conn:
        await _set_org_context(conn, org_a)
        result = await conn.execute(
            text("SELECT id FROM knowledge_sources WHERE id = :sid"),
            {"sid": src_b},
        )
        rows = result.fetchall()
    assert rows == [], "RLS must hide Tenant B's source from Tenant A"


# ---------------------------------------------------------------------------
# RLS2A-02  Tenant A cannot INSERT knowledge_sources with Tenant B's org_id
# ---------------------------------------------------------------------------


async def test_rls2a_02_insert_wrong_org_rejected(engine: AsyncEngine, two_tenants: Any) -> None:
    org_a, org_b, _ua, _ub, ws_a, ws_b, *_ = two_tenants
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        await _set_org_context(conn, org_a)
        with pytest.raises(Exception):  # IntegrityError (RLS WITH CHECK) or similar
            await conn.execute(
                text(
                    "INSERT INTO knowledge_sources "
                    "(id, organisation_id, workspace_id, source_type, display_name) "
                    "VALUES (:id, :oid, :wid, 'manual_upload', 'bad') "
                ),
                {"id": uuid.uuid4(), "oid": org_b, "wid": ws_b},
            )
        await conn.execute(text("ROLLBACK"))


# ---------------------------------------------------------------------------
# RLS2A-03  Tenant A cannot UPDATE knowledge_sources owned by tenant B
# ---------------------------------------------------------------------------


async def test_rls2a_03_update_cross_tenant_noop(engine: AsyncEngine, two_tenants: Any) -> None:
    org_a, org_b, *_ = two_tenants
    src_b = two_tenants[7]
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        await _set_org_context(conn, org_a)
        result = await conn.execute(
            text(
                "UPDATE knowledge_sources SET display_name = 'hacked' WHERE id = :sid RETURNING id"
            ),
            {"sid": src_b},
        )
        rows = result.fetchall()
        await conn.execute(text("ROLLBACK"))
    assert rows == [], "RLS must prevent Tenant A from updating Tenant B's source"


# ---------------------------------------------------------------------------
# RLS2A-04  Tenant A cannot DELETE knowledge_sources owned by tenant B
# ---------------------------------------------------------------------------


async def test_rls2a_04_delete_cross_tenant_noop(engine: AsyncEngine, two_tenants: Any) -> None:
    org_a, org_b, *_ = two_tenants
    src_b = two_tenants[7]
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        await _set_org_context(conn, org_a)
        result = await conn.execute(
            text("DELETE FROM knowledge_sources WHERE id = :sid RETURNING id"),
            {"sid": src_b},
        )
        rows = result.fetchall()
        await conn.execute(text("ROLLBACK"))
    assert rows == [], "RLS must prevent Tenant A from deleting Tenant B's source"


# ---------------------------------------------------------------------------
# RLS2A-05  Empty context (fail-closed) → zero rows in knowledge_sources
# ---------------------------------------------------------------------------


async def test_rls2a_05_empty_context_fail_closed(engine: AsyncEngine, two_tenants: Any) -> None:
    async with engine.connect() as conn:
        await _clear_org_context(conn)
        result = await conn.execute(text("SELECT id FROM knowledge_sources"))
        rows = result.fetchall()
    assert rows == [], "Empty context must show zero rows (NULLIF fail-closed)"


# ---------------------------------------------------------------------------
# RLS2A-06  NULL GUC → NULLIF converts to NULL → no match → zero rows
# ---------------------------------------------------------------------------


async def test_rls2a_06_null_context_fail_closed(engine: AsyncEngine, two_tenants: Any) -> None:
    async with engine.connect() as conn:
        # Reset GUC to the postgres-level NULL (not set).
        await conn.execute(text("SELECT set_config('app.current_organisation_id', '', true)"))
        result = await conn.execute(text("SELECT id FROM knowledge_sources"))
        rows = result.fetchall()
    assert rows == [], "NULL/empty context must show zero rows (fail-closed)"


# ---------------------------------------------------------------------------
# RLS2A-07  Correct context → SELECT returns own knowledge_sources
# ---------------------------------------------------------------------------


async def test_rls2a_07_correct_context_returns_own_sources(
    engine: AsyncEngine, two_tenants: Any
) -> None:
    org_a, org_b, *_ = two_tenants
    src_a, src_b = two_tenants[6], two_tenants[7]
    async with engine.connect() as conn:
        ids_a = await _select_source_ids(conn, org_a)
    assert src_a in ids_a
    assert src_b not in ids_a


# ---------------------------------------------------------------------------
# RLS2A-08  Tenant A cannot SELECT knowledge_documents owned by tenant B
# ---------------------------------------------------------------------------


async def test_rls2a_08_cross_tenant_document_invisible(
    engine: AsyncEngine, two_tenants: Any
) -> None:
    org_a, org_b, _ua, _ub, ws_a, ws_b, src_a, src_b = two_tenants
    doc_b = uuid.uuid4()
    async with engine.begin() as conn:
        await _set_workspace_context(conn, org_b, ws_b)
        await conn.execute(
            text(
                "INSERT INTO knowledge_documents "
                "(id, organisation_id, workspace_id, source_id, original_filename, media_type) "
                "VALUES (:id, :oid, :wid, :sid, 'doc.txt', 'text/plain') ON CONFLICT DO NOTHING"
            ),
            {"id": doc_b, "oid": org_b, "wid": ws_b, "sid": src_b},
        )

    async with engine.connect() as conn:
        await _set_org_context(conn, org_a)
        result = await conn.execute(
            text("SELECT id FROM knowledge_documents WHERE id = :id"),
            {"id": doc_b},
        )
        rows = result.fetchall()

    # Cleanup
    async with engine.begin() as conn:
        await _set_workspace_context(conn, org_b, ws_b)
        await conn.execute(text("DELETE FROM knowledge_documents WHERE id = :id"), {"id": doc_b})

    assert rows == [], "RLS must hide Tenant B's document from Tenant A"


# ---------------------------------------------------------------------------
# RLS2A-09  Correct context → SELECT returns own knowledge_documents
# ---------------------------------------------------------------------------


async def test_rls2a_09_correct_context_returns_own_documents(
    engine: AsyncEngine, two_tenants: Any
) -> None:
    org_a, _ob, _ua, _ub, ws_a, _wb, src_a, _sb = two_tenants
    doc_a = uuid.uuid4()
    async with engine.begin() as conn:
        await _set_workspace_context(conn, org_a, ws_a)
        await conn.execute(
            text(
                "INSERT INTO knowledge_documents "
                "(id, organisation_id, workspace_id, source_id, original_filename, media_type) "
                "VALUES (:id, :oid, :wid, :sid, 'mine.txt', 'text/plain') ON CONFLICT DO NOTHING"
            ),
            {"id": doc_a, "oid": org_a, "wid": ws_a, "sid": src_a},
        )

    async with engine.connect() as conn:
        await _set_workspace_context(conn, org_a, ws_a)
        result = await conn.execute(
            text("SELECT id FROM knowledge_documents WHERE id = :id"),
            {"id": doc_a},
        )
        rows = result.fetchall()

    async with engine.begin() as conn:
        await _set_workspace_context(conn, org_a, ws_a)
        await conn.execute(text("DELETE FROM knowledge_documents WHERE id = :id"), {"id": doc_a})

    assert len(rows) == 1, "Correct context must return own document"


# ---------------------------------------------------------------------------
# Helper: seed a document + version + job + chunk for cross-table RLS tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def tenant_knowledge_stack(engine: AsyncEngine, two_tenants: Any):
    """Seed a complete knowledge stack for both tenants under matching RLS context."""
    org_a, org_b, _ua, _ub, ws_a, ws_b, src_a, src_b = two_tenants
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
    ver_a, ver_b = uuid.uuid4(), uuid.uuid4()
    job_a, job_b = uuid.uuid4(), uuid.uuid4()
    chunk_a, chunk_b = uuid.uuid4(), uuid.uuid4()
    emb_a, emb_b = uuid.uuid4(), uuid.uuid4()
    sha = hashlib.sha256(b"test").hexdigest()
    sample_vec = json.dumps([0.1] * 8)

    for oid, wid, sid, did, vid, jid, cid, eid in (
        (org_a, ws_a, src_a, doc_a, ver_a, job_a, chunk_a, emb_a),
        (org_b, ws_b, src_b, doc_b, ver_b, job_b, chunk_b, emb_b),
    ):
        async with engine.begin() as conn:
            await _set_workspace_context(conn, oid, wid)
            await conn.execute(
                text(
                    "INSERT INTO knowledge_documents "
                    "(id, organisation_id, workspace_id, source_id, original_filename, media_type) "
                    "VALUES (:id, :oid, :wid, :sid, 'f.txt', 'text/plain') ON CONFLICT DO NOTHING"
                ),
                {"id": did, "oid": oid, "wid": wid, "sid": sid},
            )
            key = f"{oid}/{wid}/{did}/{vid}"
            await conn.execute(
                text(
                    "INSERT INTO knowledge_document_versions "
                    "(id, document_id, organisation_id, workspace_id, version_number, "
                    "content_sha256, size_bytes, media_type, storage_key) "
                    "VALUES (:id, :did, :oid, :wid, 1, :sha, 4, 'text/plain', :key) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"id": vid, "did": did, "oid": oid, "wid": wid, "sha": sha, "key": key},
            )
            await conn.execute(
                text(
                    "INSERT INTO knowledge_ingestion_jobs "
                    "(id, version_id, document_id, organisation_id, workspace_id, status, idempotency_key) "
                    "VALUES (:id, :vid, :did, :oid, :wid, 'queued', :ikey) ON CONFLICT DO NOTHING"
                ),
                {
                    "id": jid,
                    "vid": vid,
                    "did": did,
                    "oid": oid,
                    "wid": wid,
                    "ikey": f"ikey-{jid.hex[:8]}",
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO knowledge_chunks "
                    "(id, version_id, organisation_id, workspace_id, chunk_index, chunk_text, content_sha256, token_count) "
                    "VALUES (:id, :vid, :oid, :wid, 0, 'test', :sha, 1) ON CONFLICT DO NOTHING"
                ),
                {"id": cid, "vid": vid, "oid": oid, "wid": wid, "sha": sha},
            )
            await conn.execute(
                text(
                    "INSERT INTO knowledge_chunk_embeddings "
                    "(id, chunk_id, organisation_id, workspace_id, model_id, embedding, dimensions) "
                    "VALUES (:id, :cid, :oid, :wid, 'deterministic-test-v1', :emb, 8) ON CONFLICT DO NOTHING"
                ),
                {"id": eid, "cid": cid, "oid": oid, "wid": wid, "emb": sample_vec},
            )

    yield dict(
        org_a=org_a,
        org_b=org_b,
        ws_a=ws_a,
        ws_b=ws_b,
        src_a=src_a,
        src_b=src_b,
        doc_a=doc_a,
        doc_b=doc_b,
        ver_a=ver_a,
        ver_b=ver_b,
        job_a=job_a,
        job_b=job_b,
        chunk_a=chunk_a,
        chunk_b=chunk_b,
        emb_a=emb_a,
        emb_b=emb_b,
    )


# ---------------------------------------------------------------------------
# RLS2A-10  Tenant A cannot SELECT knowledge_document_versions owned by tenant B
# ---------------------------------------------------------------------------


async def test_rls2a_10_cross_tenant_version_invisible(
    engine: AsyncEngine, tenant_knowledge_stack: dict
) -> None:
    s = tenant_knowledge_stack
    async with engine.connect() as conn:
        await _set_workspace_context(conn, s["org_a"], s["ws_a"])
        result = await conn.execute(
            text("SELECT id FROM knowledge_document_versions WHERE id = :id"),
            {"id": s["ver_b"]},
        )
        rows = result.fetchall()
    assert rows == [], "RLS must hide Tenant B's version from Tenant A"


# ---------------------------------------------------------------------------
# RLS2A-11  Correct context → SELECT returns own knowledge_document_versions
# ---------------------------------------------------------------------------


async def test_rls2a_11_correct_context_own_versions(
    engine: AsyncEngine, tenant_knowledge_stack: dict
) -> None:
    s = tenant_knowledge_stack
    async with engine.connect() as conn:
        await _set_workspace_context(conn, s["org_a"], s["ws_a"])
        result = await conn.execute(
            text("SELECT id FROM knowledge_document_versions WHERE id = :id"),
            {"id": s["ver_a"]},
        )
        rows = result.fetchall()
    assert len(rows) == 1, "Correct context must return own version"


# ---------------------------------------------------------------------------
# RLS2A-12  Tenant A cannot SELECT knowledge_ingestion_jobs owned by tenant B
# ---------------------------------------------------------------------------


async def test_rls2a_12_cross_tenant_job_invisible(
    engine: AsyncEngine, tenant_knowledge_stack: dict
) -> None:
    s = tenant_knowledge_stack
    async with engine.connect() as conn:
        await _set_workspace_context(conn, s["org_a"], s["ws_a"])
        result = await conn.execute(
            text("SELECT id FROM knowledge_ingestion_jobs WHERE id = :id"),
            {"id": s["job_b"]},
        )
        rows = result.fetchall()
    assert rows == [], "RLS must hide Tenant B's ingestion job from Tenant A"


# ---------------------------------------------------------------------------
# RLS2A-13  Correct context → SELECT returns own knowledge_ingestion_jobs
# ---------------------------------------------------------------------------


async def test_rls2a_13_correct_context_own_jobs(
    engine: AsyncEngine, tenant_knowledge_stack: dict
) -> None:
    s = tenant_knowledge_stack
    async with engine.connect() as conn:
        await _set_workspace_context(conn, s["org_a"], s["ws_a"])
        result = await conn.execute(
            text("SELECT id FROM knowledge_ingestion_jobs WHERE id = :id"),
            {"id": s["job_a"]},
        )
        rows = result.fetchall()
    assert len(rows) == 1, "Correct context must return own ingestion job"


# ---------------------------------------------------------------------------
# RLS2A-14  Tenant A cannot SELECT knowledge_chunks owned by tenant B
# ---------------------------------------------------------------------------


async def test_rls2a_14_cross_tenant_chunk_invisible(
    engine: AsyncEngine, tenant_knowledge_stack: dict
) -> None:
    s = tenant_knowledge_stack
    async with engine.connect() as conn:
        await _set_workspace_context(conn, s["org_a"], s["ws_a"])
        result = await conn.execute(
            text("SELECT id FROM knowledge_chunks WHERE id = :id"),
            {"id": s["chunk_b"]},
        )
        rows = result.fetchall()
    assert rows == [], "RLS must hide Tenant B's chunk from Tenant A"


# ---------------------------------------------------------------------------
# RLS2A-15  Correct context → SELECT returns own knowledge_chunks
# ---------------------------------------------------------------------------


async def test_rls2a_15_correct_context_own_chunks(
    engine: AsyncEngine, tenant_knowledge_stack: dict
) -> None:
    s = tenant_knowledge_stack
    async with engine.connect() as conn:
        await _set_workspace_context(conn, s["org_a"], s["ws_a"])
        result = await conn.execute(
            text("SELECT id FROM knowledge_chunks WHERE id = :id"),
            {"id": s["chunk_a"]},
        )
        rows = result.fetchall()
    assert len(rows) == 1, "Correct context must return own chunk"


# ---------------------------------------------------------------------------
# RLS2A-16  Tenant A cannot SELECT knowledge_chunk_embeddings owned by tenant B
# ---------------------------------------------------------------------------


async def test_rls2a_16_cross_tenant_embedding_invisible(
    engine: AsyncEngine, tenant_knowledge_stack: dict
) -> None:
    s = tenant_knowledge_stack
    async with engine.connect() as conn:
        await _set_workspace_context(conn, s["org_a"], s["ws_a"])
        result = await conn.execute(
            text("SELECT id FROM knowledge_chunk_embeddings WHERE id = :id"),
            {"id": s["emb_b"]},
        )
        rows = result.fetchall()
    assert rows == [], "RLS must hide Tenant B's embedding from Tenant A"


# ---------------------------------------------------------------------------
# RLS2A-17  Correct context → SELECT returns own knowledge_chunk_embeddings
# ---------------------------------------------------------------------------


async def test_rls2a_17_correct_context_own_embeddings(
    engine: AsyncEngine, tenant_knowledge_stack: dict
) -> None:
    s = tenant_knowledge_stack
    async with engine.connect() as conn:
        await _set_workspace_context(conn, s["org_a"], s["ws_a"])
        result = await conn.execute(
            text("SELECT id FROM knowledge_chunk_embeddings WHERE id = :id"),
            {"id": s["emb_a"]},
        )
        rows = result.fetchall()
    assert len(rows) == 1, "Correct context must return own embedding"


# ---------------------------------------------------------------------------
# RLS2A-18  Cross-org workspace reference is rejected by composite FK
# ---------------------------------------------------------------------------


async def test_rls2a_18_composite_fk_prevents_cross_tenant_workspace(
    engine: AsyncEngine, two_tenants: Any
) -> None:
    """
    Attempting to INSERT a knowledge_source with org_a but workspace_id of org_b
    must fail: composite FK (workspace_id, organisation_id) → workspaces(id, organisation_id)
    ensures workspace belongs to the same org.
    """
    org_a, org_b, *_ = two_tenants
    ws_b = two_tenants[5]  # workspace of org_b

    with pytest.raises(Exception):  # IntegrityError from FK violation
        async with engine.begin() as conn:
            await _set_workspace_context(conn, org_a, two_tenants[4])
            await conn.execute(
                text(
                    "INSERT INTO knowledge_sources "
                    "(id, organisation_id, workspace_id, source_type, display_name) "
                    "VALUES (:id, :oid, :wid, 'manual_upload', 'cross-tenant-attempt')"
                ),
                {"id": uuid.uuid4(), "oid": org_a, "wid": ws_b},
            )


# ---------------------------------------------------------------------------
# RLS2A-19  Storage keys are UUID paths — no filename or path traversal
# ---------------------------------------------------------------------------


def test_rls2a_19_storage_key_format() -> None:
    """
    A storage_key generated by BlobStore.generate_key must:
    - consist of exactly 4 UUID segments separated by '/'
    - not contain any filename extension, '..', or absolute path components
    """
    from app.knowledge.blob_store import LocalFilesystemBlobStore

    org_id = uuid.uuid4()
    ws_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    ver_id = uuid.uuid4()

    key = LocalFilesystemBlobStore.generate_key(org_id, ws_id, doc_id, ver_id)
    parts = key.split("/")

    assert len(parts) == 4, "storage_key must have exactly 4 segments"
    for part in parts:
        # Each part must be a valid UUID string
        uuid.UUID(part)  # raises ValueError if not a valid UUID

    assert ".." not in key
    assert not key.startswith("/")
    assert "passwd" not in key
    assert ".txt" not in key
    assert ".pdf" not in key


# ---------------------------------------------------------------------------
# RLS2A-20  GLOBAL_EVENT_TYPES unchanged: still exactly 4 members after Phase 2A
# ---------------------------------------------------------------------------


def test_rls2a_20_global_event_types_unchanged() -> None:
    """
    Phase 2A audit events go through emit_transactional (not GLOBAL).
    GLOBAL_EVENT_TYPES must remain exactly the 4 pre-auth events from Phase 1A.
    """
    expected = frozenset(
        {
            "auth.login_failed",
            "auth.pre_auth_session_expired",
            "auth.pre_auth_session_reused",
            "auth.token_reuse_detected",
        }
    )
    assert expected == GLOBAL_EVENT_TYPES, (
        f"GLOBAL_EVENT_TYPES must not have been extended. Current: {sorted(GLOBAL_EVENT_TYPES)}"
    )


# ---------------------------------------------------------------------------
# RLS2A-21  Tenant A cannot INSERT knowledge_documents with Tenant B's org_id
# ---------------------------------------------------------------------------


async def test_rls2a_21_insert_document_wrong_org_rejected(
    engine: AsyncEngine, two_tenants: Any
) -> None:
    """
    RLS WITH CHECK on knowledge_documents rejects INSERTs where organisation_id
    does not match the current GUC context.
    """
    org_a, org_b, _ua, _ub, ws_a, ws_b, _src_a, src_b = two_tenants
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        await _set_workspace_context(conn, org_a, ws_a)
        with pytest.raises(Exception):  # IntegrityError (WITH CHECK violation)
            await conn.execute(
                text(
                    "INSERT INTO knowledge_documents "
                    "(id, organisation_id, workspace_id, source_id, original_filename, media_type) "
                    "VALUES (:id, :oid, :wid, :sid, 'bad.txt', 'text/plain')"
                ),
                {"id": uuid.uuid4(), "oid": org_b, "wid": ws_b, "sid": src_b},
            )
        await conn.execute(text("ROLLBACK"))


# ---------------------------------------------------------------------------
# RLS2A-22  Tenant A cannot UPDATE knowledge_documents owned by tenant B
# ---------------------------------------------------------------------------


async def test_rls2a_22_update_cross_tenant_document_noop(
    engine: AsyncEngine, tenant_knowledge_stack: dict
) -> None:
    """
    RLS USING clause on knowledge_documents means UPDATE targeting Tenant B's
    document while running as Tenant A returns 0 affected rows.
    """
    s = tenant_knowledge_stack
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        await _set_workspace_context(conn, s["org_a"], s["ws_a"])
        result = await conn.execute(
            text(
                "UPDATE knowledge_documents SET original_filename = 'hacked.txt' "
                "WHERE id = :id RETURNING id"
            ),
            {"id": s["doc_b"]},
        )
        rows = result.fetchall()
        await conn.execute(text("ROLLBACK"))
    assert rows == [], "RLS must prevent Tenant A from updating Tenant B's document"


# ---------------------------------------------------------------------------
# RLS2A-23  Tenant A cannot DELETE knowledge_documents owned by tenant B
# ---------------------------------------------------------------------------


async def test_rls2a_23_delete_cross_tenant_document_noop(
    engine: AsyncEngine, tenant_knowledge_stack: dict
) -> None:
    """
    RLS USING clause on knowledge_documents means DELETE targeting Tenant B's
    document while running as Tenant A returns 0 affected rows.
    """
    s = tenant_knowledge_stack
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        await _set_workspace_context(conn, s["org_a"], s["ws_a"])
        result = await conn.execute(
            text("DELETE FROM knowledge_documents WHERE id = :id RETURNING id"),
            {"id": s["doc_b"]},
        )
        rows = result.fetchall()
        await conn.execute(text("ROLLBACK"))
    assert rows == [], "RLS must prevent Tenant A from deleting Tenant B's document"


# ---------------------------------------------------------------------------
# Same-org two-workspace fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def same_org_two_workspaces(engine: AsyncEngine, tables: None):
    """Create W1/W2 in one org and seed a full knowledge stack into W2."""
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    ws1_id, ws2_id = uuid.uuid4(), uuid.uuid4()
    src2_id, doc2_id, ver2_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    job2_id, chunk2_id, emb2_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sha = hashlib.sha256(b"w2-test").hexdigest()
    sample_vec = json.dumps([0.2] * 8)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, full_name, password_hash, pepper_version) "
                "VALUES (:id, :email, 'Test', 'hash', 1) ON CONFLICT DO NOTHING"
            ),
            {"id": user_id, "email": f"{user_id.hex[:8]}@test.example"},
        )

    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(org_id)},
        )
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(user_id)},
        )
        slug = f"org-sw-{org_id.hex[:8]}"
        await conn.execute(
            text("INSERT INTO organisations (id, slug, display_name) VALUES (:id, :slug, :name)"),
            {"id": org_id, "slug": slug, "name": slug},
        )
        await conn.execute(
            text(
                "INSERT INTO organisation_memberships (id, user_id, organisation_id, org_role) "
                "VALUES (:mid, :uid, :oid, 'owner')"
            ),
            {"mid": uuid.uuid4(), "uid": user_id, "oid": org_id},
        )
        for wid in (ws1_id, ws2_id):
            await conn.execute(
                text(
                    "INSERT INTO workspaces (id, organisation_id, slug, display_name) "
                    "VALUES (:id, :oid, :slug, :name)"
                ),
                {
                    "id": wid,
                    "oid": org_id,
                    "slug": f"ws-{wid.hex[:8]}",
                    "name": f"Workspace {wid.hex[:8]}",
                },
            )
        await _set_workspace_context(conn, org_id, ws2_id)
        await conn.execute(
            text(
                "INSERT INTO knowledge_sources (id, organisation_id, workspace_id, source_type, display_name) "
                "VALUES (:id, :oid, :wid, 'manual_upload', 'w2-source')"
            ),
            {"id": src2_id, "oid": org_id, "wid": ws2_id},
        )
        await conn.execute(
            text(
                "INSERT INTO knowledge_documents (id, organisation_id, workspace_id, source_id, original_filename, media_type) "
                "VALUES (:id, :oid, :wid, :sid, 'w2.txt', 'text/plain')"
            ),
            {"id": doc2_id, "oid": org_id, "wid": ws2_id, "sid": src2_id},
        )
        key = f"{org_id}/{ws2_id}/{doc2_id}/{ver2_id}"
        await conn.execute(
            text(
                "INSERT INTO knowledge_document_versions (id, document_id, organisation_id, workspace_id, version_number, content_sha256, size_bytes, media_type, storage_key) "
                "VALUES (:id, :did, :oid, :wid, 1, :sha, 4, 'text/plain', :key)"
            ),
            {"id": ver2_id, "did": doc2_id, "oid": org_id, "wid": ws2_id, "sha": sha, "key": key},
        )
        await conn.execute(
            text(
                "INSERT INTO knowledge_ingestion_jobs (id, version_id, document_id, organisation_id, workspace_id, status, idempotency_key) "
                "VALUES (:id, :vid, :did, :oid, :wid, 'queued', :ikey)"
            ),
            {
                "id": job2_id,
                "vid": ver2_id,
                "did": doc2_id,
                "oid": org_id,
                "wid": ws2_id,
                "ikey": f"ikey-{job2_id.hex[:8]}",
            },
        )
        await conn.execute(
            text(
                "INSERT INTO knowledge_chunks (id, version_id, organisation_id, workspace_id, chunk_index, chunk_text, content_sha256, token_count) "
                "VALUES (:id, :vid, :oid, :wid, 0, 'w2 content', :sha, 1)"
            ),
            {"id": chunk2_id, "vid": ver2_id, "oid": org_id, "wid": ws2_id, "sha": sha},
        )
        await conn.execute(
            text(
                "INSERT INTO knowledge_chunk_embeddings (id, chunk_id, organisation_id, workspace_id, model_id, embedding, dimensions) "
                "VALUES (:id, :cid, :oid, :wid, 'deterministic-test-v1', :emb, 8)"
            ),
            {"id": emb2_id, "cid": chunk2_id, "oid": org_id, "wid": ws2_id, "emb": sample_vec},
        )

    yield dict(
        org_id=org_id,
        user_id=user_id,
        ws1_id=ws1_id,
        ws2_id=ws2_id,
        src2_id=src2_id,
        doc2_id=doc2_id,
        ver2_id=ver2_id,
        job2_id=job2_id,
        chunk2_id=chunk2_id,
        emb2_id=emb2_id,
    )

    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(org_id)},
        )
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": str(user_id)}
        )
        await conn.execute(text("DELETE FROM organisations WHERE id = :id"), {"id": org_id})
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


# ---------------------------------------------------------------------------
# RLS2A-24  Same-org W1 context: SELECT W2 rows → zero (all 6 knowledge tables)
# ---------------------------------------------------------------------------


async def test_rls2a_24_same_org_select_w2_invisible(
    engine: AsyncEngine, same_org_two_workspaces: dict
) -> None:
    """
    Same organisation, W1 context.  All six knowledge tables seeded into W2
    must return zero rows when queried via W1 workspace context.

    TABLE                       | SELECT | INSERT | UPDATE | DELETE
    knowledge_sources           | 0 rows | denied | 0 rows | 0 rows   ← this test: SELECT
    knowledge_documents         | 0 rows | denied | 0 rows | 0 rows
    knowledge_document_versions | 0 rows | denied | 0 rows | 0 rows
    knowledge_ingestion_jobs    | 0 rows | denied | 0 rows | 0 rows
    knowledge_chunks            | 0 rows | denied | 0 rows | 0 rows
    knowledge_chunk_embeddings  | 0 rows | denied | 0 rows | 0 rows
    """
    s = same_org_two_workspaces
    checks = [
        ("knowledge_sources", "src2_id"),
        ("knowledge_documents", "doc2_id"),
        ("knowledge_document_versions", "ver2_id"),
        ("knowledge_ingestion_jobs", "job2_id"),
        ("knowledge_chunks", "chunk2_id"),
        ("knowledge_chunk_embeddings", "emb2_id"),
    ]
    async with engine.connect() as conn:
        await _set_workspace_context(conn, s["org_id"], s["ws1_id"])
        for table, key in checks:
            result = await conn.execute(
                text(f"SELECT id FROM {table} WHERE id = :id"),
                {"id": s[key]},
            )
            rows = result.fetchall()
            assert rows == [], (
                f"RLS must hide {table} row from same-org W2 when W1 context is active"
            )


# ---------------------------------------------------------------------------
# RLS2A-25  Same-org W1 context: INSERT into W2 → rejected (all 6 tables)
# ---------------------------------------------------------------------------


async def test_rls2a_25_same_org_insert_w2_rejected(
    engine: AsyncEngine, same_org_two_workspaces: dict
) -> None:
    """
    Same organisation, W1 context.  Attempting to INSERT a row with workspace_id=W2
    must be rejected by the RLS WITH CHECK clause for all six knowledge tables.

    The expected exception is sqlalchemy.exc.ProgrammingError — specifically the
    PostgreSQL ERROR 'new row violates row-level security policy' (SQLSTATE 42501).
    A broad Exception would also catch unrelated failures (FK violations, type
    errors, etc.) and give a false signal that RLS is working.  ProgrammingError
    proves the rejection came from the RLS WITH CHECK predicate.

    TABLE                       | SELECT | INSERT | UPDATE | DELETE
    knowledge_sources           | 0 rows | denied | 0 rows | 0 rows   ← this test: INSERT
    knowledge_documents         | 0 rows | denied | 0 rows | 0 rows
    knowledge_document_versions | 0 rows | denied | 0 rows | 0 rows
    knowledge_ingestion_jobs    | 0 rows | denied | 0 rows | 0 rows
    knowledge_chunks            | 0 rows | denied | 0 rows | 0 rows
    knowledge_chunk_embeddings  | 0 rows | denied | 0 rows | 0 rows
    """
    s = same_org_two_workspaces
    sha = hashlib.sha256(b"bad-insert").hexdigest()

    # knowledge_sources
    with pytest.raises(ProgrammingError, match="row-level security"):
        async with engine.connect() as conn:
            await conn.execute(text("BEGIN"))
            await _set_workspace_context(conn, s["org_id"], s["ws1_id"])
            await conn.execute(
                text(
                    "INSERT INTO knowledge_sources "
                    "(id, organisation_id, workspace_id, source_type, display_name) "
                    "VALUES (:id, :oid, :wid, 'manual_upload', 'bad-w2')"
                ),
                {"id": uuid.uuid4(), "oid": s["org_id"], "wid": s["ws2_id"]},
            )
            await conn.execute(text("ROLLBACK"))

    # knowledge_documents
    with pytest.raises(ProgrammingError, match="row-level security"):
        async with engine.connect() as conn:
            await conn.execute(text("BEGIN"))
            await _set_workspace_context(conn, s["org_id"], s["ws1_id"])
            await conn.execute(
                text(
                    "INSERT INTO knowledge_documents "
                    "(id, organisation_id, workspace_id, source_id, original_filename, media_type) "
                    "VALUES (:id, :oid, :wid, :sid, 'bad.txt', 'text/plain')"
                ),
                {
                    "id": uuid.uuid4(),
                    "oid": s["org_id"],
                    "wid": s["ws2_id"],
                    "sid": s["src2_id"],
                },
            )
            await conn.execute(text("ROLLBACK"))

    # knowledge_document_versions — also uses src2_id / doc2_id from W2
    new_ver_id = uuid.uuid4()
    with pytest.raises(ProgrammingError, match="row-level security"):
        async with engine.connect() as conn:
            await conn.execute(text("BEGIN"))
            await _set_workspace_context(conn, s["org_id"], s["ws1_id"])
            await conn.execute(
                text(
                    "INSERT INTO knowledge_document_versions "
                    "(id, document_id, organisation_id, workspace_id, version_number, "
                    "content_sha256, size_bytes, media_type, storage_key) "
                    "VALUES (:id, :did, :oid, :wid, 99, :sha, 1, 'text/plain', :key)"
                ),
                {
                    "id": new_ver_id,
                    "did": s["doc2_id"],
                    "oid": s["org_id"],
                    "wid": s["ws2_id"],
                    "sha": sha,
                    "key": f"{s['org_id']}/{s['ws2_id']}/{s['doc2_id']}/{new_ver_id}",
                },
            )
            await conn.execute(text("ROLLBACK"))

    # knowledge_ingestion_jobs
    with pytest.raises(ProgrammingError, match="row-level security"):
        async with engine.connect() as conn:
            await conn.execute(text("BEGIN"))
            await _set_workspace_context(conn, s["org_id"], s["ws1_id"])
            new_jid = uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO knowledge_ingestion_jobs "
                    "(id, version_id, document_id, organisation_id, workspace_id, "
                    "status, idempotency_key) "
                    "VALUES (:id, :vid, :did, :oid, :wid, 'queued', :ikey)"
                ),
                {
                    "id": new_jid,
                    "vid": s["ver2_id"],
                    "did": s["doc2_id"],
                    "oid": s["org_id"],
                    "wid": s["ws2_id"],
                    "ikey": f"bad-{new_jid.hex[:8]}",
                },
            )
            await conn.execute(text("ROLLBACK"))

    # knowledge_chunks
    with pytest.raises(ProgrammingError, match="row-level security"):
        async with engine.connect() as conn:
            await conn.execute(text("BEGIN"))
            await _set_workspace_context(conn, s["org_id"], s["ws1_id"])
            await conn.execute(
                text(
                    "INSERT INTO knowledge_chunks "
                    "(id, version_id, organisation_id, workspace_id, chunk_index, "
                    "chunk_text, content_sha256, token_count) "
                    "VALUES (:id, :vid, :oid, :wid, 99, 'bad', :sha, 1)"
                ),
                {
                    "id": uuid.uuid4(),
                    "vid": s["ver2_id"],
                    "oid": s["org_id"],
                    "wid": s["ws2_id"],
                    "sha": sha,
                },
            )
            await conn.execute(text("ROLLBACK"))

    # knowledge_chunk_embeddings
    with pytest.raises(ProgrammingError, match="row-level security"):
        async with engine.connect() as conn:
            await conn.execute(text("BEGIN"))
            await _set_workspace_context(conn, s["org_id"], s["ws1_id"])
            await conn.execute(
                text(
                    "INSERT INTO knowledge_chunk_embeddings "
                    "(id, chunk_id, organisation_id, workspace_id, model_id, embedding, dimensions) "
                    "VALUES (:id, :cid, :oid, :wid, 'bad-model', :emb, 8)"
                ),
                {
                    "id": uuid.uuid4(),
                    "cid": s["chunk2_id"],
                    "oid": s["org_id"],
                    "wid": s["ws2_id"],
                    "emb": json.dumps([0.9] * 8),
                },
            )
            await conn.execute(text("ROLLBACK"))


# ---------------------------------------------------------------------------
# RLS2A-26  Same-org W1 context: UPDATE W2 rows → 0 affected (all 6 tables)
# ---------------------------------------------------------------------------


async def test_rls2a_26_same_org_update_w2_noop(
    engine: AsyncEngine, same_org_two_workspaces: dict
) -> None:
    """
    Same organisation, W1 context.  UPDATE targeting W2 rows must return
    zero affected rows — the USING clause makes W2 rows invisible.

    TABLE                       | SELECT | INSERT | UPDATE | DELETE
    knowledge_sources           | 0 rows | denied | 0 rows | 0 rows   ← this test: UPDATE
    knowledge_documents         | 0 rows | denied | 0 rows | 0 rows
    knowledge_document_versions | 0 rows | denied | 0 rows | 0 rows
    knowledge_ingestion_jobs    | 0 rows | denied | 0 rows | 0 rows
    knowledge_chunks            | 0 rows | denied | 0 rows | 0 rows
    knowledge_chunk_embeddings  | 0 rows | denied | 0 rows | 0 rows
    """
    s = same_org_two_workspaces

    updates = [
        (
            "knowledge_sources",
            "UPDATE knowledge_sources SET display_name = 'hacked' WHERE id = :id RETURNING id",
            s["src2_id"],
        ),
        (
            "knowledge_documents",
            "UPDATE knowledge_documents SET original_filename = 'hacked.txt' WHERE id = :id RETURNING id",
            s["doc2_id"],
        ),
        (
            "knowledge_document_versions",
            "UPDATE knowledge_document_versions SET size_bytes = 9999 WHERE id = :id RETURNING id",
            s["ver2_id"],
        ),
        (
            "knowledge_ingestion_jobs",
            "UPDATE knowledge_ingestion_jobs SET status = 'failed' WHERE id = :id RETURNING id",
            s["job2_id"],
        ),
        (
            "knowledge_chunks",
            "UPDATE knowledge_chunks SET chunk_text = 'hacked' WHERE id = :id RETURNING id",
            s["chunk2_id"],
        ),
        (
            "knowledge_chunk_embeddings",
            "UPDATE knowledge_chunk_embeddings SET model_id = 'hacked' WHERE id = :id RETURNING id",
            s["emb2_id"],
        ),
    ]

    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        await _set_workspace_context(conn, s["org_id"], s["ws1_id"])
        for table, stmt, row_id in updates:
            result = await conn.execute(text(stmt), {"id": row_id})
            rows = result.fetchall()
            assert rows == [], f"RLS USING must prevent W1 context from updating {table} rows in W2"
        await conn.execute(text("ROLLBACK"))


# ---------------------------------------------------------------------------
# RLS2A-27  Same-org W1 context: DELETE W2 rows → 0 affected (all 6 tables)
# ---------------------------------------------------------------------------


async def test_rls2a_27_same_org_delete_w2_noop(
    engine: AsyncEngine, same_org_two_workspaces: dict
) -> None:
    """
    Same organisation, W1 context.  DELETE targeting W2 rows must return
    zero affected rows — the USING clause makes W2 rows invisible.

    TABLE                       | SELECT | INSERT | UPDATE | DELETE
    knowledge_sources           | 0 rows | denied | 0 rows | 0 rows   ← this test: DELETE
    knowledge_documents         | 0 rows | denied | 0 rows | 0 rows
    knowledge_document_versions | 0 rows | denied | 0 rows | 0 rows
    knowledge_ingestion_jobs    | 0 rows | denied | 0 rows | 0 rows
    knowledge_chunks            | 0 rows | denied | 0 rows | 0 rows
    knowledge_chunk_embeddings  | 0 rows | denied | 0 rows | 0 rows
    """
    s = same_org_two_workspaces

    # Delete in FK-safe order: embeddings → chunks → jobs → versions → documents → sources
    deletes = [
        (
            "knowledge_chunk_embeddings",
            "DELETE FROM knowledge_chunk_embeddings WHERE id = :id RETURNING id",
            s["emb2_id"],
        ),
        (
            "knowledge_chunks",
            "DELETE FROM knowledge_chunks WHERE id = :id RETURNING id",
            s["chunk2_id"],
        ),
        (
            "knowledge_ingestion_jobs",
            "DELETE FROM knowledge_ingestion_jobs WHERE id = :id RETURNING id",
            s["job2_id"],
        ),
        (
            "knowledge_document_versions",
            "DELETE FROM knowledge_document_versions WHERE id = :id RETURNING id",
            s["ver2_id"],
        ),
        (
            "knowledge_documents",
            "DELETE FROM knowledge_documents WHERE id = :id RETURNING id",
            s["doc2_id"],
        ),
        (
            "knowledge_sources",
            "DELETE FROM knowledge_sources WHERE id = :id RETURNING id",
            s["src2_id"],
        ),
    ]

    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        await _set_workspace_context(conn, s["org_id"], s["ws1_id"])
        for table, stmt, row_id in deletes:
            result = await conn.execute(text(stmt), {"id": row_id})
            rows = result.fetchall()
            assert rows == [], f"RLS USING must prevent W1 context from deleting {table} rows in W2"
        await conn.execute(text("ROLLBACK"))


# ---------------------------------------------------------------------------
# RLS2A-28  Workspace context unset → zero knowledge rows
# ---------------------------------------------------------------------------


async def test_rls2a_28_workspace_context_unset_fail_closed(
    engine: AsyncEngine, same_org_two_workspaces: dict
) -> None:
    """
    Organisation GUC set, workspace GUC absent (empty string → NULLIF → NULL).
    Knowledge rows must be invisible — fail-closed.
    """
    s = same_org_two_workspaces
    async with engine.connect() as conn:
        # Set org but explicitly clear workspace GUC
        await conn.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(s["org_id"])},
        )
        await conn.execute(text("SELECT set_config('app.current_workspace_id', '', true)"))
        result = await conn.execute(text("SELECT id FROM knowledge_sources"))
        rows = result.fetchall()
    assert rows == [], "Missing workspace context must expose ZERO knowledge rows (fail-closed)"


# ---------------------------------------------------------------------------
# RLS2A-29  Workspace context empty string → zero knowledge rows
# ---------------------------------------------------------------------------


async def test_rls2a_29_workspace_context_empty_string_fail_closed(
    engine: AsyncEngine, same_org_two_workspaces: dict
) -> None:
    """
    Both GUCs are explicitly set to empty string.  NULLIF maps '' → NULL.
    The UUID cast of NULL produces NULL; NULL = uuid fails → zero rows.
    """
    _s = same_org_two_workspaces  # fixture consumed for side effects
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "SELECT set_config('app.current_organisation_id', '', true), "
                "       set_config('app.current_workspace_id', '', true)"
            )
        )
        result = await conn.execute(text("SELECT id FROM knowledge_sources"))
        rows = result.fetchall()
    assert rows == [], "Empty-string GUCs must expose zero knowledge rows (NULLIF fail-closed)"


# ---------------------------------------------------------------------------
# RLS2A-30  Wrong workspace UUID → zero knowledge rows
# ---------------------------------------------------------------------------


async def test_rls2a_30_wrong_workspace_uuid_fail_closed(
    engine: AsyncEngine, same_org_two_workspaces: dict
) -> None:
    """
    Correct organisation, but workspace GUC holds a random UUID that does not
    match any seeded workspace.  RLS must return zero rows.
    """
    s = same_org_two_workspaces
    random_ws = uuid.uuid4()
    async with engine.connect() as conn:
        await _set_workspace_context(conn, s["org_id"], random_ws)
        result = await conn.execute(text("SELECT id FROM knowledge_sources"))
        rows = result.fetchall()
    assert rows == [], "Wrong workspace UUID must expose zero knowledge rows"


# ---------------------------------------------------------------------------
# RLS2A-31  Correct org + correct workspace → own rows returned
# ---------------------------------------------------------------------------


async def test_rls2a_31_correct_context_returns_w2_rows(
    engine: AsyncEngine, same_org_two_workspaces: dict
) -> None:
    """
    Correct organisation + correct workspace (W2) → W2 rows are visible.
    Verifies that the hardened policy still allows access to the correct workspace.
    """
    s = same_org_two_workspaces
    checks = [
        ("knowledge_sources", s["src2_id"]),
        ("knowledge_documents", s["doc2_id"]),
        ("knowledge_document_versions", s["ver2_id"]),
        ("knowledge_ingestion_jobs", s["job2_id"]),
        ("knowledge_chunks", s["chunk2_id"]),
        ("knowledge_chunk_embeddings", s["emb2_id"]),
    ]
    async with engine.connect() as conn:
        await _set_workspace_context(conn, s["org_id"], s["ws2_id"])
        for table, row_id in checks:
            result = await conn.execute(
                text(f"SELECT id FROM {table} WHERE id = :id"),
                {"id": row_id},
            )
            rows = result.fetchall()
            assert len(rows) == 1, f"Correct org+workspace context must return own {table} row"


# ---------------------------------------------------------------------------
# RLS2A-32  Correct workspace but wrong org → zero knowledge rows
# ---------------------------------------------------------------------------


async def test_rls2a_32_wrong_org_correct_workspace_fail_closed(
    engine: AsyncEngine, same_org_two_workspaces: dict
) -> None:
    """
    Workspace GUC matches W2 but organisation GUC is a random UUID that
    does not match any seeded organisation.  Both predicates must match;
    a wrong org alone exposes zero rows.
    """
    s = same_org_two_workspaces
    random_org = uuid.uuid4()
    async with engine.connect() as conn:
        await _set_workspace_context(conn, random_org, s["ws2_id"])
        result = await conn.execute(text("SELECT id FROM knowledge_sources"))
        rows = result.fetchall()
    assert rows == [], "Wrong org with correct workspace must expose zero knowledge rows"
