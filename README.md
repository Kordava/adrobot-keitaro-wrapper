# Adrobot

Adrobot is intended to create Keitaro campaigns and edit their offer bindings.
The current implementation supports an archive-synchronized editor Fetch
(TASK-03 Stage B2), a separate read-only diagnostic Fetch (TASK-01/B0),
and an explicitly gated Create workflow (TASK-02). Remote editor Push is
not implemented. Editor Fetch makes **no** Keitaro write requests.
The [requirements](docs/requirements/README.md) describe the full product scope,
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

The command explicitly creates/migrates `known_offer_bindings`; the runtime
**never** creates the file or tables automatically. Without a migrated SQLite
file, `/editor` returns `503`, while `/health` and `/remote-snapshot` still work.
The archive has a composite key `(campaign_id, stream_id, offer_id)`. Run the
upgrade with **the same `ADROBOT_DATABASE_URL`** as the application. For file
names containing literal `%`, `#` or `?`, percent-encode the URL path exactly
once (e.g. `%25`, `%23`, `%3F`); a literal `%25` filename sequence needs `%2525`.
Do not use unescaped query strings or in-memory DB URLs for persistent runtime.

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
The editor Fetch synchronizes the local offer archive atomically, then shows
names, binding IDs, Adrobot Active/Removed, Keitaro active/disabled, last known
raw `share` and preserved Pin. `Removed / 0%` does not erase historical values.
Neither actual shares nor historical raw values are normalized. The full base
and pinned state for **each offer stream** can be passed to the existing stateless
Preview API. A complete, valid `schema=landings` stream with `offers=[]` has an
empty active composition. Previously seen bindings appear as `Removed / 0%`
with their history and Pin. A first-seen empty stream has no archive rows.
Redirect streams appear separately as non-offer streams.

A duplicate global Offer in one stream returns `409 editor_duplicate_offer_id`.
A stream with archived bindings that disappears or changes type also returns
`409`. Malformed or incomplete reads leave the campaign archive and previous
display intact. Removing the last active offer from a nonempty Preview base is
still forbidden. Interactive draft actions and remote Push are future work.
Create remains gated by its own integration prerequisites.

### Live Fetch smoke (authorized test instance only)

Exercise the dedicated read-only diagnostic GET in-process without opening a public port:

```bash
uv run --locked python -m adrobot.keitaro.live_fetch 215477
```

Output is restricted to status, campaign ID, stream/offer-stream/binding counts.
The command returns nonzero for an unauthorized, unavailable, malformed or
failed read. `215477` is a sample campaign ID from earlier authorized read-only
discovery; choose a current campaign you have permission to read.

Campaign IDs and returned numeric fields must fit JavaScript's exact integer
range (IDs: 1–9007199254740991). Unsafe numbers are rejected rather than rounded.
Fetch accepts only HTTP 200 with a complete JSON array; partial-response markers,
invalid schemas and inconsistent IDs fail the whole read. Upstream 422 has its own
`keitaro_upstream_validation_error` category, distinct from local input validation.
Fetch responses use `Cache-Control: no-store`. Structured operation logs correlate
start/completion/failure with request ID, known IDs and safe error categories.
Both diagnostic commands report invalid backend configuration without exposing
input values or a traceback.

For schema-only debugging (field names/types and counts, never values):

```bash
uv run --locked python -m adrobot.keitaro.inspect 215477
```

Upstream: `GET /admin_api/v1/campaigns/{id}/streams`. The diagnostic route is
`GET /api/campaigns/{id}/remote-snapshot`, with `mapping_status=remote_only`
and `read_only=true`. It never accesses the local editor archive. The editor uses `GET /api/campaigns/{id}/editor` with
`mapping_status=archive_synced` and `local_archive_synced=true`. Its per-stream
`editor` field is exactly the nine-field Stage A `PreviewInput`, alongside
`known_offers`. The diagnostic route/CLI never synchronize or write SQLite.

### Create Campaign (TASK-02 — guarded until live verification)

The home page contains a Create form (`Name`, comma-separated ISO GEO, existing
`Offer ID`) and read-only autocomplete from global offers. The backend never
creates an Offer, and only a verified campaign and two-stream readback may return
`SUCCESS` and `/editor?campaign_id=<created_id>`. The editor link fills the
campaign ID and performs one read-only Fetch. A lost browser/backend response
is **unconfirmed**, never automatically resubmitted; check Keitaro before
issuing another explicit Create. The client, service and transport do not retry
mutating operations; this does *not* guarantee exactly-once semantics for third-
party clients, proxies or manual resubmissions.

Create is deliberately **disabled** unless the following non-secret local
settings are provided *after* GET-only discovery and an explicit live-write
approval (do not commit `.env`):

- `ADROBOT_CREATE_DOMAIN_ID`, `ADROBOT_CREATE_GROUP_ID`,
  `ADROBOT_CREATE_TRAFFIC_SOURCE_ID`: positive IDs obtained from the actual
  Keitaro instance. The backend validates each existing active record by GET.
- `ADROBOT_CREATE_OFFER_ACTION_TYPE`: action key discovered from the existing
  offer-stream instance/API documentation. It is **not** inferred from a guess.
- `ADROBOT_CREATE_MAPPING_VERIFIED=true`: set only when the instance's create
  payload and GEO/action semantics have been confirmed through approved
  GET-only discovery. Default `false`.
- `ADROBOT_CREATE_WRITES_ENABLED=true`: separate explicit authorization gate
  for live writes. Default `false`. **Do not enable without user consent**.

The server checks `/domains`, `/campaign_groups`, `/traffic_sources`,
`/stream_filters`, `/streams_actions` and the selected `/offers/{id}` before
any POST. Campaign POST explicitly includes `type=position`, a generated alias,
domain/group/source IDs, followed by a `regular` country-filtered HTTP redirect
and a `default` single-offer fallback. The documented one-offer `share=100`
requires instance confirmation during live Create acceptance. The application
re-reads the campaign (including rotation) before adding flows and then re-reads
both flows, comparing IDs, order, types, GEO and private action/offer details.
If the concrete instance differs from this public-documentation mapping,
**stop before live writes** and return safe GET evidence for a new ChatGPT-authored
patch; do not modify the local adapter without review.

`PARTIAL` means a known incorrect/unfinished campaign state; `UNKNOWN` means a
potential write or readback could not be verified. Neither state triggers
rollback, DELETE or another POST. A browser may also receive **valid gateway JSON**
after a successful backend Create; only correlated, explicitly recognized
pre-write application errors are shown as safe rejections. Other errors produce
an unconfirmed outcome, disable resubmission and require manual Keitaro inspection.
For reported `PARTIAL`/`UNKNOWN`, `creation.stage` is the **attempted** step,
whereas `creation.last_confirmed_stage` describes the last positive response or
readback (`none`, `campaign_post_accepted`, `campaign_readback_verified`,
`geo_post_accepted`, `fallback_post_accepted`). A POST acknowledgement does not
prove final campaign correctness; only the complete readback produces `SUCCESS`.
Only safe IDs, these stages, public error codes and request correlation reach
the browser; never raw upstream JSON.
New persistence, deduplication and API write-transaction claims are out of scope.
`GET /api/reference-data` returns only validation availability and reference
labels; `GET /api/offers?query=...` returns only existing offer IDs/names.
The archive-backed editor works without any Create settings, but requires the
explicit SQLite migration. The diagnostic `/remote-snapshot` remains independent.

**Integration boundary:** the public reference documents the general endpoints
and fields, but exact POST serialization and GEO action behavior on `tlgk.host`
have not been proven live. The authorized test campaign `215477` remains
GET-only. Any live Create must target a **new dedicated test campaign** with
its own explicit permission, and be followed by a GET readback.

## Offline checks and CI

After the locked install:

```bash
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src/adrobot
uv run --locked pre-commit run --all-files
node --check src/adrobot/web/static/editor.js
node --check src/adrobot/web/static/create.js
```

Node 20 is used for the JavaScript syntax check. For Python 3.13, pass
`--python-version 3.13` to mypy. To enable the versioned hooks locally, run
`uv run --locked pre-commit install`.

CI runs these gates on Python 3.12 and 3.13, plus two Alembic upgrades against
a separate temporary SQLite database. It needs no Keitaro secrets or live API.
The offline tests use fake HTTP/ASGI clients for HTTP errors, malformed nested
responses, ordered safe projections, Create sequences, failure boundaries and
startup/static assets. They do not prove live integration. Opt-in Chromium
regressions for Create→editor, post-Create errors and empty-stream Fetch live in
`tests/browser/` (requires external Playwright/Chromium; not a new locked dependency). Actual GitHub Actions on the published state remains
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
  `campaigns/create.py` owns one-shot Create sequencing and semantic readback;
  the router serves HTML and deliberately narrow API responses.
- Missing or malformed nested fields produce **502**, never a synthetic empty
  list. A failed Fetch retains the previous successful UI table.
- Editor Fetch stores known offers in a migrated SQLite archive and returns full
  independent Preview bases, names, status and persisted Pin per stream. The
  arithmetic-only `SNAPSHOT`/`RECALCULATED` Preview exists; interactive draft
  operations, freshness in the browser and Keitaro Push remain future work.
- Live unequal-share mapping (OPEN-02) remains open. The OPEN-04 probe
  observed a persisted empty `landings` in both list and detail GETs and a new
  `binding_id` after Remove/Readd. The editor rejects duplicate current
  occurrences of the same Offer within a stream; B0 shows their binding rows.
- Create reports `PARTIAL` and `UNKNOWN` explicitly. TASK-04 Push will need
  `CONFLICT` and separate revision/CAS discipline; this implementation does not
  provide atomic CAS against Keitaro. GET/PUT races and temporary drafts are
  accepted limits of the future editor contract.
- Bind to `127.0.0.1`. Public-user authentication is not implemented.

## Project documentation

- [Requirements](docs/requirements/README.md) — the active specification, rules,
  acceptance criteria and task plan.
- [Keitaro API notes](docs/keitaro-api-notes.md) — observed API behavior and
  open integration questions.
- [Development instructions](AGENTS.md) — local development and independent review.
  Local task status and verification reports live in `.ai-work/` when installed.
