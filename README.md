# Adrobot — TASK-01 read-only Fetch (v0.3)

This is a **partial implementation**, not the complete Keitaro wrapper. The
normative requirements remain in `source/Adrobot_TZ_v0.3.1.md`; `AGENTS.md` and
`04_tasks.yaml` describe scope and acceptance.

## Start locally

Requires Python 3.12 or 3.13, `uv`, and network access to install dependencies.

```bash
uv sync --all-groups
cp .env.example .env
# Edit .env locally: set ADROBOT_KEITARO_URL and ADROBOT_KEITARO_API_KEY
uv run alembic upgrade head
uv run uvicorn adrobot.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/editor>, enter a **numeric campaign ID**, and
click **Fetch streams from KT**. Never put the Keitaro API key in the URL or
browser JavaScript. The app authenticates with the backend-only `Api-Key`
header; it does not follow redirects and validates TLS certificates.

The current page is **read-only**. It shows ordered streams and, for offer
streams, binding ID, global offer ID, binding state, and the *raw* `share`
integer provided by Keitaro. It does not normalize the shares or claim they
are traffic percentages for unequal mixes. Non-offer streams are displayed as
streams without exposing their redirects or filters. There is **no editor
archive, Pin, Create or Push** at this stage.

### Live Fetch smoke (requires network access to test Keitaro)

Run the same FastAPI GET route in-process; no public port is required:

```bash
uv run python -m adrobot.keitaro.live_fetch 215477
```

The output is deliberately restricted to `status`, campaign ID, count of
streams, offer-streams, and bindings. The command returns nonzero for an
unauthorized, unavailable, malformed or failed read. `215477` is a **sample
campaign ID observed during authorized read-only discovery**; choose a current
test campaign you have permission to read. It is not a stream ID.

For schema-only debugging (field names/types and counts, never values):

```bash
uv run python -m adrobot.keitaro.inspect 215477
```

The path used upstream is `GET /admin_api/v1/campaigns/{id}/streams`. Our own
read-only route is `GET /api/campaigns/{id}/editor` and currently advertises
`mapping_status=remote_only`. Successful Fetch must not be confused with a
full synchronized editor snapshot (TASK-03).

## Offline checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src/adrobot
uv run pre-commit run --all-files
uv run alembic upgrade head
python checks/validate_pack.py
```

The offline test suite uses `httpx.MockTransport`; **it never requests live
Keitaro**. It includes fake HTTP errors and malformed responses, an in-process
FastAPI smoke test, strict nested stream validation, and checks that unknown
upstream fields/credentials do not reach the public response.

**Execution evidence:** in the provided sandbox Python 3.13.5,
`PYTHONPATH=src python -m pytest -q` passed **27 tests** and `compileall`
passed. Actual live Fetch **has not** been executed through the local app:
the sandbox cannot connect to the test Keitaro host. Ruff, mypy, pre-commit,
CI and a dependency lock have **not** been verified here; offline dependency
installation and lock resolution cannot access uncached packages. Follow the
commands above on a networked machine and record their actual results. Do not
mark TASK-01 complete until the live FastAPI Fetch is observed.

## Design and limitations

- `src/adrobot/keitaro/client.py` owns HTTP/timeouts and response errors.
- `src/adrobot/keitaro/read_models.py` validates the complete remote array and
  produces a narrow public projection; extra remote attributes are discarded.
- `src/adrobot/api/routes.py` serves HTML and the temporary JSON Fetch route.
- `src/adrobot/keitaro/live_fetch.py` exercises that route with an ASGI client;
  it emits no raw upstream payload or authentication secrets.
- A missing or malformed nested field is **502**, never a synthetic empty list.
  A failed Fetch does not replace the previously displayed table in the UI.
- Alembic environment exists; `known_offer_bindings` migration belongs to
  TASK-03 after resolving OPEN-03 (binding identity and duplicates).
- `uv.lock` is not in this snapshot. Generate it and switch CI to `--locked`
  after successful resolution; do not claim a reproducible lockfile yet.
- This scaffold is meant for `127.0.0.1` only. There is no public-user
  authentication; a publicly exposed endpoint would reveal test campaigns.

The evidence and still-unverified API assumptions are in
`docs/keitaro-api-notes.md`; work status is in `docs/IMPLEMENTATION_STATUS.md`.
