"""
RBAC permission matrix — single authoritative source.

Roles are checked at the router layer (via require_permission dependency)
and the service layer.  There are no mutable DB role tables in Phase 1A.

The seven organisation roles:
  owner            — full control including ownership transfer
  administrator    — full control except ownership transfer
  workflow_builder — create and manage workflows
  analyst          — read-only access plus analytics queries
  operator         — run workflows, cannot configure
  viewer           — read-only
  auditor          — read audit log only

Workspace roles mirror organisation roles minus 'owner' (workspaces do not
have separate ownership — they are owned by the organisation).

A NULL org_role means the user is a member of the organisation but holds no
named role.  They can see the organisation in their list but cannot perform
any role-gated action.
"""

from __future__ import annotations

from enum import StrEnum


class OrgRole(StrEnum):
    OWNER = "owner"
    ADMINISTRATOR = "administrator"
    WORKFLOW_BUILDER = "workflow_builder"
    ANALYST = "analyst"
    OPERATOR = "operator"
    VIEWER = "viewer"
    AUDITOR = "auditor"


class WorkspaceRole(StrEnum):
    ADMINISTRATOR = "administrator"
    WORKFLOW_BUILDER = "workflow_builder"
    ANALYST = "analyst"
    OPERATOR = "operator"
    VIEWER = "viewer"
    AUDITOR = "auditor"


# ---------------------------------------------------------------------------
# Permission identifiers
# ---------------------------------------------------------------------------
class Permission(StrEnum):
    # Organisation management
    ORG_READ = "org:read"
    ORG_UPDATE = "org:update"
    ORG_DELETE = "org:delete"
    ORG_TRANSFER_OWNERSHIP = "org:transfer_ownership"

    # Membership management
    ORG_MEMBER_INVITE = "org:member:invite"
    ORG_MEMBER_REMOVE = "org:member:remove"
    ORG_MEMBER_ROLE_CHANGE = "org:member:role_change"
    ORG_MEMBER_LIST = "org:member:list"

    # Workspace management
    WORKSPACE_CREATE = "workspace:create"
    WORKSPACE_READ = "workspace:read"
    WORKSPACE_UPDATE = "workspace:update"
    WORKSPACE_DELETE = "workspace:delete"
    WORKSPACE_MEMBER_MANAGE = "workspace:member:manage"

    # Phase 1B — Invitations
    INVITATION_CREATE = "invitation:create"
    INVITATION_REVOKE = "invitation:revoke"
    INVITATION_LIST = "invitation:list"

    # Phase 1B — Teams
    TEAM_CREATE = "team:create"
    TEAM_UPDATE = "team:update"
    TEAM_DELETE = "team:delete"
    TEAM_READ = "team:read"
    TEAM_MEMBER_MANAGE = "team:member:manage"

    # Phase 1B — Service accounts
    SERVICE_ACCOUNT_CREATE = "service_account:create"
    SERVICE_ACCOUNT_MANAGE = "service_account:manage"
    SERVICE_ACCOUNT_READ = "service_account:read"

    # Phase 1B — API keys
    API_KEY_CREATE = "api_key:create"
    API_KEY_REVOKE = "api_key:revoke"
    API_KEY_LIST = "api_key:list"

    # Audit
    AUDIT_READ = "audit:read"

    # Analytics (Phase 3)
    ANALYTICS_QUERY = "analytics:query"

    # Workflow (Phase 4)
    WORKFLOW_CREATE = "workflow:create"
    WORKFLOW_RUN = "workflow:run"
    WORKFLOW_APPROVE = "workflow:approve"
    WORKFLOW_READ = "workflow:read"

    # Phase 2A — Knowledge management
    KNOWLEDGE_READ = "knowledge:read"
    KNOWLEDGE_SOURCE_CREATE = "knowledge:source:create"
    KNOWLEDGE_SOURCE_UPDATE = "knowledge:source:update"
    KNOWLEDGE_DOCUMENT_UPLOAD = "knowledge:document:upload"
    KNOWLEDGE_DOCUMENT_ARCHIVE = "knowledge:document:archive"
    KNOWLEDGE_INGESTION_RETRY = "knowledge:ingestion:retry"


# ---------------------------------------------------------------------------
# Hardcoded permission matrix — this is the single authoritative source.
# Any permission check in routers or services must reference this dict.
# ---------------------------------------------------------------------------
_ORG_ROLE_PERMISSIONS: dict[OrgRole, frozenset[Permission]] = {
    OrgRole.OWNER: frozenset(Permission),  # all permissions
    OrgRole.ADMINISTRATOR: frozenset(
        {
            Permission.ORG_READ,
            Permission.ORG_UPDATE,
            Permission.ORG_MEMBER_INVITE,
            Permission.ORG_MEMBER_REMOVE,
            Permission.ORG_MEMBER_ROLE_CHANGE,
            Permission.ORG_MEMBER_LIST,
            Permission.WORKSPACE_CREATE,
            Permission.WORKSPACE_READ,
            Permission.WORKSPACE_UPDATE,
            Permission.WORKSPACE_DELETE,
            Permission.WORKSPACE_MEMBER_MANAGE,
            Permission.AUDIT_READ,
            Permission.ANALYTICS_QUERY,
            Permission.WORKFLOW_CREATE,
            Permission.WORKFLOW_RUN,
            Permission.WORKFLOW_APPROVE,
            Permission.WORKFLOW_READ,
            # Phase 1B
            Permission.INVITATION_CREATE,
            Permission.INVITATION_REVOKE,
            Permission.INVITATION_LIST,
            Permission.TEAM_CREATE,
            Permission.TEAM_UPDATE,
            Permission.TEAM_DELETE,
            Permission.TEAM_READ,
            Permission.TEAM_MEMBER_MANAGE,
            Permission.SERVICE_ACCOUNT_CREATE,
            Permission.SERVICE_ACCOUNT_MANAGE,
            Permission.SERVICE_ACCOUNT_READ,
            Permission.API_KEY_CREATE,
            Permission.API_KEY_REVOKE,
            Permission.API_KEY_LIST,
            # Phase 2A — Knowledge
            Permission.KNOWLEDGE_READ,
            Permission.KNOWLEDGE_SOURCE_CREATE,
            Permission.KNOWLEDGE_SOURCE_UPDATE,
            Permission.KNOWLEDGE_DOCUMENT_UPLOAD,
            Permission.KNOWLEDGE_DOCUMENT_ARCHIVE,
            Permission.KNOWLEDGE_INGESTION_RETRY,
        }
    ),
    OrgRole.WORKFLOW_BUILDER: frozenset(
        {
            Permission.ORG_READ,
            Permission.ORG_MEMBER_LIST,
            Permission.WORKSPACE_READ,
            Permission.WORKFLOW_CREATE,
            Permission.WORKFLOW_RUN,
            Permission.WORKFLOW_READ,
            Permission.TEAM_READ,
            # Phase 2A — Knowledge (read + upload)
            Permission.KNOWLEDGE_READ,
            Permission.KNOWLEDGE_DOCUMENT_UPLOAD,
        }
    ),
    OrgRole.ANALYST: frozenset(
        {
            Permission.ORG_READ,
            Permission.ORG_MEMBER_LIST,
            Permission.WORKSPACE_READ,
            Permission.ANALYTICS_QUERY,
            Permission.WORKFLOW_READ,
            Permission.TEAM_READ,
            # Phase 2A — Knowledge (read only)
            Permission.KNOWLEDGE_READ,
        }
    ),
    OrgRole.OPERATOR: frozenset(
        {
            Permission.ORG_READ,
            Permission.ORG_MEMBER_LIST,
            Permission.WORKSPACE_READ,
            Permission.WORKFLOW_RUN,
            Permission.WORKFLOW_READ,
            Permission.WORKFLOW_APPROVE,
            Permission.TEAM_READ,
            # Phase 2A — Knowledge (read only)
            Permission.KNOWLEDGE_READ,
        }
    ),
    OrgRole.VIEWER: frozenset(
        {
            Permission.ORG_READ,
            Permission.ORG_MEMBER_LIST,
            Permission.WORKSPACE_READ,
            Permission.WORKFLOW_READ,
            Permission.TEAM_READ,
            # Phase 2A — Knowledge (read only)
            Permission.KNOWLEDGE_READ,
        }
    ),
    OrgRole.AUDITOR: frozenset(
        {
            Permission.ORG_READ,
            Permission.AUDIT_READ,
        }
    ),
}


def has_org_permission(role: OrgRole | None, permission: Permission) -> bool:
    """
    Return True if the given org role grants the given permission.

    A None role (member without a named role) has no permissions.
    """
    if role is None:
        return False
    return permission in _ORG_ROLE_PERMISSIONS.get(role, frozenset())


def get_org_permissions(role: OrgRole | None) -> frozenset[Permission]:
    """Return all permissions for an org role.  Returns empty set for None."""
    if role is None:
        return frozenset()
    return _ORG_ROLE_PERMISSIONS.get(role, frozenset())


def is_valid_org_role(role: str) -> bool:
    """Return True if the string is a valid OrgRole value."""
    return role in {r.value for r in OrgRole}


def is_valid_workspace_role(role: str) -> bool:
    """Return True if the string is a valid WorkspaceRole value."""
    return role in {r.value for r in WorkspaceRole}
