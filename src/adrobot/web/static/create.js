'use strict';

const createForm = document.querySelector('#create-form');
const createButton = document.querySelector('#create-submit');
const createName = document.querySelector('#create-name');
const createGeo = document.querySelector('#create-geo');
const offerIdInput = document.querySelector('#create-offer-id');
const offerSearch = document.querySelector('#offer-search');
const offerResults = document.querySelector('#offer-results');
const createStatus = document.querySelector('#create-status');
const createResult = document.querySelector('#create-result');
const defaultsStatus = document.querySelector('#create-defaults');
let submitting = false;
let unconfirmed = false;
let lookupSerial = 0;

// Only our own, correlated *pre-write* errors can establish a safe rejection.
// A gateway can return well-formed JSON after Keitaro has already committed.
const knownNoWriteErrors = Object.freeze({
  invalid_origin: 403,
  invalid_create_input: 422,
  invalid_offer: 422,
  create_unavailable: 503,
  create_rejected: 502,
  keitaro_not_configured: 503,
});
const lastConfirmedLabels = Object.freeze({
  none: 'нет подтверждённых ответов на запись',
  campaign_post_accepted: 'получен ответ на создание кампании',
  campaign_readback_verified: 'параметры кампании проверены чтением',
  geo_post_accepted: 'получен ответ на создание GEO-потока',
  fallback_post_accepted: 'получен ответ на создание fallback-потока',
});
const attemptedStageLabels = Object.freeze({
  create_campaign: 'создание кампании',
  campaign_created: 'проверка созданной кампании',
  create_geo_redirect: 'создание GEO-потока',
  create_offer_fallback: 'создание fallback-потока',
  readback: 'итоговая проверка',
});

function correlatedBackendError(response, body) {
  const id = response.headers.get('X-Request-ID');
  return typeof id === 'string' && /^[a-f0-9-]{36}$/.test(id) &&
    body && typeof body.error === 'object' && body.error !== null &&
    body.error.request_id === id && typeof body.error.code === 'string' &&
    typeof body.error.message === 'string';
}

function confirmedNoWrite(response, body) {
  return correlatedBackendError(response, body) &&
    !Object.hasOwn(body, 'creation') &&
    Object.hasOwn(knownNoWriteErrors, body.error.code) &&
    knownNoWriteErrors[body.error.code] === response.status;
}

function showText(target, text) {
  target.textContent = text;
}

function showUnconfirmed() {
  unconfirmed = true;
  createButton.disabled = true;
  showText(createStatus,
    'Исход создания не подтверждён. Проверьте кампании в Keitaro вручную перед новой попыткой. ' +
    'Автоматического повтора не будет.');
}

async function loadReferences() {
  try {
    const response = await fetch('/api/reference-data', { cache: 'no-store' });
    const body = await response.json();
    if (!response.ok || !body.available) {
      showText(defaultsStatus, 'Настройки Create пока недоступны. Fetch существующих кампаний работает.');
      return;
    }
    showText(defaultsStatus,
      `Настройки: домен ${body.domain}; группа ${body.group}; источник ${body.traffic_source}.`);
  } catch (_error) {
    showText(defaultsStatus, 'Не удалось проверить настройки Create.');
  }
}

async function searchOffers() {
  const serial = ++lookupSerial;
  const text = offerSearch.value.trim();
  offerResults.replaceChildren();
  if (!text) return;
  try {
    const response = await fetch(`/api/offers?query=${encodeURIComponent(text)}`, { cache: 'no-store' });
    if (!response.ok) throw new Error('lookup failed');
    const body = await response.json();
    if (serial !== lookupSerial || !Array.isArray(body.offers)) return;
    for (const offer of body.offers) {
      if (!Number.isSafeInteger(offer.id) || offer.id <= 0 || typeof offer.name !== 'string') continue;
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'offer-option';
      option.textContent = `#${offer.id} — ${offer.name}`;
      option.addEventListener('click', () => {
        offerIdInput.value = String(offer.id);
        offerSearch.value = offer.name;
        offerResults.replaceChildren();
      });
      offerResults.appendChild(option);
    }
  } catch (_error) {
    if (serial === lookupSerial) showText(offerResults, 'Поиск офферов временно недоступен.');
  }
}

offerSearch.addEventListener('input', searchOffers);

createForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (submitting || unconfirmed) return;
  const offerId = Number(offerIdInput.value);
  if (!Number.isSafeInteger(offerId) || offerId <= 0) {
    showText(createStatus, 'Введите корректный положительный Offer ID.');
    return;
  }
  submitting = true;
  createButton.disabled = true;
  createResult.replaceChildren();
  showText(createStatus, 'Создаём кампанию. Не отправляйте запрос повторно…');
  let response;
  let body;
  try {
    response = await fetch('/api/campaigns', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      cache: 'no-store',
      body: JSON.stringify({ name: createName.value, geo: createGeo.value, offer_id: offerId }),
    });
    // A network disconnect, empty/unparseable JSON or an unrecognized response
    // cannot establish the outcome of a POST. Never resubmit automatically.
    body = await response.json();
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
      showUnconfirmed();
      return;
    }
    if (response.status === 201 && body.status === 'SUCCESS') {
      const id = body.campaign_id;
      if (!Number.isSafeInteger(id) || id <= 0 ||
          body.editor_url !== `/editor?campaign_id=${id}`) {
        showUnconfirmed();
        return;
      }
      showText(createStatus, `Кампания #${id} создана и проверена повторным чтением Keitaro.`);
      const link = document.createElement('a');
      link.href = body.editor_url;
      link.textContent = 'Открыть созданную кампанию в editor';
      createResult.appendChild(link);
      return;
    }
    if (correlatedBackendError(response, body) && body.creation &&
        ((response.status === 409 && body.creation.status === 'PARTIAL' &&
          body.error.code === 'create_partial') ||
         (response.status === 502 && body.creation.status === 'UNKNOWN' &&
          body.error.code === 'create_unknown'))) {
      const creation = body.creation;
      if (!Object.hasOwn(lastConfirmedLabels, creation.last_confirmed_stage) ||
          !Object.hasOwn(attemptedStageLabels, creation.stage)) {
        showUnconfirmed();
        return;
      }
      const id = creation.campaign_id;
      const idText = Number.isSafeInteger(id) && id > 0 ? ` ID: ${id}.` : '';
      showText(createStatus,
        `${creation.status}: создание не подтверждено.${idText} ` +
        `Последний подтверждённый этап: ${lastConfirmedLabels[creation.last_confirmed_stage]}. ` +
        `Этап ошибки: ${attemptedStageLabels[creation.stage]}. ` +
        'Проверьте состояние кампании в Keitaro вручную. Автоповтора нет.');
      unconfirmed = true;
      return;
    }
    if (!response.ok && confirmedNoWrite(response, body)) {
      showText(createStatus, `Не удалось создать: ${body.error.message}`);
      return;
    }
    showUnconfirmed();
  } catch (_error) {
    showUnconfirmed();
  } finally {
    submitting = false;
    if (!unconfirmed) createButton.disabled = false;
  }
});

void loadReferences();
