# Phase A — Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scaffold the `web-monitor` Python project so that lint, typecheck, tests, and `docker compose up` all go green serving a `/healthz` endpoint.

**Architecture:** A single-process Python app (FastAPI today, engine added later) laid out as `app/` with a config module. The Docker image is multi-stage and builds on both amd64 and arm64. All future phases branch off this green skeleton.

**Tech Stack:** Python 3.12, `uv`, FastAPI + Uvicorn, pydantic v2 + pydantic-settings, ruff, mypy `--strict`, pytest. Docker `python:3.12-slim` + `ghcr.io/astral-sh/uv`.

**Spec:** [`PLAN.md`](../../../PLAN.md) — Phase A (Foundations), §4 D2/D5/D6/D8, §12 (Docker), §13 (stack), §15 (milestones/gate).

## Global Constraints

Copied verbatim from the spec; every task's requirements include these.

- Python 3.12 only (`requires-python = ">=3.12"`, `.python-version` = `3.12`, image `python:3.12-slim`).
- Environment/deps managed by `uv` only — never `pip` or global Python.
- Project is a package: wheel `packages = ["app"]` (hatchling); `uv.lock` committed.
- Lint with `ruff`, types with `mypy --strict` over `app/` only (tests excluded), tests with `pytest`.
- Full gate (the Phase A acceptance): `uv sync` + ruff clean + mypy clean + pytest green + `docker compose up` serves `GET /healthz` → `{"status": "ok"}`.
- **Locked decisions (PLAN §16):** YAML config file is monitor source of truth; custom asyncio scheduler; repo workflow `main` + `develop` + `feature/*`.
- Repo workflow: implement on `feature/phase-a-foundation` branched from `develop`; commit per task; merge to `develop` at the end; merge `develop`→`main` + tag `phase-a` only after the user confirms acceptance.
- No secrets, no `.env` in git (already ignored). No comments in code unless they explain *why*.
- Do NOT add anything beyond this milestone's needs (no scheduler, no probing, no CLI, no SQLite yet).

---

### Task 1: uv project scaffold + tool config

**Files:**
- Create: `.python-version`
- Create: `pyproject.toml`
- Create: `app/__init__.py`
- Create: `app/core/__init__.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: an installable `web-monitor` package with `app` importable, tooling configured (ruff/mypy/pytest), so Tasks 2–5 can `uv run`.

- [ ] **Step 1: Create `.python-version`**

Content (exactly):
```
3.12
```

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[project]
name = "web-monitor"
version = "0.1.0"
description = "Self-hosted website and service monitoring (see PLAN.md)"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn>=0.32",
  "pydantic-settings>=2.6",
]

[dependency-groups]
dev = [
  "httpx>=0.27",
  "mypy>=1.13",
  "pytest>=8.3",
  "pytest-asyncio>=0.24",
  "ruff>=0.8",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "ASYNC"]

[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.12"
strict = true
files = ["app"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 3: Create empty package markers**

`app/__init__.py` and `app/core/__init__.py`: both empty files (no content, no comments).

- [ ] **Step 4: Sync dependencies and verify import**

Run:
```bash
uv sync
uv run python -c "import app; import app.core; print('ok')"
```
Expected: `ok`, no errors. (`uv sync` also generates `uv.lock`.)

- [ ] **Step 5: Commit**

```bash
git add .python-version pyproject.toml uv.lock app/__init__.py app/core/__init__.py
git commit -m "chore: scaffold uv project with lint/type/test config"
```

---

### Task 2: `/healthz` test first (red)

**Files:**
- Create: `tests/test_healthz.py`

**Interfaces:**
- Consumes: Task 1 package scaffold.
- Produces: a failing test that pins the API of Task 4 (`app.main.create_app` returning a FastAPI app whose `GET /healthz` yields `{"status": "ok"}`).

- [ ] **Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_healthz_returns_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_healthz.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_healthz.py
git commit -m "test: add failing healthz endpoint test"
```

---

### Task 3: Settings model test first (red)

**Files:**
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: Task 1 package scaffold.
- Produces: a failing test that pins the API of Task 5 (`app.core.config.Settings` with defaults `app_name="web-monitor"`, `log_level="INFO"`, `timezone="UTC"`, env prefix `WM_`, `env_file=".env"`, `extra="ignore"`, and `log_level` restricted to a `Literal`).

- [ ] **Step 1: Write the failing test**

```python
from app.core.config import Settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.app_name == "web-monitor"
    assert settings.log_level == "INFO"
    assert settings.timezone == "UTC"


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WM_TIMEZONE", "Europe/Berlin")
    monkeypatch.setenv("WM_LOG_LEVEL", "DEBUG")
    settings = Settings(_env_file=None)
    assert settings.timezone == "Europe/Berlin"
    assert settings.log_level == "DEBUG"
```

Add the missing import at the top:

```python
import pytest
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.config'`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_config.py
git commit -m "test: add failing settings model test"
```

---

### Task 4: App factory + `/healthz` (green)

**Files:**
- Create: `app/main.py`

**Interfaces:**
- Consumes: `app.core.config.Settings` (Task 5 — used here; implement Task 5 immediately after or stub note: `create_app(settings: Settings | None = None)` resolves defaults via `Settings()`).
- Produces: `create_app(settings: Settings | None = None) -> FastAPI` and a module-level `app = create_app()` for `uvicorn app.main:app`. Tasks 5 and the Docker task rely on `app.main:app`.

- [ ] **Step 1: Implement minimal code to make Task 2 pass**

```python
from fastapi import FastAPI

from app.core.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title=(settings or Settings()).app_name)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

Note: `create_app` references `app.core.config.Settings`, which does not exist until Task 5 — so Task 2's test will still fail with `ModuleNotFoundError` until Task 5 lands. Do Task 5 before re-running the suite.

- [ ] **Step 2: Commit**

```bash
git add app/main.py
git commit -m "feat: add app factory with healthz endpoint"
```

---

### Task 5: Settings model (green)

**Files:**
- Create: `app/core/config.py`

**Interfaces:**
- Consumes: pydantic-settings.
- Produces: `class Settings(BaseSettings)` (fields `app_name: str`, `log_level: Literal[...]`, `timezone: str`; `model_config = SettingsConfigDict(env_file=".env", env_prefix="WM_", extra="ignore")`). Later phases import `Settings` and build on `get_settings()`.

- [ ] **Step 1: Implement minimal code to make Task 3 pass**

```python
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="WM_", extra="ignore")

    app_name: str = "web-monitor"
    log_level: LogLevel = "INFO"
    timezone: str = "UTC"


def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 2: Run both test modules to verify they pass**

Run: `uv run pytest tests/ -q`
Expected: PASS (2 tests).

- [ ] **Step 3: Commit**

```bash
git add app/core/config.py
git commit -m "feat: add pydantic-settings settings model"
```

---

### Task 6: Quality gate (`make gate`)

**Files:**
- Create: `Makefile`

**Interfaces:**
- Consumes: everything so far; ruff/mypy/pytest must pass on the code created.
- Produces: the repeatable gate `make gate` (lint → typecheck → test) that every later phase runs, and the acceptance commands for the milestone.

- [ ] **Step 1: Create `Makefile`**

```makefile
PY := uv run

.PHONY: lint typecheck test gate

lint:
	$(PY) ruff check .
	$(PY) ruff format --check .

typecheck:
	$(PY) mypy

test:
	$(PY) pytest

gate: lint typecheck test
```

- [ ] **Step 2: Run the gate and fix until green**

Run: `make gate`
Expected: all three stages pass. Fix any lint/type errors in `app/` (tests are excluded from mypy by `files = ["app"]`; ruff checks everything, including tests).

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "chore: add make quality gate"
```

---

### Task 7: Dockerfile + `.dockerignore` + compose

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `docker-compose.yml`

**Interfaces:**
- Consumes: Task 1 `pyproject.toml` + `uv.lock`, Tasks 4–5 `app/` package.
- Produces: a `web-monitor:latest` image whose entrypoint `uvicorn app.main:app` listens on `8080` as a non-root user; `docker compose up` maps `8080:8080`.

- [ ] **Step 1: Create `.dockerignore`**

```
.git
.env
.venv
__pycache__/
*.py[cod]
.ruff_cache/
.mypy_cache/
.pytest_cache/
docs/
tests/
data/
config/
```

- [ ] **Step 2: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY app ./app

RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime

ENV PATH="/app/.venv/bin:$PATH"

RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/app /app/app

USER app
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 3: Create `docker-compose.yml`**

```yaml
services:
  monitor:
    build:
      context: .
    image: web-monitor:latest
    ports:
      - "8080:8080"
    restart: unless-stopped
```

Note: `env_file` and the `./config` / `./data` volume mounts arrive with the engine (Phase C/D) — not now.

- [ ] **Step 4: Build and verify the container serves `/healthz`**

Run:
```bash
docker compose up -d --build
```
Then poll until ready (up to ~60 s):
```bash
curl -fsS http://localhost:8080/healthz
```
Expected: `{"status":"ok"}`. If the editable-install import fails inside the container (venv copied across stages), fix the Dockerfile — the robust fix is to copy the whole builder `/app` (pyproject + app + .venv) into runtime at the same `/app` path instead of only `.venv` and `app`.

- [ ] **Step 5: Tear down and commit**

Run:
```bash
docker compose down
```
```bash
git add Dockerfile .dockerignore docker-compose.yml
git commit -m "feat: add multi-stage dockerfile and compose"
```

---

### Task 8: Phase A acceptance

**Files:**
- Modify: `PLAN.md` (mark Phase A complete in §15).

**Interfaces:**
- Consumes: the finished skeleton.
- Produces: a green milestone, merged to `develop`, then to `main` + tag `phase-a` on user confirmation.

- [ ] **Step 1: Run the full gate fresh**

Run: `uv sync --frozen && make gate`
Expected: ruff, mypy, pytest all green.

- [ ] **Step 2: Mark Phase A done in `PLAN.md`**

At the top of the "**Phase A — Foundations**" paragraph in §15, prefix with `✅ Done (` and reference the plan:

> ✅ Done (see `docs/superpowers/plans/2026-09-07-phase-a-foundation.md`). **Phase A — Foundations (decision lock + skeleton)**

- [ ] **Step 3: Commit**

```bash
git add PLAN.md
git commit -m "docs: mark phase a foundations complete"
```

- [ ] **Step 4: Merge to `develop`**

```bash
git checkout develop
git merge --no-ff feature/phase-a-foundation -m "merge: phase a foundations into develop"
git push origin develop
```

- [ ] **Step 5: Ask the user to confirm acceptance (stop here)**

Present the gate results and the live `/healthz` check. **Do not touch `main` or tag without explicit confirmation.**

- [ ] **Step 6 (only after user confirms): merge to `main` and tag**

```bash
git checkout main
git merge --no-ff develop -m "merge: phase a milestone into main"
git tag -a phase-a -m "Phase A: foundations"
git push origin main phase-a
```
