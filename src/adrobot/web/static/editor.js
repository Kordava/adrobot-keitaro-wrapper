'use strict';

const form = document.querySelector('#fetch-form');
const button = document.querySelector('#fetch-button');
const idInput = document.querySelector('#campaign-id');
const list = document.querySelector('#stream-list');
const status = document.querySelector('#status');

function element(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function streamCard(stream) {
  const card = document.createElement('section');
  card.className = 'stream-card';
  card.appendChild(element('h2', `${stream.name} · #${stream.id}`));
  card.appendChild(element(
    'p',
    `Позиция ${stream.position} · ${stream.state} · ${stream.kind === 'offers' ? 'офферный поток' : 'другой тип потока'}`,
    'stream-meta',
  ));

  if (stream.kind !== 'offers') {
    card.appendChild(element('p', 'Без редактирования и разбора redirect/фильтров.', 'muted'));
    return card;
  }
  if (stream.offers.length === 0) {
    card.appendChild(element('p', 'В Keitaro сейчас нет привязок. История появится в TASK-03.', 'muted'));
    return card;
  }

  const table = document.createElement('table');
  const header = document.createElement('thead');
  const headerRow = document.createElement('tr');
  for (const label of ['Binding ID', 'Offer ID', 'Состояние', 'Share (KT)']) {
    headerRow.appendChild(element('th', label));
  }
  header.appendChild(headerRow);
  table.appendChild(header);

  const body = document.createElement('tbody');
  for (const offer of stream.offers) {
    const row = document.createElement('tr');
    for (const text of [offer.binding_id, offer.offer_id, offer.state, offer.share]) {
      row.appendChild(element('td', String(text)));
    }
    body.appendChild(row);
  }
  table.appendChild(body);
  card.appendChild(table);
  return card;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const id = Number(idInput.value);
  if (!Number.isSafeInteger(id) || id <= 0) {
    status.textContent = 'Введите положительный числовой ID кампании.';
    return;
  }
  button.disabled = true;
  idInput.disabled = true;
  status.textContent = 'Читаем потоки Keitaro…';
  try {
    const response = await fetch(`/api/campaigns/${id}/editor`, { cache: 'no-store' });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error?.message || result.detail || `HTTP ${response.status}`);
    }
    if (result.mapping_status !== 'remote_only' || !Array.isArray(result.streams)) {
      throw new Error('Получен неожиданный формат ответа Adrobot');
    }
    const view = document.createDocumentFragment();
    view.appendChild(element('h2', `Потоки кампании #${result.campaign_id} (${result.streams.length})`));
    for (const stream of result.streams) view.appendChild(streamCard(stream));
    // Replace only after a *complete* successful response. A read failure
    // must not erase previously displayed data or simulate remote deletion.
    list.replaceChildren(view);
    status.textContent = 'Чтение выполнено. Данные только из Keitaro; изменений и архива пока нет.';
  } catch (error) {
    status.textContent = `Ошибка чтения: ${error.message}. Предыдущие данные (если были) сохранены.`;
  } finally {
    button.disabled = false;
    idInput.disabled = false;
  }
});
