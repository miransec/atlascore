#!/usr/bin/env bash
# AtlasCore Phase 1A quality gate runner.
#
# Runs all automated checks in order.  Exits non-zero on the first failure.
# Must be run from the repository root: ./scripts/quality-gate.sh
#
# Prerequisites:
#   - docker compose is installed and the test stack is up
#     (docker compose -f docker-compose.test.yml up -d)
#   - The gate runner assumes the services it needs (postgres, backend) are
#     reachable.  Python checks run inside the backend container; npm checks
#     run inside the frontend container.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PASS=0
FAIL=0

_step() {
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  ▶  $*"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

_ok()   { echo "  ✓  $*"; PASS=$((PASS+1)); }
_fail() { echo "  ✗  $*"; FAIL=$((FAIL+1)); }

# ---------------------------------------------------------------------------
# 1. Docker Compose config validation
# ---------------------------------------------------------------------------
_step "docker compose config (production)"
if docker compose config --quiet 2>&1; then
  _ok "docker-compose.yml is valid"
else
  _fail "docker-compose.yml has errors"
fi

_step "docker compose config (test)"
if docker compose -f docker-compose.test.yml config --quiet 2>&1; then
  _ok "docker-compose.test.yml is valid"
else
  _fail "docker-compose.test.yml has errors"
fi

# ---------------------------------------------------------------------------
# 2. Backend — ruff (lint + format check)
# ---------------------------------------------------------------------------
_step "ruff check (backend)"
if docker compose exec -T backend ruff check app tests scripts; then
  _ok "ruff: no lint errors"
else
  _fail "ruff: lint errors found"
fi

_step "ruff format --check (backend)"
if docker compose exec -T backend ruff format --check app tests scripts; then
  _ok "ruff format: code is correctly formatted"
else
  _fail "ruff format: formatting issues found (run: ruff format app tests scripts)"
fi

# ---------------------------------------------------------------------------
# 3. Backend — mypy --strict
# ---------------------------------------------------------------------------
_step "mypy --strict (backend)"
if docker compose exec -T backend mypy --strict app; then
  _ok "mypy: no type errors"
else
  _fail "mypy: type errors found"
fi

# ---------------------------------------------------------------------------
# 4. Backend — pytest (unit + integration)
# ---------------------------------------------------------------------------
_step "pytest (backend)"
if docker compose -f docker-compose.test.yml exec -T backend \
      pytest tests/ -v --tb=short --cov=app --cov-report=term-missing; then
  _ok "pytest: all tests passed"
else
  _fail "pytest: test failures"
fi

# ---------------------------------------------------------------------------
# 5. Backend — pip-audit
# ---------------------------------------------------------------------------
_step "pip-audit (backend)"
if docker compose exec -T backend pip-audit --strict; then
  _ok "pip-audit: no known vulnerabilities"
else
  _fail "pip-audit: vulnerabilities found"
fi

# ---------------------------------------------------------------------------
# 6. Frontend — TypeScript / ESLint
# ---------------------------------------------------------------------------
_step "next lint (frontend)"
if docker compose exec -T frontend npm run lint; then
  _ok "next lint: no ESLint errors"
else
  _fail "next lint: ESLint errors found"
fi

# ---------------------------------------------------------------------------
# 7. Frontend — build
# ---------------------------------------------------------------------------
_step "next build (frontend)"
if docker compose exec -T frontend npm run build; then
  _ok "next build: build succeeded"
else
  _fail "next build: build failed"
fi

# ---------------------------------------------------------------------------
# 8. Migration cycle (up → down → up)
# ---------------------------------------------------------------------------
_step "Alembic migration cycle"
if docker compose -f docker-compose.test.yml exec -T backend sh -c \
      "alembic upgrade head && alembic downgrade base && alembic upgrade head"; then
  _ok "Alembic: migration cycle passed"
else
  _fail "Alembic: migration cycle failed"
fi

# ---------------------------------------------------------------------------
# 9. Health + readiness endpoints
# ---------------------------------------------------------------------------
_step "Backend health endpoint"
if docker compose exec -T backend sh -c \
      'curl -sf http://localhost:8100/health | grep -q "ok"'; then
  _ok "GET /health → ok"
else
  _fail "GET /health failed or returned wrong body"
fi

_step "Backend readiness endpoint"
if docker compose exec -T backend sh -c \
      'curl -sf http://localhost:8100/readiness | grep -q "ok"'; then
  _ok "GET /readiness → ok"
else
  _fail "GET /readiness failed or returned wrong body"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Quality Gate Summary"
echo "  Passed: $PASS    Failed: $FAIL"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ $FAIL -gt 0 ]]; then
  echo "  GATE FAILED — $FAIL check(s) did not pass."
  exit 1
fi

echo "  GATE PASSED — all checks green."
exit 0
