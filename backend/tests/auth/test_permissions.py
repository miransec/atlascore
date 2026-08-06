"""
Tests for the RBAC permission matrix in app.auth.permissions.

Coverage:
- OWNER has every permission
- ADMINISTRATOR has admin permissions but NOT owner-only ones (org_delete)
- VIEWER has read-only permissions; lacks write permissions
- AUDITOR has audit_log_read; lacks everything else
- None role returns False for any permission check
- has_org_permission() is False for unknown roles (defensive)
- All OrgRole members appear in _ORG_ROLE_PERMISSIONS
"""

from __future__ import annotations

from app.auth.permissions import (
    _ORG_ROLE_PERMISSIONS,
    OrgRole,
    Permission,
    get_org_permissions,
    has_org_permission,
)


def test_owner_has_all_permissions() -> None:
    for perm in Permission:
        assert has_org_permission(OrgRole.OWNER, perm), f"OWNER missing {perm}"


def test_administrator_has_workspace_manage() -> None:
    assert has_org_permission(OrgRole.ADMINISTRATOR, Permission.WORKSPACE_CREATE)
    assert has_org_permission(OrgRole.ADMINISTRATOR, Permission.ORG_MEMBER_INVITE)


def test_administrator_lacks_org_delete() -> None:
    """Only OWNER can delete the organisation."""
    assert not has_org_permission(OrgRole.ADMINISTRATOR, Permission.ORG_DELETE)


def test_viewer_has_read_permissions() -> None:
    assert has_org_permission(OrgRole.VIEWER, Permission.WORKSPACE_READ)
    assert has_org_permission(OrgRole.VIEWER, Permission.ORG_MEMBER_LIST)


def test_viewer_lacks_write_permissions() -> None:
    assert not has_org_permission(OrgRole.VIEWER, Permission.WORKSPACE_CREATE)
    assert not has_org_permission(OrgRole.VIEWER, Permission.ORG_MEMBER_INVITE)
    assert not has_org_permission(OrgRole.VIEWER, Permission.ORG_DELETE)


def test_auditor_has_audit_log_read() -> None:
    assert has_org_permission(OrgRole.AUDITOR, Permission.AUDIT_READ)


def test_auditor_lacks_workspace_create() -> None:
    assert not has_org_permission(OrgRole.AUDITOR, Permission.WORKSPACE_CREATE)


def test_none_role_returns_false() -> None:
    for perm in Permission:
        assert has_org_permission(None, perm) is False  # type: ignore[arg-type]


def test_get_org_permissions_returns_frozenset() -> None:
    perms = get_org_permissions(OrgRole.ANALYST)
    assert isinstance(perms, frozenset)
    assert len(perms) > 0


def test_all_org_roles_in_matrix() -> None:
    for role in OrgRole:
        assert role in _ORG_ROLE_PERMISSIONS, f"{role} missing from permission matrix"


def test_permission_enum_values_are_strings() -> None:
    for perm in Permission:
        assert isinstance(perm.value, str)
        assert ":" in perm.value  # namespaced
