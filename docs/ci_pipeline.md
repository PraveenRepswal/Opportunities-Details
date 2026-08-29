# CI Pipeline — `.github/workflows/ci.yml`

## 1. Purpose & Overview

The project ships a single GitHub Actions workflow, **`ci.yml`** (`.github/workflows/ci.yml:1-49`), that enforces two quality gates on every change:

1. **Lint** — `ruff` (pinned to version `0.16.4` via the `astral-sh/ruff-action`) runs the project's lint config defined in `pyproject.toml` under `[tool.ruff]`.
2. **Test** — `pytest` runs the test suite under `tests/` on Python 3.12, with a small set of test-only dependencies installed from PyPI.

The workflow triggers on **every push to `main`**, **every `v*` tag**, and **every pull request**, providing a tight feedback loop on both feature branches and the protected `main` branch.

The pipeline is intentionally **fast and minimal**: it does not build Docker images, does not run integration tests against a live backend, and does not deploy. Its sole job is to catch lint regressions and unit-test failures before code lands.

## 2. Architecture

```
        ┌──────────────────────────────┐
        │  GitHub event source         │
        │  - push: main                │
        │  - push: v* tag              │
        │  - pull_request (any branch)  │
        └──────────────┬───────────────┘
                       │
                       ▼
        ┌──────────────────────────────┐
        │  Workflow: CI                │
        │  permissions: contents: read │
        │                              │
        │  ┌────────────┐ ┌──────────┐ │
        │  │  job: lint │ │ job: test│ │
        │  │ ubuntu-    │ │ ubuntu-  │ │
        │  │ latest     │ │ latest   │ │
        │  │            │ │          │ │
        │  │ checkout   │ │ checkout │ │
        │  │ ruff@v3    │ │ setup-   │ │
        │  │ 0.16.4     │ │ python@5 │ │
        │  │ ruff check │ │ pytest   │ │
        │  └────────────┘ └──────────┘ │
        └──────────────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────┐
        │  GitHub UI                   │
        │  - green/red status checks   │
        │  - required check on main    │
        └──────────────────────────────┘
```

The two jobs are **independent** — they run in parallel on separate runners, neither waits for the other. There is no `needs:` clause and no shared artifact.

## 3. Triggers (`.github/workflows/ci.yml:10-15`)

```yaml
on:
  push:
    branches: [main]
    tags: ["v*"]
  pull_request:
```

- **`push` to `main`** — every direct commit and merge to the protected branch runs the full pipeline.
- **`push` of `v*` tags** — e.g. `v0.5.0`, `v1.2.3-rc1`. The glob `v*` matches any tag starting with `v`. This is the standard release-trigger pattern.
- **`pull_request`** — any PR opened against any branch (the default `pull_request` event has no `branches` filter, so it fires for forks and internal PRs alike).

> **Note on concurrency:** the workflow does not declare a `concurrency:` block. Concurrent runs on the same branch are allowed; if a faster follow-up commit arrives, both will run to completion. This is fine for a small Python project but can be added later to cancel superseded runs.

## 4. Permissions (`.github/workflows/ci.yml:16-17`)

```yaml
permissions:
  contents: read
```

The workflow has the **minimum** permission: read repository contents. It cannot write issues, PR comments, packages, etc. This follows GitHub's principle-of-least-privilege and is the recommended default for a CI workflow that only needs to read source and run tools.

## 5. Jobs

The workflow defines **two jobs** that run on `ubuntu-latest` (`.github/workflows/ci.yml:22, 34`).

### 5.1 Job: `lint`  (`.github/workflows/ci.yml:20-30`)

| Field | Value | Line |
|---|---|---|
| `name` | `Lint (ruff)` | `:21` |
| `runs-on` | `ubuntu-latest` | `:22` |

**Steps:**

1. **`actions/checkout@v4`** (`:24`) — checks out the repo at the triggering SHA. No `fetch-depth` override, so the full history is cloned.
2. **`astral-sh/ruff-action@v3`** with `version: "0.16.4"` (`:26-28`) — installs the exact ruff version. Pinning the version makes lint output deterministic across runs (a ruff rule change between versions cannot suddenly turn a passing build red).
3. **`ruff check .`** (`:30`) — runs the lint over the entire repo using the config in `pyproject.toml` `[tool.ruff]` (`:113-127` of `pyproject.toml`).

**Effective ruff configuration** (from `pyproject.toml`):
- `line-length = 120`
- `target-version = "py312"`
- `extend-exclude = ["scratch_*"]`
- `select = ["E4", "E7", "E9", "F"]` — pycodestyle import/indentation/statement/syntax errors plus pyflakes (undefined names, unused imports, f-string issues).
- `ignore = ["E402"]` — module-level imports after optional-dependency try/except blocks (used in `rag.py` and `agent.py`).

**Failure mode:** if any selected rule fires, ruff exits non-zero and the job is marked failed. The PR cannot be merged if the workflow is a required check.

### 5.2 Job: `test`  (`.github/workflows/ci.yml:32-49`)

| Field | Value | Line |
|---|---|---|
| `name` | `Tests (pytest)` | `:33` |
| `runs-on` | `ubuntu-latest` | `:34` |

**Steps:**

1. **`actions/checkout@v4`** (`:36`) — same as the lint job.
2. **`actions/setup-python@v5`** with `python-version: "3.12"` (`:38-40`) — installs CPython 3.12 on the runner.
3. **Install test dependencies** (`:44-47`):
   ```bash
   python -m pip install --upgrade pip
   pip install pytest dateparser pydantic-settings fastapi httpx
   ```
   The inline comment at `:42-43` notes this set covers the modules under test:
   - `backend.metadata_extractor` → requires `dateparser`, `pydantic-settings`.
   - `backend.rate_limit` → requires `fastapi`, `httpx` (the FastAPI `TestClient` is built on `httpx`).
4. **`python -m pytest tests/ -v`** (`:49`) — runs the full test suite under `tests/` with verbose output.

**Failure mode:** any test that exits non-zero fails the job. A short traceback is shown in the GitHub UI.

## 6. End-to-end run lifecycle

1. **Trigger** — a push, tag, or PR event fires.
2. **Runner allocation** — GitHub provisions two `ubuntu-latest` runners (one per job) in parallel.
3. **Checkout** — both jobs clone the repo at the triggering SHA.
4. **Tooling install** — `lint` installs ruff `0.16.4` via the official action; `test` installs Python 3.12 + a minimal pip set.
5. **Execution** — `ruff check .` and `pytest tests/ -v` run. Their stdout/stderr is captured by the runner and surfaced in the GitHub UI.
6. **Status report** — each job reports `success` or `failure` independently. The overall commit/PR status is `success` only if **both** jobs succeed.
7. **Branch protection** — if `lint` and `test` are configured as required checks on `main`, a red ✗ blocks merge.

There is no teardown step; the runners are ephemeral and are destroyed by GitHub after the run.

## 7. Configuration / environment variables

The workflow itself does not consume project env vars (it does not start the backend or the UI). The only environment knob is the runner's Python version (`3.12`), matching `pyproject.toml:6` (`requires-python = ">=3.12,<3.14"`).

If you need to test against a different Python version or a Postgres-backed integration, the typical pattern would be to add a matrix entry, e.g.:

```yaml
strategy:
  matrix:
    python-version: ["3.12", "3.13"]
```

… and parameterize the `python-version` field of `setup-python`. This is not currently used.

## 8. Network / API calls

The CI workflow makes **no external API calls**. The only network egress is:

- GitHub → runner via the `actions/checkout@v4` and `astral-sh/ruff-action@v3` actions (these are JavaScript actions that download their own dependencies at runtime).
- `pip install` from the `test` job hits the public PyPI (`https://pypi.org`) for `pytest`, `dateparser`, `pydantic-settings`, `fastapi`, `httpx`.

The backend and frontend are not started, no Docker images are built, and no LLM calls are made.

## 9. Error handling / fallbacks

- **Action version drift:** pinning `astral-sh/ruff-action@v3` (a major version) and `version: "0.16.4"` (the action's own input) means the action's own behavior is stable, and the ruff binary it installs is reproducible.
- **Outdated Python:** if the runner does not have Python 3.12 preinstalled, `actions/setup-python@v5` will download it. If the download fails (e.g. transient network blip), the job fails. Re-running the job is the standard fix.
- **Test import errors:** the `pip install` block at `:44-47` is intentionally narrow (only the dependencies of the modules under test). If a future test imports, say, `sentence-transformers`, the import will fail. The remedy is to expand the install list.
- **Ruff config drift:** the `select` and `ignore` rules live in `pyproject.toml`, not in the workflow. Updating them requires editing `pyproject.toml`, not the workflow.
- **No retries:** the workflow has no `continue-on-error` and no automatic retry. A flaky test must be fixed in code, not in CI.

## 10. Notable design decisions

1. **Two narrow jobs, no shared setup.** Each job installs only what it needs. The lint job is fast (sub-minute); the test job is bounded by what the user writes in `tests/`. There is no `setup` job that both depend on, so they start in parallel.

2. **Ruff instead of flake8 + isort + black.** Ruff is a single-binary linter that implements pycodestyle, pyflakes, and (with config) isort/black rules. The `select = ["E4", "E7", "E9", "F"]` choice (`:119-124` of `pyproject.toml`) deliberately avoids style-nanny rules (E1, E2, W) in favor of correctness checks (undefined names, unused imports, syntax errors, f-string issues).

3. **Pinned ruff version.** `version: "0.16.4"` (`:28`) makes CI reproducible. Without a pin, a new ruff release with a new rule could turn a green build red overnight.

4. **Python 3.12 only.** The project requires `>=3.12,<3.14` (`pyproject.toml:6`). 3.12 is the lowest supported, so testing on it is the most restrictive case. A multi-version matrix is a future enhancement.

5. **Minimal `pip install` set.** The `test` job does not run `pip install -r requirements.txt` (which would be hundreds of MB and tens of minutes for torch). It only installs the packages the **existing tests** import. This is a deliberate trade-off: fast CI in exchange for not testing the heavy ML stack. Any test that imports `torch`, `sentence-transformers`, or `transformers` will currently fail at import time.

6. **`permissions: contents: read`.** Sets the principle-of-least-privilege. A future addition (e.g. comment-on-PR with coverage report) would require expanding the block.

7. **No `needs:` between jobs.** They are independent. This is the simplest possible structure; adding `needs: lint` for `test` would only be useful if the test job were a superset of the lint work (it isn't).

8. **`pull_request` with no `branches` filter.** This catches PRs from forks too, not just internal branches — important for a public open-source project.

9. **No concurrency block.** Superseded runs are not cancelled. This is acceptable for a small project but worth revisiting once the workflow gets slower (e.g. adding a Docker build job).

10. **No caching of pip or ruff.** A small `actions/setup-python@v5` cache or a `actions/cache` step on `~/.cache/pip` would shave seconds off each run. Not currently configured; could be added without risk.
