# Adrobot

Adrobot is intended to create Keitaro campaigns and edit their offer bindings.
The current implementation supports read-only Fetch (TASK-01). The
[requirements](docs/requirements/README.md) describe the full product scope,
task dependencies and acceptance criteria.

## Start locally

Requires Python 3.12 or 3.13, **uv 0.13.9**, and network access for dependency
installation. `uv.lock` fixes the application and development package versions;
local commands and CI use the same locked installation. It does not bundle
packages for offline download or fix every part of the host toolchain.

From a clean clone, without `.env`, a Keitaro key or the historical ZIP:

```bash
uv sync --locked --all-groups --python 3.12
uv run --locked uvicorn adrobot.main:app --host 127.0.0.1 --port 8000
```

Use `--python 3.13` instead to select the other supported interpreter. Open
<http://127.0.0.1:8000/health> or <http://127.0.0.1:8000/editor>. Health,
HTML and static assets work without credentials or creating a database. Fetch
returns an unavailable response until the backend is configured.

The existing SQLite bootstrap is run separately:

```bash
uv run --locked alembic upgrade head
```

It currently creates only the Alembic bootstrap, not the editor archive schema.
`known_offer_bindings` and its constraints belong to TASK-03/OPEN-03.

### Configure live Fetch

Obtain an Admin API key from your own authorized test Keitaro administration.
Stop the app, copy `.env.example` to `.env`, and replace the placeholders locally:

- `ADROBOT_KEITARO_URL`: the authorized test instance's HTTPS origin.
- `ADROBOT_KEITARO_API_KEY`: the Admin API key, used only by the backend.
- `ADROBOT_DATABASE_URL`: SQLite URL (default `sqlite:///./adrobot.db`).
- `ADROBOT_LOG_LEVEL`: logging level (default `INFO`).

Restart with the same uvicorn command. `.env` is ignored by Git. Never put the
key in a URL, browser JavaScript, screenshots or logs. The backend uses the
`Api-Key` header, validates TLS certificates and does not follow redirects.

Enter a **numeric campaign ID** in the editor and click **Fetch streams from KT**.
The current page shows ordered streams and, for offer streams, binding ID,
global offer ID, binding state and the *raw* upstream `share` integer. It does
not normalize shares or claim they are traffic percentages for unequal mixes.
Non-offer streams are displayed without exposing their redirects or filters.
There is **no editor archive, Pin, Create, Preview or Push** at this stage.

### Live Fetch smoke (authorized test instance only)

Exercise the same FastAPI GET route in-process without opening a public port:

```bash
uv run --locked python -m adrobot.keitaro.live_fetch 215477
```

Output is restricted to status, campaign ID, stream/offer-stream/binding counts.
The command returns nonzero for an unauthorized, unavailable, malformed or
failed read. `215477` is a sample campaign ID from earlier authorized read-only
discovery; choose a current campaign you have permission to read.

For schema-only debugging (field names/types and counts, never values):

```bash
uv run --locked python -m adrobot.keitaro.inspect 215477
```

Upstream: `GET /admin_api/v1/campaigns/{id}/streams`. The internal route is
`GET /api/campaigns/{id}/editor`, with `mapping_status=remote_only`. Successful
Fetch is not a synchronized editor snapshot (TASK-03).

## Offline checks and CI

After the locked install:

```bash
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src/adrobot
uv run --locked pre-commit run --all-files
node --check src/adrobot/web/static/editor.js
```

Node 20 is used for the JavaScript syntax check. For Python 3.13, pass
`--python-version 3.13` to mypy. To enable the versioned hooks locally, run
`uv run --locked pre-commit install`.

CI runs these gates on Python 3.12 and 3.13, plus two Alembic upgrades against
a separate temporary SQLite database. It needs no Keitaro secrets or live API.
The offline tests use fake HTTP/ASGI clients for HTTP errors, malformed nested
responses, ordered safe projections and startup/static assets. They do not
prove live integration. Actual GitHub Actions on the published state remains
a separate acceptance gate; local success does not close AC-Q-05.

### Full requirements-pack validation

The strict structural validator needs the **original** historical ZIP at
`.ai-work/reference/Adrobot_requirements_review_v0.2.zip` (hash in
[`docs/requirements/00_manifest.yaml`](docs/requirements/00_manifest.yaml)):

```bash
uv run --locked python checks/validate_pack.py
```

This ZIP is ignored by Git and is not required to install or run the app, or
for CI. The command intentionally fails when the archive is absent. A valid
pack proves document provenance and references, not application acceptance.

## Design and limitations

- `keitaro/client.py` owns HTTP/timeouts and response errors;
  `keitaro/read_models.py` validates the complete array and projects safe DTOs.
  The router serves HTML and the temporary JSON Fetch response.
- Missing or malformed nested fields produce **502**, never a synthetic empty
  list. A failed Fetch retains the previous successful UI table.
- The planned editor archive stores binding history and Pin in SQLite. Draft
  modes `SNAPSHOT`/`RECALCULATED`, full Pin handling, Preview freshness and
  Push/readback are future work, not current capabilities.
- Live unequal shares and empty offer-stream classification still require
  instance evidence. API questions remain in `docs/keitaro-api-notes.md`.
- Future writes must report `UNKNOWN`/`PARTIAL`/`CONFLICT` honestly; revisions
  alone do not provide atomic CAS against Keitaro. GET/PUT races and temporary
  browser drafts are accepted limits of that future editor contract.
- Bind to `127.0.0.1`. Public-user authentication is not implemented.

## Project documentation

- [Requirements](docs/requirements/README.md) — the active specification, rules,
  acceptance criteria and task plan.
- [Keitaro API notes](docs/keitaro-api-notes.md) — observed API behavior and
  open integration questions.
- [Development instructions](AGENTS.md) — local development and independent review.
  Local task status and verification reports live in `.ai-work/` when installed.
