# Implementation status — TASK-01 read-only Fetch v0.3

Baseline: `source/Adrobot_TZ_v0.3.1.md`, implementation pack v0.1.

## Current implementation

- Typed settings and lifespan-owned HTTP client with TLS verification,
  backend-only `Api-Key`, bounded timeouts, no redirects or mutation methods.
- Read `GET /admin_api/v1/campaigns/{id}/streams`, strict nested validation and
  safe public DTOs with distinct binding/offer IDs and raw upstream shares.
- Internal `GET /api/campaigns/{id}/editor` returning `remote_only` data;
  browser Fetch retains the previous successful view after a read error.
- In-process `live_fetch` smoke uses the same route and emits safe counters.
- Offline fake/ASGI tests cover invalid nested fields and IDs, redaction,
  HTTP errors, timeouts, zero-binding streams and startup/static assets.
- Locked application/dev dependencies, uv 0.13.9 commands, Ruff 0.14.10 matching
  the existing hooks, strict mypy with the existing Pydantic plugin.
- CI matrix for Python 3.12/3.13 with locked install, quality/tests, Node syntax,
  hooks and two temporary SQLite bootstrap upgrades. No Keitaro secrets needed.
- Alembic bootstrap only; editor archive schema remains TASK-03/OPEN-03.

## Verification

Local foundation verification on 2026-11-20 passed on Python **3.12.3** and
**3.13.18**, using separate clean source copies without `.env`, an existing
`.venv` or the historical ZIP:

- `uv sync --locked --all-groups` installed the same locked graph on both.
- **27 pytest tests passed on each interpreter**; Ruff lint/format, strict
  mypy (15 modules) and all versioned pre-commit hooks passed on each.
- Real uvicorn processes served health, both HTML pages and JS/CSS resources
  on loopback without credentials or creating the configured database.
- Two Alembic upgrades per interpreter passed on fresh temporary SQLite;
  integrity/schema checks passed and a control row survived the second run.
- Node 20 syntax and `git diff --check` passed. The strict full-pack validator
  passed separately with the original ZIP (70 rules, 37 AC, 5 tasks, 64 sections,
  17 DoD items). This proves document structure, not application acceptance.

No Fetch mapping/error semantics changed. The existing Starlette warning about
the deprecated httpx transport integration remains; it did not fail the suite.

Actual GitHub Actions on matching published content is still pending. The
agent does not create commits; publication needs user action/authorization.
Foundation is not VERIFIED until that external gate passes.

## Integration still unproven

- App's own live Fetch with the backend `Api-Key` against the authorized test
  instance. Earlier API Explorer discovery is not proof of this app's route.
- Live unequal-share and zero-offer offer-stream behavior (OPEN-02/04).
- Mutation schemas and binding identity/duplicates (OPEN-01/02/03/04/05/06).
- Request/stage/campaign/error diagnostics and runtime security checks belong
  to the separate `readonly_fetch` scope; foundation preserves Fetch semantics.

## Acceptance mapping and next step

Foundation covers installation/startup and tooling portions of TASK-01,
ENG-02/03/05/06/07/08/10/12/13, AC-Q-01/04/05. AC-Q-05 requires actual CI.
AC-Q-02 is not complete: bootstrap is not the future archive table/constraints.
No archive, Pin, math, Create or Push acceptance is marked passed.

Next: publish the matching foundation state and observe CI; independent
`readonly_fetch` work may proceed while waiting only for that external gate.
TASK-01 additionally requires actual FastAPI/browser Fetch, error preservation,
security/observability checks and recorded live API facts. Ordinary pytest/CI
remain independent of Keitaro. No read failure may update the archive.
