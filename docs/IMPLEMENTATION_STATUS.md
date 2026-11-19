# Implementation status — TASK-01 read-only Fetch v0.3

Baseline: `source/Adrobot_TZ_v0.3.1.md`, implementation pack v0.1.

## Implemented and checked offline

- [x] Typed settings, HTTP lifespan/client with TLS verification, `Api-Key` header,
      bounded timeouts, no redirects, no mutation methods.
- [x] Read `GET /admin_api/v1/campaigns/{id}/streams` through `KeitaroClient`.
- [x] Strict nested read validation and safe public DTO: stream position/type,
      separate binding ID and offer ID, raw upstream `share`, `state`.
- [x] FastAPI `GET /api/campaigns/{id}/editor` returning `remote_only` response.
- [x] Browser `Fetch` renders stream and binding data, retaining last successful
      view on read failure. No archive, Pin or write buttons yet.
- [x] `python -m adrobot.keitaro.live_fetch CAMPAIGN_ID`: in-process HTTP
      smoke of **the same** FastAPI route; output limited to safe counters.
- [x] In-memory fake/ASGI tests including invalid nested fields, wrong IDs,
      redaction, timeout/401/404/429/5xx, schema types and zero-binding stream.
- [x] `PYTHONPATH=src python -m pytest -q`: **27 passed** (Python 3.13.5).
- [x] Python source compilation and JS syntax check succeeded.
- [x] Alembic bootstrap exists; no binding table until OPEN-03 is resolved.
- [x] Authorized in-app API Explorer returned live **read-only** Keitaro JSON
      (see API notes). No writes were executed.

## Not yet proven (do not mark TASK-01 VERIFIED)

- [ ] **App's own live Fetch with `Api-Key` header** against `tlgk.host`.
      Development sandbox external connection failed (`ConnectError`).
      Execute `uv run python -m adrobot.keitaro.live_fetch 215477` on a networked
      authorized workstation and record the sanitized result.
- [ ] Ruff lint, Ruff format, mypy, pre-commit and GitHub Actions CI actually green.
      Tools unavailable in the offline environment.
- [ ] `uv.lock` generated with a full dependency resolve, committed, and CI
      updated to `uv sync --locked`.
- [ ] Verify the live behavior of unequal `share` and zero-offer offer-stream
      before editor archive/draft; mutation schemas remain OPEN-01/02/03/04/05/06.

## Acceptance mapping

`TASK-01` (ENG/INT plus partial `AC-E-01`, `AC-Q-04/05/06/08`): offline
implementation evidence available. First **live** FastAPI Fetch is required
before marking task complete. No `AC` dependent on archive, Pin, calculations
or mutation is marked passed.

Next: networked live read smoke, finish quality gates/lock, then TASK-02
creator and independent TASK-03 domain math. No read failure may update archive.
