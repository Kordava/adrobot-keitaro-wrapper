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
  if (!stream.editor || !Array.isArray(stream.known_offers)) {
    card.appendChild(element('p', 'История потока недоступна.', 'muted'));
    return card;
  }

  const table = document.createElement('table');
  const header = document.createElement('thead');
  const headerRow = document.createElement('tr');
  for (const label of ['Offer ID', 'Имя', 'Статус Adrobot', 'Keitaro', 'Share / история', 'Pin']) {
    headerRow.appendChild(element('th', label));
  }
  header.appendChild(headerRow);
  table.appendChild(header);

  const body = document.createElement('tbody');
  for (const offer of stream.known_offers) {
    const row = document.createElement('tr');
    const live = offer.status === 'Active';
    const values = [
      offer.offer_id,
      offer.name,
      offer.status,
      live ? `${offer.kt_state} · binding #${offer.binding_id}` : `last binding #${offer.last_binding_id ?? '—'}`,
      live ? `${offer.display_share} (KT raw)` : `Removed / 0% · last KT raw ${offer.last_seen_raw_share}`,
      offer.pinned_share === null ? 'Unpinned' : `${offer.pinned_share}%`,
    ];
    for (const value of values) row.appendChild(element('td', String(value)));
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
    if (result.mapping_status !== 'archive_synced' || result.local_archive_synced !== true || !Array.isArray(result.streams)) {
      throw new Error('Получен неожиданный формат ответа Adrobot');
    }
    const view = document.createDocumentFragment();
    view.appendChild(element('h2', `Потоки кампании #${result.campaign_id} (${result.streams.length})`));
    for (const stream of result.streams) view.appendChild(streamCard(stream));
    // Replace only after a *complete* successful response. A read failure
    // must not erase previously displayed data or simulate remote deletion.
    list.replaceChildren(view);
    status.textContent = 'Fetch завершён; архив синхронизирован. Это просмотр без публикации в Keitaro.';
  } catch (error) {
    status.textContent = `Ошибка чтения: ${error.message}. Предыдущие данные (если были) сохранены.`;
  } finally {
    button.disabled = false;
    idInput.disabled = false;
  }
});

// Created campaign links use the exact same Fetch path as manual submission.
if (idInput.dataset.autofetch === 'true') form.requestSubmit();
