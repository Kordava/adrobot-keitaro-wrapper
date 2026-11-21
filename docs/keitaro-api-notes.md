# Keitaro Admin API mapping — work in progress

**Status as of 2026-11-21.** On `https://tlgk.host`, live Fetch of campaign
`215477` succeeded through the CLI, loopback backend and Chrome headless. A
separately authorized live Create smoke created campaign `216881` with one
GEO and one offer. The [Create results](#2026-11-20--live-create-smoke) record
the tested configuration and the limits of that run.

| Item | Evidence | Confidence |
|---|---|---|
| Admin API uses `Api-Key` header | Live application Fetch and Create, 2026-11-20 | Verified for the observed reads and Create |
| `GET /admin_api/v1/campaigns/{id}/streams` | In-app API Explorer GET, 2026-11-19 | Observed on live test instance |
| Stream response `offers[]` contains binding `id`, `offer_id`, `share` | In-app API Explorer GET, 2026-11-19 | Observed on live test instance; unequal-share semantics unverified |

**OPEN-01.** Live Fetch and Create with `GEO=AU`, two ordered streams and one
offer are verified on the instance below. Multi-GEO Create and other mutation
contracts remain open.

**OPEN-02.** Create persisted an integer `share=100` for one active binding.
Unequal-share conversion, rounding and Push remain unverified.

**OPEN-03/04:** binding `id` and `offer_id` are distinct; live Remove/Readd changes the binding ID, and a specific duplicate PUT collapsed to one row. These observations justify the guarded B1 logical archive key but not a universal uniqueness guarantee. An empty `landings/offers=[]` was observed; safe editor synchronization remains deferred to B2.

**OPEN-05.** The tested Create defaults are domain `1813`, campaign group `9799`
and traffic source `836`. All three were validated through GET and confirmed
on the created campaign.

**OPEN-06.** Safe update fields and CAS behavior remain unverified.

To repeat the read-only check, configure the backend locally and run
`python -m adrobot.keitaro.live_fetch 215477`, then migrate the local SQLite
archive and Fetch the same campaign in `/editor`. The UI displays stream
metadata, archived names, Adrobot status, binding IDs/state/raw share and
retained Pin, without redirect/filter details. Keep credentials and raw upstream responses out
of reports and logs.

## 2026-11-21 — OPEN-03 live readback (dedicated disabled campaign)

Evidence described by the user and checked locally against `summary.json`,
`evidence.jsonl` and SQLite authority by the local reviewer. This note records
those reported observations; no live writes were performed while implementing B1.

- Dedicated **disabled** test campaign `217517`, offer stream `1335575`, global
  Offer `18813`; mutations used only `PUT /admin_api/v1/streams/{id}`.
- Initial GET showed `offer_id=18813`, `binding_id=2092367`, raw `share=100`.
- A PUT with two entries for offer `18813` with raw `50/50` returned 200; the
  campaign stream list and direct stream GET both showed **one** binding,
  `binding_id=2092367`, raw `share=50`. This single probe is **not** proof of
  global uniqueness of `offer_id` within every Keitaro stream.
- Removing the only offer via `offers=[]` returned 200; both readbacks showed
  `schema=landings`, `offers=[]`. Full editor synchronization of such a stream
  is deliberately deferred to OPEN-04/Stage B2, not treated as a mass remove.
- Readding the same global offer returned one binding with **new**
  `binding_id=2092392`, raw `share=100`. Logical offer identity is therefore
  distinct from Keitaro binding identity across complete Remove/Readd.
- A non-offer-field fingerprint changed after the duplicate PUT; the precise
  field is unknown. Production Push must not assume that PUT only changes offers.
  The test campaign was left disabled with one restored offer at raw 100.

B1's supported archive identity is `(campaign_id, stream_id, offer_id)` with
`DraftId=offer_id` scoped to one stream, while the latest Keitaro binding ID
is stored in nullable `last_binding_id`. A fully read campaign with multiple
bindings of the same offer in the **same** stream is rejected by editor Fetch
as `409 editor_duplicate_offer_id` **before archive DML**; the diagnostic
`/remote-snapshot` still returns both raw binding rows, and CLI only prints
aggregate `bindings=N`. This is an explicit application policy, not an API
claim about universal uniqueness. The `share` read-mapping (OPEN-02) and the
complete empty stream lifecycle (OPEN-04) are still separate questions.

## 2026-11-20 — live Create smoke

At **16:47–16:48 Moscow (13:47–13:48 UTC)**, Adrobot at Git `f27e219` ran one
Create submission against `https://tlgk.host`. The instance's Status page
reported Keitaro version `11.5.0` and Build `11.12.0-2026-10-26-07:53:03`.
Swagger in the instance loaded the vendor's public
[OpenAPI YAML](https://admin-api.docs.keitaro.io/openapi.yaml).

| Check | Verified result |
|---|---|
| Preflight | Domain `1813` and source `836` were active. Campaign group `9799` was present in `GET /admin_api/v1/groups?type=campaigns`, whose entries have no `state` field. Offer `18813` was active. The capabilities included `country/accept` and `http`. |
| AC-C-01 | One `POST /admin_api/v1/campaigns` created active campaign `216881` with positional rotation and the verified defaults. The request used string `group_id`; readback returned integer `9799`. A paginated GET found one campaign matching the generated name and alias. |
| AC-C-02, first stream | Stream `1332703` was active, `regular` and `redirect` at position 1. Its `country/accept` filter contained `["AU"]`. Keitaro persisted the submitted `action_payload=https://google.com`. The public request schema omits this field. |
| AC-C-02, fallback | Stream `1332704` was active, `default` and `landings` at position 2, with `action_type=http`. Its only binding (`2088967`) was active, pointed to offer `18813` and had integer `share=100`. The request omitted `landings` and the readback returned `[]`. |
| AC-C-06 | Keitaro returned HTTP 200 for the campaign POST and both `POST /admin_api/v1/streams` calls. Adrobot returned HTTP 201 `SUCCESS` after reading the campaign and both streams back. The editor HTML selected the new campaign, and its Fetch endpoint returned the same ordered stream and binding data. This run exercised the application HTTP paths without a browser. |

The smoke left campaign `216881` active. A future smoke needs separate write
authorization, a new uniquely named test campaign and fresh GET validation of
the selected resources. Keep the API key in backend settings. Use
`ADROBOT_CREATE_OFFER_ACTION_TYPE=http` for the tested configuration. Enable
`ADROBOT_CREATE_MAPPING_VERIFIED` and `ADROBOT_CREATE_WRITES_ENABLED` only for
the isolated run. Submit once and verify the result by reading it back. On
`PARTIAL` or `UNKNOWN`, stop writes and inspect the campaign by ID or alias
before deciding on recovery. Retrying or deleting requires separate approval.

Multi-GEO input (AC-C-07), traffic delivery, Push and unequal shares were
outside this smoke.

## 2026-11-20 — Create campaign-group preflight (GET-only)

On `https://tlgk.host`, discovery at **12:54–12:55 UTC** observed
`GET /admin_api/v1/campaign_groups` returning HTTP 404. The correct catalog is
`GET /admin_api/v1/groups?type=campaigns`: HTTP 200, an array of 36 groups with
`id`, `name`, `position` and `type=campaigns`; none has a `state` field.
Requiring the shared domain/source `state` model therefore rejects real groups
even after correcting the endpoint.

At **13:10:34 UTC (16:10:34 Moscow)** the corrected application's
`KeitaroClient.list_campaign_groups()` successfully read all 36 groups. A request
guard allowed only that exact GET URL; TLS verification was enabled and redirects
were disabled. Parsed ID/name/type values matched the response in memory.
Response SHA-256:
`2861b2d88ecced64d8e3d671c205dd1fb7bebb00f25d4418c5fc5ba0b2fe5a94`.
No raw response, names or credentials were saved in this document.

The adapter validates positive JavaScript-safe integer IDs, nonempty names and
`type=campaigns`, rejects duplicate IDs, invalid JSON/entries, lists over 20000
items and partial-response markers. Extra upstream fields are accepted. The
service checks that the configured group ID exists without inventing an activity
state. Domains and traffic sources retain their required `state` and `active`
checks. These validation limits are application rules, not claims about all
possible Keitaro pagination modes.

This verifies the group-read correction only (CRE-05/09, INT-01/03, OPEN-01/05).
Defaults and write/mapping flags were not changed; no Keitaro write was made.
The intended test resources, precise instance version/schema and remaining
Create payload assumptions still require confirmation before separately
authorized live smoke. Offline Create tests do not close live AC-C-01/02/06.

## 2026-11-20 — live application and browser verification

Observed at **09:20–09:24 UTC (12:20–12:24 Moscow)**, using Python 3.12.3 and
Chrome 158.0.8127.36. Verified source content matches Git
`435120413db810cd3b9d663d672b4d5b23740ae1`; source/test hashes matched the
offline-verified implementation. Only this
notes file was updated after these runtime checks. The backend key came from
local ignored settings and was sent only in the `Api-Key` header.

- CLI `live_fetch 215477`: exit 0, HTTP 200, two streams, one offer stream and
  one binding; correlated start/complete and HTTP completion logs.
- Independent upstream GET compared in memory with the loopback route: exact
  narrow projection matched, including order `(position,id)`, schema, binding
  identity, state and raw share; `Cache-Control: no-store` was present.
- Stream `1326672`: position 1, `schema=redirect`, no bindings. Stream `1326673`:
  position 2, `schema=landings`, one binding. Binding `2080989` referred to global
  offer `18813`, state `active`, raw share `100`.
- HTTP 200 returned a JSON array with `application/json; charset=utf-8` and no
  partial-response or pagination headers. This describes the observed response;
  it does not establish pagination behavior for other endpoint modes or datasets.
- Real Chrome headless loaded the production HTML/JS from the loopback backend,
  submitted Fetch and displayed the same ordered streams and binding values.
  Redirect/filter details and the backend key were absent from the public
  projection; browser API requests contained no API credentials.
- A browser-only offline failure retained the successful table, showed an error
  and restored controls. Server operation counts confirmed that this failed
  attempt did not reach Keitaro. No upstream error or mutation was induced.
- The CLI and two loopback Fetch calls each logged one correlated start/complete
  pair. The independent reference read made the fourth authorized upstream GET.
  The configured key was absent from captured application logs and CLI output.

This establishes live application compatibility for the observed read, not
Create/Push/readback, unequal-share conversion or an empty offer-stream. OPEN-02/04
remain open. [GitHub Actions run 44708836529](https://github.com/Kordava/adrobot-keitaro-wrapper/actions/runs/44708836529)
completed successfully for that exact source commit. The subsequent notes-only
delta was checked locally; the broader product acceptance remains separate.

## 2026-11-20 — instance-linked OpenAPI documentation

Source: the authenticated [Admin API documentation](https://tlgk.host/admin/?object=adminApi)
linked by the user. Inspected the Swagger UI's actual OpenAPI document; no
Try-it-out operation or campaign mutation was executed. This is documentation
evidence, separate from the live application results above.

- The document is OpenAPI `3.0.0`, API version `v1`, server path `/admin_api/v1`.
  API `v1` is not the installed Keitaro build/version.
- `ApiKeyAuth` requires an API key in the `Api-Key` header, matching the verified
  backend transport.
- `GET /campaigns/{id}/streams` has one required integer path parameter `id`.
  Neither path-level nor operation-level parameters declare pagination. Its HTTP
  200 `application/json` response is an array of `Stream`; no response headers
  are declared. Together with the observed full array and absent pagination
  headers, this supports the current single-read implementation of this endpoint.
- `Stream.offers[]` refers to `OfferStream`, whose `id`, `stream_id`, `offer_id`
  and `share` are integers and `state` is a string. The read schema specifies no
  share minimum, maximum or description explaining percentage/weight conversion;
  it therefore does not resolve OPEN-02 for unequal shares.
- `StreamObject.schema` lists `landings`, `redirect` and `action`. It does not
  explicitly describe empty `landings` classification or its allowed persistence
  behavior. OPEN-04 still requires an actual empty-stream example; no new write
  behavior is inferred from the enum.

The documentation agrees with the observed read contract and adds no requirement
to change the approved Fetch implementation. No code or test was changed.

## 2026-11-20 — application validation update (offline)

This section records the offline rules before a backend key was configured.
The live verification above supersedes its earlier integration status; historical
API Explorer observations below remain historical evidence. Unequal-share
semantics and empty `landings` remain OPEN-02/04.

The client accepts only HTTP 200 with a JSON array. Content-Range or a Link with
`rel=next` rejects the read as incomplete; these are defensive checks, not observed
Keitaro pagination behavior. Other successful status codes, nonstandard JSON
NaN/Infinity, malformed nested fields and unsafe integers reject the whole Fetch.
Upstream 422 maps to `keitaro_upstream_validation_error`, separately from local 422.

The read rule is explicit: `landings` is an offer stream, including an empty
offers array; any other nonempty schema is non-offer only when bindings are empty.
Bindings on `redirect` or an unknown schema are rejected. Present filter
`stream_id` must match the parent; filters remain outside the public projection.
IDs, raw shares and positions must fit the JavaScript exact integer range; no
rounding, percentage conversion or share normalization is performed. These are
application validation rules and do not establish additional live API facts.

## 2026-11-19 — target UI inspection (read-only)

Evidence: authorized browser inspection of `https://tlgk.host/admin` (UI, **not** Admin API JSON).

- Login to the test instance succeeded; edition displays **Enterprise**; precise build/version not visible.
- Existing campaign `test_bekbolot_e2e_DE` shows rotation **by position**, source `FORTESTS`, group `VZ adrobot test`, domain `https://kylovoa.com/`.
- Tracking UI shows stream **1326672** (`Geo DE → Google`) with Germany filter and Google redirect, and stream **1326673** (`Offers`). These are stream IDs, **not** campaign IDs.
- UI observations do not establish numeric domain/group/source IDs, Admin API JSON shapes, offer share semantics, or safe update fields. OPEN-01…06 remain unverified.
- Admin API reference is accessible in the web UI, but browser sign-in does not necessarily authorize the Admin API.

### Safe Admin API diagnostic

With a valid locally configured Admin API key:

```bash
uv run python -m adrobot.keitaro.inspect <NUMERIC_TEST_CAMPAIGN_ID>
```

The tool makes one read-only GET through our existing client and emits only field names, field types, and list lengths, not response values or credentials. Mock tests verify redaction properties. This is *not* proof of live API compatibility until actually run against the target.

## 2026-11-19 — live Admin API GET evidence (read-only)

Evidence: the in-app Swagger/API Explorer returned successful GET JSON from the provided test instance. **No mutations performed. Sensitive values deliberately omitted.**

### Authorization and campaign/stream reads

- Browser session without Admin API authentication: GET returned `401` with `Invalid API key`.
- A valid *existing* Admin API key provided authorized GET access in API Explorer. No new key was created. The explorer used a query parameter in this observation; `Api-Key` request header is documented and remains to be tested from this application's own HTTP client. **Never put API keys in URLs: query strings can be retained in logs.** The application continues to use only the header.
- `GET /admin_api/v1/campaigns` returned an array of campaigns; fields include `id` (integer), `alias`, `name`, `type` (`position`), `state`, `group_id` (integer), `traffic_source_id` (integer), `domain_id` (integer), and `domain` (string).
- `GET /admin_api/v1/campaigns/{id}/streams` returned an **array of stream objects**. Relevant fields: `id`, `name`, `campaign_id`, `position`, `type`, `state`, `schema`, `action_type`, `action_payload`, `filters`, `offers`, `landings`, `weight`, `offer_selection`, `triggers`, `comments`, `action_options`. The existing adapter's array + numeric `id` + string `name` checks match this observed shape.
- `GET /admin_api/v1/streams/{id}` returned a single stream with the same field structure.

### Nested schemas

- Offer binding object in `offers[]`: `id` (integer binding ID), `stream_id` (integer), `offer_id` (integer), `state` (string), `share` (integer), `created_at`, `updated_at`. Distinguish `id` (binding ID) from `offer_id` (global offer ID).
- GEO filter in `filters[]`: `id`, `stream_id`, `name` (`country`), `mode` (`accept`), `payload` (list of country codes), `oid`.
- Redirect stream observed: `schema=redirect`, `action_type=http`, URL in `action_payload`, `offers=[]`, country accept filter.
- Offer stream observed: `schema=landings`, `offers=[{...}]`, `landings=[]`, `action_payload=null`.
- Offer binding `share=100` was returned on a single-offer stream. This confirms **read field type**, not behavior of `share` with multiple unequal offers, normalization or update mapping.
- Non-offer redirect stream can be recognized by `schema=redirect` in these observations; a **zero-offer offer-stream** (`schema=landings` with `offers=[]`) was not observed and remains OPEN-04.

### Application read-model contract (TASK-01 v0.3)

The implemented read route validates *all* returned stream entries before emitting a response. Required fields: `id`, `campaign_id`, `name`, `position`, `state`, `schema`, `filters[]`, `offers[]`. Required binding fields: binding `id`, `stream_id`, global `offer_id`, `state`, integer `share`. Cross-campaign IDs, mismatched stream IDs, repeated binding IDs and malformed nested fields cause a sanitized **502**. Identical global `offer_id` is not rejected here (OPEN-03).

The public `GET /api/campaigns/{id}/editor` currently returns `mapping_status=remote_only`, `read_only=true` and sorted streams with a narrow binding projection. A `schema=landings` stream with `offers=[]` is still tagged as an offer stream by the **inferred** parser; the live zero-offer case remains OPEN-04. Redirect/filter fields are not exposed to browser. `share` is deliberately not called `displayed_share` and is not mathematically normalized until OPEN-02 is resolved.

Fake ASGI-through-FastAPI tests confirm this mapping and the `Api-Key` header. **They are not evidence of a live app Fetch.** On a networked authorized workstation run `uv run python -m adrobot.keitaro.live_fetch <campaign-id>` and record whether the actual route succeeds.

### Discovery status and remaining work

- **OPEN-01 (read subset):** live routes and JSON shape confirmed. App's own header authorization and live end-to-end Fetch remain unverified; create/update endpoints not examined.
- **OPEN-02:** `offers[].share` observed as integer `100`; display/rounding rules for nontrivial mixes and update semantics still unverified.
- **OPEN-03:** explicit binding `id` exists. Whether repeated identical `offer_id` can occur in the same stream remains unverified; do not choose the archive unique key yet.
- **OPEN-04:** redirect has `offers=[]`; no direct zero-offer `landings` stream sample or evidence about saving an empty offer-stream.
- **OPEN-05:** IDs for group/source/domain exist and are integers; the intended configured defaults must be resolved/confirmed separately for the correct named objects.
- **OPEN-06:** safe update payload and atomic compare-and-swap remain unverified; do not write.

**Security:** Authentication artifacts observed in the read-only API Explorer must not be included in application source, documentation, fixtures or shared output. Keys seen in URLs should be considered exposed to URL logging; rotate them according to owner policy if necessary.
