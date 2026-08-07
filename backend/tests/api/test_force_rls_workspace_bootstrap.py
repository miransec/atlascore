"""Regression: FORCE-RLS GUC bootstrap before WorkspaceMembership reads."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.deps import get_validated_workspace_context

pytestmark = pytest.mark.asyncio


def _payload(*, user_id: uuid.UUID, org_id: uuid.UUID, workspace_id: uuid.UUID) -> MagicMock:
    p = MagicMock()
    p.user_id = user_id
    p.organisation_id = org_id
    p.workspace_id = workspace_id
    return p


async def test_validated_workspace_bootstraps_rls_gucs_before_membership_query() -> None:
    """
    FORCE RLS fail-closes when tenant GUCs are unset.  Membership revalidation
    must set org/user/workspace GUCs from the verified JWT before SELECT.
    """
    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    ws_id = uuid.uuid4()
    payload = _payload(user_id=user_id, org_id=org_id, workspace_id=ws_id)

    execute_calls: list[object] = []

    async def _execute(statement, params=None):
        execute_calls.append((str(statement), params))
        result = MagicMock()
        # First three calls are set_config; fourth is membership SELECT.
        if len(execute_calls) <= 3:
            result.scalar_one_or_none.return_value = None
        else:
            result.scalar_one_or_none.return_value = MagicMock()
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)

    validated = await get_validated_workspace_context(ws_id, payload, db)
    assert validated == ws_id
    assert len(execute_calls) >= 4

    guc_sql = " ".join(sql for sql, _ in execute_calls[:3])
    assert "app.current_organisation_id" in guc_sql
    assert "app.current_user_id" in guc_sql
    assert "app.current_workspace_id" in guc_sql

    org_params = execute_calls[0][1]
    user_params = execute_calls[1][1]
    ws_params = execute_calls[2][1]
    assert org_params == {"org_id": str(org_id)}
    assert user_params == {"user_id": str(user_id)}
    assert ws_params == {"ws_id": str(ws_id)}


async def test_validated_workspace_revoked_membership_returns_403_after_guc_bootstrap() -> None:
    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    ws_id = uuid.uuid4()
    payload = _payload(user_id=user_id, org_id=org_id, workspace_id=ws_id)

    db = AsyncMock()
    empty = MagicMock()
    empty.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=empty)

    with pytest.raises(HTTPException) as exc:
        await get_validated_workspace_context(ws_id, payload, db)
    assert exc.value.status_code == 403
    assert "membership" in exc.value.detail.lower()
    # GUCs were still bootstrapped before the failed membership read.
    assert db.execute.await_count >= 4
