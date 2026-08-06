"""API v1 router — aggregates all endpoint routers."""

from fastapi import APIRouter

from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.invitations import router as invitations_router
from app.api.v1.endpoints.knowledge import router as knowledge_router
from app.api.v1.endpoints.organisations import router as org_router
from app.api.v1.endpoints.selector import router as selector_router
from app.api.v1.endpoints.service_accounts import router as service_accounts_router
from app.api.v1.endpoints.teams import router as teams_router
from app.api.v1.endpoints.workspaces import router as ws_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth_router)
api_router.include_router(org_router)
api_router.include_router(ws_router)
api_router.include_router(invitations_router)
api_router.include_router(teams_router)
api_router.include_router(service_accounts_router)
api_router.include_router(selector_router)
api_router.include_router(knowledge_router)
