/* tailtest dashboard -- vanilla JS, no frameworks */

// --- State ---
const state = { status: null, findings: [], events: [], connected: false };
let activeKind = 'all';
let newSessionOnly = false;
let sessionStart = null;
let userScrolledDown = false;
let wsRetryDelay = 1000;
let wsRetryTimer = null;

// --- Helpers ---

function relativeTime(isoStr) {
  if (!isoStr) return '';
  const delta = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
  if (delta < 5) return 'just now';
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

function severityIcon(severity) {
  if (!severity) return '';
  const s = severity.toLowerCase();
  if (s === 'error' || s === 'critical' || s === 'high') return '✗';
  if (s === 'warning' || s === 'medium') return '⚠';
  return 'ℹ';
}

function severityClass(severity) {
  if (!severity) return '';
  const s = severity.toLowerCase();
  if (s === 'error' || s === 'critical' || s === 'high') return 'sev-error';
  if (s === 'warning' || s === 'medium') return 'sev-warning';
  return 'sev-info';
}

function kindColor(kind) {
  const map = { edit: 'event-edit', run: 'event-run', finding: 'event-finding',
    recommendation: 'event-recommendation', session_start: 'event-session_start' };
  return map[kind] || '';
}

function depthBadgeClass(depth) {
  const map = { off: 'badge-gray', quick: 'badge-blue', standard: 'badge-green',
    thorough: 'badge-orange', paranoid: 'badge-red' };
  return map[depth] || 'badge-gray';
}

function aiBadgeClass(surface) {
  const map = { agent: 'badge-purple', utility: 'badge-teal', none: 'badge-gray' };
  return map[surface] || 'badge-gray';
}

function eventDescription(event) {
  const kind = event.kind || '';
  if (kind === 'session_start') return 'Session started';
  if (kind === 'run') return `Run: ${event.test_path || event.runner || ''}`;
  if (kind === 'edit') return `Edit: ${event.file_path || ''}`;
  if (kind === 'finding') return `Finding: ${event.message || ''}`;
  if (kind === 'recommendation') return `Rec: ${event.message || ''}`;
  return JSON.stringify(event).slice(0, 60);
}

// --- WebSocket ---

function connectWS() {
  const ws = new WebSocket(`ws://${location.host}/live`);
  ws.onopen = () => { state.connected = true; wsRetryDelay = 1000; updateConnectionIndicator(); };
  ws.onclose = () => { state.connected = false; updateConnectionIndicator(); reconnectWS(); };
  ws.onerror = () => ws.close();
  ws.onmessage = (e) => {
    try { handleWSMessage(JSON.parse(e.data)); } catch (_) { /* ignore malformed */ }
  };
}

function reconnectWS() {
  if (wsRetryTimer) clearTimeout(wsRetryTimer);
  wsRetryTimer = setTimeout(() => { wsRetryTimer = null; connectWS(); }, wsRetryDelay);
  wsRetryDelay = Math.min(wsRetryDelay * 2, 30000);
}

function handleWSMessage(msg) {
  if (msg.kind === 'event') { prependEvent(msg.payload); updateLastEvent(msg.payload.timestamp); }
  if (msg.kind === 'ping') { updateLastEvent(new Date().toISOString()); }
  if (msg.kind === 'profile_update') { updateStatus(msg.payload); }
}

// --- Bootstrap ---

async function init() {
  sessionStart = new Date().toISOString();
  setupFilters();
  setupEventFeedScroll();
  await Promise.all([fetchStatus(), fetchFindings(), fetchEvents()]);
  connectWS();
}

async function fetchStatus() {
  try {
    const resp = await fetch('/api/status');
    if (!resp.ok) return;
    const data = await resp.json();
    state.status = data;
    renderStatus(data);
  } catch (_) { /* server may not be ready */ }
}

async function fetchFindings() {
  try {
    const resp = await fetch('/api/findings');
    if (!resp.ok) return;
    const data = await resp.json();
    state.findings = data.findings || [];
    renderFindings(state.findings);
  } catch (_) { /* ignore */ }
}

async function fetchEvents() {
  try {
    const resp = await fetch('/api/events');
    if (!resp.ok) return;
    const data = await resp.json();
    state.events = data.events || [];
    renderAllEvents(state.events);
  } catch (_) { /* ignore */ }
}

// --- Render: status bar ---

function renderStatus(data) {
  const profile = data.profile || {};
  const config = data.config || {};
  const nameEl = document.getElementById('project-name');
  if (profile.name) nameEl.textContent = profile.name;
  const depthEl = document.getElementById('depth-badge');
  const depth = config.depth || 'off';
  depthEl.textContent = depth;
  depthEl.className = `badge ${depthBadgeClass(depth)}`;
  const aiEl = document.getElementById('ai-badge');
  const aiSurface = profile.ai_surface || 'none';
  aiEl.textContent = aiSurface;
  aiEl.className = `badge ${aiBadgeClass(aiSurface)}`;
  const countEl = document.getElementById('finding-count');
  if (typeof data.baseline_count === 'number') {
    countEl.textContent = `${data.baseline_count} baseline`;
  }
}

function updateStatus(payload) {
  if (!state.status) state.status = {};
  Object.assign(state.status, payload);
  renderStatus(state.status);
}

function updateLastEvent(isoStr) {
  const el = document.getElementById('last-event');
  el.textContent = isoStr ? `Last: ${relativeTime(isoStr)}` : '';
  el.dataset.iso = isoStr || '';
}

function updateConnectionIndicator() {
  const dot = document.getElementById('connection-dot');
  dot.classList.toggle('connected', state.connected);
  dot.title = state.connected ? 'Connected' : 'Disconnected';
}

// Refresh relative timestamps every 30s
setInterval(() => {
  const el = document.getElementById('last-event');
  if (el && el.dataset.iso) updateLastEvent(el.dataset.iso);
  document.querySelectorAll('.event-time[data-iso]').forEach((span) => {
    span.textContent = relativeTime(span.dataset.iso);
  });
}, 30000);

// --- Render: event feed ---

function setupEventFeedScroll() {
  const list = document.getElementById('event-feed-list');
  list.addEventListener('scroll', () => { userScrolledDown = list.scrollTop > 8; });
}

function renderAllEvents(events) {
  const list = document.getElementById('event-feed-list');
  const placeholder = document.getElementById('events-placeholder');
  list.querySelectorAll('.event-item').forEach((el) => el.remove());
  if (!events || events.length === 0) { placeholder.style.display = ''; return; }
  placeholder.style.display = 'none';
  events.forEach((event) => list.appendChild(buildEventEl(event)));
}

function prependEvent(event) {
  state.events.unshift(event);
  const list = document.getElementById('event-feed-list');
  document.getElementById('events-placeholder').style.display = 'none';
  const el = buildEventEl(event);
  const first = list.querySelector('.event-item');
  first ? list.insertBefore(el, first) : list.appendChild(el);
  if (!userScrolledDown) list.scrollTop = 0;
  if (event.kind === 'finding') fetchFindings();
}

function buildEventEl(event) {
  const div = document.createElement('div');
  div.className = `event-item ${kindColor(event.kind || '')}`;
  const header = document.createElement('div');
  header.className = 'event-header';
  const dot = document.createElement('span');
  dot.className = 'event-dot';
  const kindLabel = document.createElement('span');
  kindLabel.className = 'event-kind-label';
  kindLabel.textContent = event.kind || 'event';
  const timeSpan = document.createElement('span');
  timeSpan.className = 'event-time';
  timeSpan.dataset.iso = event.timestamp || '';
  timeSpan.textContent = relativeTime(event.timestamp);
  header.appendChild(dot);
  header.appendChild(kindLabel);
  header.appendChild(timeSpan);
  const desc = document.createElement('div');
  desc.className = 'event-desc';
  desc.textContent = eventDescription(event);
  const payload = document.createElement('pre');
  payload.className = 'event-payload';
  payload.textContent = JSON.stringify(event, null, 2);
  div.appendChild(header);
  div.appendChild(desc);
  div.appendChild(payload);
  div.addEventListener('click', () => div.classList.toggle('expanded'));
  return div;
}

// --- Render: findings ---

function setupFilters() {
  document.querySelectorAll('.filter-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.filter-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      activeKind = btn.dataset.kind || 'all';
      renderFindings(state.findings);
    });
  });
  const toggle = document.getElementById('new-session-toggle');
  toggle.addEventListener('change', () => { newSessionOnly = toggle.checked; renderFindings(state.findings); });
}

function applyFilters(findings) {
  let result = findings;
  if (activeKind !== 'all') result = result.filter((f) => (f.kind || '').toLowerCase() === activeKind);
  if (newSessionOnly && sessionStart) result = result.filter((f) => f.timestamp && f.timestamp >= sessionStart);
  return result;
}

function renderFindings(findings) {
  const filtered = applyFilters(findings || []);
  const placeholder = document.getElementById('findings-placeholder');
  const table = document.getElementById('findings-table');
  const tbody = document.getElementById('findings-tbody');
  const badge = document.getElementById('findings-count-badge');
  badge.textContent = String(filtered.length);
  if (filtered.length === 0) { placeholder.style.display = ''; table.style.display = 'none'; return; }
  placeholder.style.display = 'none';
  table.style.display = '';
  tbody.innerHTML = '';
  filtered.forEach((finding) => buildFindingRows(finding).forEach((row) => tbody.appendChild(row)));
}

function buildFindingRows(finding) {
  const mainRow = document.createElement('tr');
  mainRow.dataset.id = finding.id || '';
  const detailRow = document.createElement('tr');
  const detailTd = document.createElement('td');
  detailTd.colSpan = 5;
  const detailDiv = document.createElement('div');
  detailDiv.className = 'finding-detail';
  const msgP = document.createElement('p');
  msgP.textContent = finding.message || '';
  detailDiv.appendChild(msgP);
  if (finding.fix_hint) {
    const hintP = document.createElement('p');
    hintP.className = 'fix-hint';
    hintP.textContent = finding.fix_hint;
    detailDiv.appendChild(hintP);
  }
  detailTd.appendChild(detailDiv);
  detailRow.appendChild(detailTd);
  mainRow.addEventListener('click', (e) => {
    if (e.target.classList.contains('dismiss-btn')) return;
    detailDiv.classList.toggle('visible');
  });
  const sevTd = document.createElement('td');
  const sevSpan = document.createElement('span');
  sevSpan.className = `sev-icon ${severityClass(finding.severity)}`;
  sevSpan.textContent = severityIcon(finding.severity);
  sevTd.appendChild(sevSpan);
  const kindTd = document.createElement('td');
  kindTd.textContent = finding.kind || '';
  const fileTd = document.createElement('td');
  fileTd.className = 'file-cell';
  const loc = finding.location || {};
  fileTd.textContent = loc.file ? `${loc.file}${loc.line ? ':' + loc.line : ''}` : '';
  fileTd.title = fileTd.textContent;
  const msgTd = document.createElement('td');
  msgTd.className = 'msg-cell';
  msgTd.textContent = finding.message || '';
  msgTd.title = finding.message || '';
  const actionsTd = document.createElement('td');
  const dismissBtn = document.createElement('button');
  dismissBtn.className = 'dismiss-btn';
  dismissBtn.textContent = 'Dismiss';
  dismissBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    dismissFinding(finding.id, mainRow, detailRow);
  });
  actionsTd.appendChild(dismissBtn);
  mainRow.appendChild(sevTd);
  mainRow.appendChild(kindTd);
  mainRow.appendChild(fileTd);
  mainRow.appendChild(msgTd);
  mainRow.appendChild(actionsTd);
  return [mainRow, detailRow];
}

async function dismissFinding(id, mainRow, detailRow) {
  if (!id) return;
  try {
    const resp = await fetch(`/api/dismiss/${encodeURIComponent(id)}`, { method: 'POST' });
    if (resp.ok) {
      state.findings = state.findings.filter((f) => f.id !== id);
      mainRow.remove();
      detailRow.remove();
      const tbody = document.getElementById('findings-tbody');
      const remaining = tbody.querySelectorAll('tr[data-id]').length;
      document.getElementById('findings-count-badge').textContent = String(remaining);
      if (remaining === 0) {
        document.getElementById('findings-placeholder').style.display = '';
        document.getElementById('findings-table').style.display = 'none';
      }
    }
  } catch (_) { /* ignore */ }
}

document.addEventListener('DOMContentLoaded', init);
