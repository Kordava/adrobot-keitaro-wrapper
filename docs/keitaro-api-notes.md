# Keitaro Admin API mapping — work in progress

**Status as of 2026-11-19:** read-only Admin API JSON inspected in the target instance via its authenticated API Explorer. The application's own `Api-Key` header has **not** been exercised against the live instance: the development sandbox cannot reach the host. No write operations performed.

| Item | Evidence | Confidence |
|---|---|---|
| Admin API uses `Api-Key` header | Keitaro public Admin API docs, <https://docs.keitaro.io/en/development/admin-api.html> | Documented, app live test pending |
| `GET /admin_api/v1/campaigns/{id}/streams` | In-app API Explorer GET, 2026-11-19 | Observed on live test instance |
| Stream response `offers[]` contains binding `id`, `offer_id`, `share` | In-app API Explorer GET, 2026-11-19 | Observed on live test instance; unequal-share semantics unverified |

**OPEN-01:** read endpoints and nested JSON shape observed; own app live auth, pagination and all mutation endpoints remain unverified.

**OPEN-02:** the exact meaning of `share` and translation to UI percentages remain unverified; no normalization or publishing of shares is implemented.

**OPEN-03/04:** binding `id` vs `offer_id` confirmed; duplicate-binding semantics and an empty offer-stream on the live instance remain unverified; archive schema is not created yet.

**OPEN-05/06:** default IDs and safe update fields/CAS remain unverified.

Next read-only evidence: use a local `.env` with a valid API key, open `/editor`, fetch a known test campaign ID. Examine the response on the server with a narrowly controlled diagnostic tool; do not log raw tokens, cookies or credential headers. The provisional UI only displays IDs/names.

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
