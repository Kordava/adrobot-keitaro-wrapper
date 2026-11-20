// Opt-in real browser regression: start a LOCAL FastAPI with fake Keitaro only.
// Run: ADROBOT_PLAYWRIGHT_MODULE=file:///path/to/playwright/index.mjs node tests/browser/create_flow.spec.mjs
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const port = Number(process.env.ADROBOT_BROWSER_PORT || '18765');
const origin = `http://127.0.0.1:${port}`;
const playwrightModule = process.env.ADROBOT_PLAYWRIGHT_MODULE || 'playwright';
const { chromium } = await import(playwrightModule);
const server = spawn(process.env.ADROBOT_PYTHON || 'python',
  ['tests/browser/fake_server.py'], {
    cwd: projectRoot,
    env: { ...process.env, ADROBOT_BROWSER_PORT: String(port),
      PYTHONPATH: path.join(projectRoot, 'src') },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
let serverErrors = '';
server.stderr.on('data', bytes => { serverErrors += bytes.toString().slice(0, 250); });

async function waitForServer() {
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      const response = await fetch(`${origin}/health`);
      if (response.ok) return;
    } catch (_error) { /* startup */ }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error(`Local-only test server did not start: ${serverErrors.slice(0, 250)}`);
}

async function resetFake() {
  const result = await fetch(`${origin}/__test__/reset`, { method: 'POST' });
  assert.equal(result.status, 200);
}

async function fillCreate(page) {
  await page.goto(origin + '/');
  await page.locator('#create-name').fill('Playwright Sample');
  await page.locator('#create-geo').fill('AU,MX');
  await page.locator('#create-offer-id').fill('42');
}

let browser;
try {
  await waitForServer();
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.ADROBOT_CHROMIUM_PATH || undefined,
    args: process.env.ADROBOT_CHROMIUM_PATH ? ['--no-sandbox'] : [],
  });
  const page = await browser.newPage();
  await resetFake();
  await fillCreate(page);
  await page.locator('#create-submit').click();
  const link = page.locator('#create-result a');
  await link.waitFor();
  assert.equal(await link.getAttribute('href'), '/editor?campaign_id=501');
  const state = await (await fetch(`${origin}/__test__/state`)).json();
  assert.deepEqual([state.campaign_writes, state.stream_writes], [1, 2]);
  const editorGets = [];
  page.on('request', request => {
    if (/\/api\/campaigns\/\d+\/editor/.test(request.url())) editorGets.push(request.url());
  });
  await link.click();
  await page.locator('#stream-list h2').first().waitFor();
  assert.equal(await page.locator('#campaign-id').inputValue(), '501');
  assert.deepEqual(editorGets.map(url => new URL(url).pathname), ['/api/campaigns/501/editor']);
  assert.match(await page.locator('#stream-list').innerText(), /Потоки кампании #501/);
  await page.goto(origin + '/editor');
  assert.equal(await page.locator('#campaign-id').inputValue(), '');
  assert.deepEqual(editorGets.map(url => new URL(url).pathname), ['/api/campaigns/501/editor']);
  await page.close();

  // Failure injection occurs AFTER route.fetch has made the backend finish
  // exactly one full Create against fake Keitaro. Only the browser sees a loss.
  await resetFake();
  const dropped = await browser.newPage();
  await fillCreate(dropped);
  let backendCompleted = false;
  await dropped.route('**/api/campaigns', async route => {
    const response = await route.fetch();
    assert.equal(response.status(), 201);
    const completed = await (await fetch(`${origin}/__test__/state`)).json();
    assert.deepEqual([completed.campaign_writes, completed.stream_writes], [1, 2]);
    backendCompleted = true;
    await route.abort('failed');
  });
  await dropped.locator('#create-submit').click();
  await dropped.getByText('Исход создания не подтверждён', { exact: false }).waitFor();
  assert.equal(backendCompleted, true);
  assert.equal(await dropped.locator('#create-submit').isDisabled(), true);
  assert.equal(await dropped.locator('#create-result a').count(), 0);
  await dropped.waitForTimeout(800);
  const finalState = await (await fetch(`${origin}/__test__/state`)).json();
  assert.deepEqual([finalState.campaign_writes, finalState.stream_writes], [1, 2]);
  await dropped.close();

  // The backend completes the operation but an intermediary replaces its
  // successful response with a syntactically valid JSON 502. This must NOT
  // look like a safe pre-write rejection and must never enable a retry.
  await resetFake();
  const gateway = await browser.newPage();
  await fillCreate(gateway);
  let gatewayCompleted = false;
  let gatewayRequests = 0;
  await gateway.route('**/api/campaigns', async route => {
    gatewayRequests++;
    const upstream = await route.fetch();
    assert.equal(upstream.status(), 201);
    const completed = await (await fetch(`${origin}/__test__/state`)).json();
    assert.deepEqual([completed.campaign_writes, completed.stream_writes], [1, 2]);
    gatewayCompleted = true;
    await route.fulfill({
      status: 502,
      contentType: 'application/json',
      body: JSON.stringify({error: {code: 'bad_gateway', message: 'Gateway failure'}}),
    });
  });
  await gateway.locator('#create-submit').click();
  await gateway.getByText('Исход создания не подтверждён', { exact: false }).waitFor();
  assert.equal(gatewayCompleted, true);
  assert.equal(await gateway.locator('#create-submit').isDisabled(), true);
  assert.equal(await gateway.locator('#create-result a').count(), 0);
  await gateway.waitForTimeout(800);
  const gatewayState = await (await fetch(`${origin}/__test__/state`)).json();
  assert.deepEqual([gatewayState.campaign_writes, gatewayState.stream_writes], [1, 2]);
  assert.equal(gatewayRequests, 1);
  await gateway.close();

  // A real backend PARTIAL response must distinguish the attempted operation
  // from the last positively acknowledged step in the user-visible message.
  await resetFake();
  assert.equal((await fetch(`${origin}/__test__/scenario/partial_fallback`,
    {method: 'POST'})).status, 200);
  const partial = await browser.newPage();
  await fillCreate(partial);
  await partial.locator('#create-submit').click();
  await partial.getByText('Последний подтверждённый этап:', { exact: false }).waitFor();
  const partialStatus = await partial.locator('#create-status').innerText();
  assert.match(partialStatus, /получен ответ на создание GEO-потока/);
  assert.match(partialStatus, /Этап ошибки: создание fallback-потока/);
  assert.equal(await partial.locator('#create-submit').isDisabled(), true);
  const partialState = await (await fetch(`${origin}/__test__/state`)).json();
  assert.deepEqual([partialState.campaign_writes, partialState.stream_writes], [1, 2]);
  await partial.close();
  process.stdout.write('PASS: browser link, dropped response, gateway JSON after Create, partial stage\n');
} finally {
  if (browser) await browser.close();
  server.kill('SIGTERM');
}
