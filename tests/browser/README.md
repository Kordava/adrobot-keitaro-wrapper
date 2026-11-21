# Create and editor browser regressions (fully offline)

This test launches a real loopback FastAPI process with a deterministic `httpx.MockTransport`.
**It never connects to `tlgk.host` or performs real Keitaro writes.** The fixture's
`/__test__` endpoints are not part of the production application.

Run from the repository root with Node.js 20+, Python 3.12/3.13,
Playwright for Node.js and its Chromium browser available in the verifier's
separate environment:

```bash
node tests/browser/create_flow.spec.mjs
```

When using the browser bindings bundled with Python Playwright, a node-resolvable
ESM module may be set without adding a dependency to `uv.lock`:

```bash
ADROBOT_CHROMIUM_PATH=/usr/bin/chromium \
ADROBOT_PLAYWRIGHT_MODULE=file:///path/to/playwright/driver/package/index.mjs \
node tests/browser/create_flow.spec.mjs
```

The test checks (1) successful Create with one campaign POST and two stream POSTs,
then the relative editor link prefills 501 and performs **exactly one** Fetch;
(2) the backend completes a full fake Create but the browser receives no HTTP
response, displays an unconfirmed outcome and sends **no automatic retry**;
(3) a gateway returns **valid JSON 502 after complete Create**: the UI must
still show unconfirmed outcome and disable the button;
(4) a real backend PARTIAL due to the fallback stream POST shows both the last
confirmed GEO response and the failed fallback attempt, without a retry;
(5) editor Fetch renders a known binding as `Removed / 0%` in an empty offer
stream and shows the redirect separately. A malformed neighboring stream
then triggers a real backend 502 while preserving the previous cards and rows.

Record the actual tool versions and exit code. If the browser or bindings are
unavailable, mark browser verification OPEN, not PASS. Real Keitaro POST is
**not** authorized by this fixture.
