'use strict';

const $ = id => document.getElementById(id);
const S = {token: null, book: null, chapters: new Set(), units: new Set(), runId: null,
  lastSeq: 0, timer: null, observation: 0, submitting: false, active: false, runState: null};
/* 失败卡必须回答的八件事。**唯一一份定义**：服务端返回 labels，前端只按这个顺序摊开；
   少一格就在卡上标出来（"缺"不许当成"通过"）。 */
const CARD_FIELDS = [['blocker', '卡点'], ['method', '方法'], ['criterion', '标准'],
  ['owner', '责任方'], ['current_version', '当前版本'], ['previous_failure', '前次失败'],
  ['attempts', '已耗轮次'], ['stop_reason', '停下原因']];
const MEDIA_PHASE_LABEL = {planning: '媒体规划', draft: '媒体 draft（草稿档）',
  check: '媒体检查（画面/关键帧）', final: '媒体 final（成片档）'};
const ACTIVE = new Set(['queued', 'running', 'cancelling', 'stopping']);
const STATUS = {queued: '等待启动', running: '执行中', cancelling: '正在取消', cancelled: '已取消',
  succeeded: '执行完成', needs_review: '执行完成，质量待复核', failed: '执行失败',
  blocked: '执行受阻', stopping: '正在收尾', interrupted: '已中断，可恢复'};

function stored(key) { try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch (_) { return null; } }
function save(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) {} }
function remember() { save('aifs.workbench', {bookId: S.book, runId: S.runId}); }
function count(value) { return Number(value || 0).toLocaleString('zh-CN'); }
async function api(path, options = {}) {
  const opts = {...options, headers: {...(options.headers || {})}};
  if (opts.body) Object.assign(opts.headers, {'Content-Type': 'application/json', 'X-Aifs-Token': S.token || ''});
  const response = await fetch(path, opts);
  const data = await response.json().catch(() => ({error: '响应不是 JSON'}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}
function body(extra = {}) {
  const payload = {book_id: S.book, goal: $('goal').value, profile: $('profile').value,
    prepare_policy: $('prepare').value, parallelism: Number($('parallel').value),
    ...extra};
  if (!S.book) throw new Error('请先选择教材。');
  if (!payload.selectors) {
    const mode = $('scope-mode').value;
    if (mode === 'all') payload.all = true;
    else if (mode === 'units') payload.units = [...S.units];
    else payload.chapters = $('chapters-input').value.trim() || [...S.chapters];
  }
  return payload;
}
function button(text, action, className = '') {
  const el = document.createElement('button'); el.type = 'button'; el.textContent = text;
  el.className = className; el.onclick = action; return el;
}
function selectionView() {
  const mode = $('scope-mode').value;
  $('chapter-list').hidden = mode !== 'chapters'; $('chapter-input-row').hidden = mode !== 'chapters';
  $('unit-list').hidden = mode !== 'units';
  $('selection-summary').textContent = mode === 'all' ? '范围：整本教材。' : mode === 'chapters'
    ? '已勾选 ' + S.chapters.size + ' 章；输入章号时以输入为准。' : '已选择 ' + S.units.size + ' 个教学单元。';
}
function choices(target, rows, field, selected) {
  $(target).replaceChildren();
  rows.forEach(row => {
    const label = document.createElement('label'); label.className = 'chip';
    const input = document.createElement('input'); input.type = 'checkbox'; input.value = row[field];
    input.checked = selected.has(row[field]);
    input.onchange = () => { if (input.checked) selected.add(row[field]); else selected.delete(row[field]); selectionView(); };
    const text = document.createElement('span'); text.textContent = row[field] + ' ' + (row.title || '');
    label.append(input, text); $(target).append(label);
  });
}
async function loadBooks() {
  const data = await api('/api/books'); S.token = data.token;
  $('book-list').replaceChildren();
  data.books.forEach(book => {
    const el = button(book.title + (book.catalog_present ? '' : ' · 待准备'),
      () => selectBook(book).catch(showError), 'card');
    el.dataset.book = book.book_id; $('book-list').append(el);
  });
  const previous = stored('aifs.workbench');
  const book = data.books.find(item => item.book_id === previous?.bookId) || data.books[0];
  if (book) await selectBook(book, previous?.bookId === book.book_id ? previous.runId : null);
  else $('book-list').textContent = '当前没有已登记教材，请先在工作区登记教材。';
}
async function selectBook(book, preferredRun = null) {
  S.observation++; clearTimeout(S.timer); S.book = book.book_id; S.runId = null;
  S.chapters.clear(); S.units.clear(); S.lastSeq = 0;
  const detail = await api('/api/books/' + encodeURIComponent(S.book));
  S.active = Boolean(detail.active);
  [...$('book-list').children].forEach(el => el.classList.toggle('active', el.dataset.book === S.book));
  choices('chapter-list', detail.chapters || [], 'chapter_id', S.chapters);
  choices('unit-list', detail.units || [], 'unit_key', S.units);
  $('prepare-hint').textContent = detail.note;
  $('chapters-input').value = ''; selectionView();
  $('events').replaceChildren(); $('result-out').textContent = ''; $('preview-links').replaceChildren();
  $('preview-frame').hidden = true; $('preview-frame').removeAttribute('src');
  $('cancel-btn').disabled = true; $('resume-btn').disabled = true;
  renderHistory(detail.runs || []);
  const selected = (detail.runs || []).find(run => run.run_id === preferredRun) || (detail.runs || [])[0];
  if (selected) await observeRun(selected.run_id, true);
  else { $('run-summary').textContent = '暂无运行记录。'; $('quality-summary').textContent = '尚无质量结论。';
    $('preparation-progress').textContent = '尚未开始'; $('selected-progress').textContent = '尚未开始'; remember(); }
  $('run-btn').disabled = S.submitting || S.active;
}
function renderHistory(runs) {
  $('history-list').replaceChildren();
  runs.forEach(run => {
    const selectors = run.selectors || {};
    const scope = selectors.kind === 'all' ? '整本' : (selectors.values || []).join('、');
    const el = button(run.run_id + ' · ' + (STATUS[run.status] || run.status) + ' · ' + scope,
      () => observeRun(run.run_id, true).catch(showError));
    el.classList.toggle('active', run.run_id === S.runId); el.dataset.run = run.run_id;
    $('history-list').append(el);
  });
}
async function refreshHistory() {
  const book = S.book;
  const detail = await api('/api/books/' + encodeURIComponent(book));
  if (S.book === book) renderHistory(detail.runs || []);
}
async function showPlan() {
  const data = await api('/api/plans', {method: 'POST', body: JSON.stringify(body())});
  $('plan-summary').textContent = '全书准备：' + ((data.preparation || []).map(row => row.stage + '：' + row.reason).join('；') || '已有准备，执行时核对输入')
    + '\n所选范围：' + (data.target_count == null ? '目录形成后校验，数量待定' : data.target_count + ' 个教学单元');
  $('plan-out').textContent = JSON.stringify(data, null, 2);
  $('capabilities').textContent = '可用能力：' + (data.capabilities?.available || []).join('、');
}
async function startRun() {
  if (S.submitting) return;
  S.submitting = true; $('run-btn').disabled = true;
  try {
    const payload = body(); const signature = JSON.stringify(payload);
    let pending = stored('aifs.pending');
    if (!pending || pending.signature !== signature) {
      pending = {signature, key: 'ui-' + (globalThis.crypto?.randomUUID?.() || Date.now() + '-' + Math.random().toString(16).slice(2))};
      save('aifs.pending', pending);
    }
    payload.idempotency_key = pending.key;
    const data = await api('/api/runs', {method: 'POST', body: JSON.stringify(payload)});
    if (!data.run_id) throw new Error(data.note || '未创建运行。');
    save('aifs.pending', null);
    await observeRun(data.run_id); await refreshHistory();
  } finally { S.submitting = false; $('run-btn').disabled = S.active; }
}
async function observeRun(runId, restore = false) {
  clearTimeout(S.timer); const observation = ++S.observation;
  S.runId = runId; S.lastSeq = 0; $('events').replaceChildren(); remember();
  [...$('history-list').children].forEach(el => el.classList.toggle('active', el.dataset.run === runId));
  await poll(observation, restore);
}
function restoreRequest(request) {
  if (!request) return;
  const selectors = request.selectors || {}; $('scope-mode').value = selectors.kind || 'chapters';
  S.chapters = new Set(selectors.kind === 'chapters' ? selectors.values : []);
  S.units = new Set(selectors.kind === 'units' ? selectors.values : []);
  for (const [id, set] of [['chapter-list', S.chapters], ['unit-list', S.units]]) {
    $(id).querySelectorAll('input').forEach(input => { input.checked = set.has(input.value);
      input.onchange = () => { if (input.checked) set.add(input.value); else set.delete(input.value); selectionView(); }; });
  }
  $('chapters-input').value = '';
  for (const [id, field] of [['goal', 'goal'], ['profile', 'profile'], ['prepare', 'prepare_policy'], ['parallel', 'parallelism']])
    if (request[field] != null) $(id).value = request[field];
  selectionView();
}
function renderProgress(id, group = {}) {
  $(id).replaceChildren();
  const text = document.createElement('p');
  text.textContent = count(group.completed) + ' / ' + (group.total == null ? '待确定' : count(group.total)) + ' 个阶段完成'; $(id).append(text);
  (group.nodes || []).forEach(node => {
    const row = document.createElement('p'); row.className = 'stage';
    const execution = {pending: '等待执行', running: '进行中', succeeded: '已完成', cached: '已复用',
      failed: '失败', blocked: '受阻', cancelled: '已取消', not_applicable: '不适用'};
    row.textContent = node.node_id + ' · ' + (execution[node.execution_state] || node.execution_state)
      + (node.quality_state === 'needs_review' || node.quality_state === 'rejected' ? ' · 待复核' : '');
    if (node.error) row.textContent += ' · ' + (node.error.message || node.error.reason || JSON.stringify(node.error));
    $(id).append(row);
  });
}
function renderMediaRequirements(state) {
  const host = $('media-requirements');
  if (!host) return;
  host.replaceChildren();
  (state.content_versions || []).forEach(row => {
    const p = document.createElement('p');
    p.textContent = row.unit_id + ' · 内容版本 ' + (row.content_revision || '未知')
      + ' · ' + (row.valid ? row.content_hash : '当前文件无效：' + row.error);
    host.append(p);
  });
  (state.media_requirements || []).forEach(row => {
    const box = document.createElement('article');
    const title = document.createElement('h4');
    title.textContent = row.unit_id + ' · 媒体需求版本 ' + (row.revision || '未知') + ' · ' + row.quality_state;
    box.append(title);
    if (!row.valid) {
      const p = document.createElement('p'); p.textContent = '当前需求无效：' + row.error; box.append(p);
    } else {
      (row.decisions || []).forEach(decision => {
        const p = document.createElement('p');
        p.textContent = decision.target_id + ' · ' + (decision.requirement || '') + ' · ' + decision.reason;
        box.append(p);
      });
      (row.requests || []).forEach(request => {
        const p = document.createElement('p');
        const placement = request.placement || {};
        p.textContent = request.request_id + ' · ' + request.learning_goal + ' · 位置：'
          + placement.target_pointer + ' · ' + (placement.anchor_text || '');
        box.append(p);
      });
    }
    host.append(box);
  });
  if (!host.childNodes.length) host.textContent = '尚无本次运行登记的内容或媒体需求。';
}
function renderFeedback(state) {
  renderFailureCards(state.failure_cards || []);
  renderMediaTimeline(state.media_timeline || []);
  renderMediaRequirements(state);
  const limits = state.attempt_limits || {};
  const rounds = limits.content == null ? '未配置' : limits.content;
  $('resume-hint').textContent = '恢复不会重置已派发尝试和返修次数；每个对象内容返修最多 ' + rounds + ' 轮。';
}
function renderFailureCards(cards) {
  const host = $('failure-cards');
  host.replaceChildren();
  if (!cards.length) { host.textContent = '当前没有未解决的问题记录。'; return; }
  cards.forEach(card => {
    const box = document.createElement('article');
    box.className = 'failure-card' + (card.complete ? '' : ' incomplete');
    box.dataset.unit = card.unit_id || ''; box.dataset.ticket = card.ticket_id || '';
    const head = document.createElement('h3');
    head.textContent = (card.unit_id || '（未知单元）')
      + (card.ticket_id ? ' · 工单 ' + String(card.ticket_id).slice(0, 8) : '')
      + ' · ' + (card.severity || '严重度未标') + ' · ' + (card.status || '状态未标');
    box.append(head);
    const list = document.createElement('dl');
    CARD_FIELDS.forEach(([key, label]) => {
      const term = document.createElement('dt'); term.textContent = label;
      const value = document.createElement('dd'); value.dataset.field = key;
      value.textContent = (card.fields && card.fields[key]) || '（未提供）';
      if (!card.fields || !card.fields[key] || (card.missing || []).includes(key)) {
        value.classList.add('missing');
      }
      list.append(term, value);
    });
    box.append(list);
    if ((card.missing || []).length) {
      const note = document.createElement('p'); note.className = 'hint';
      note.textContent = '这张卡还缺：'
        + card.missing.map(name => (CARD_FIELDS.find(row => row[0] === name) || [name, name])[1]).join('、')
        + '。缺格不许当成通过。';
      box.append(note);
    }
    host.append(box);
  });
}
function renderMediaTimeline(rows) {
  const host = $('media-timeline');
  host.replaceChildren();
  const interesting = rows.filter(row => row.media_phase || ['lesson','text_audit','media_plan','figures','audit','interaction','render','delivery'].includes(row.stage));
  if (!interesting.length) {
    host.textContent = '还没有内容审核或媒体阶段的事件（尚未运行到这些阶段）。';
    return;
  }
  const groups = new Map();
  interesting.forEach(row => {
    const key = row.media_phase || row.stage;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  });
  groups.forEach((group, key) => {
    const box = document.createElement('div');
    box.className = 'timeline-group'; box.dataset.phase = key;
    const head = document.createElement('h3');
    head.textContent = (MEDIA_PHASE_LABEL[key] || (group[0].label || key))
      + '（' + group.length + ' 条事件）';
    box.append(head);
    group.slice(-8).forEach(row => {
      const line = document.createElement('p');
      line.className = 'stage'; line.dataset.kind = row.kind || '';
      line.textContent = '[' + row.seq + '] ' + (row.label || row.stage)
        + ' · ' + (row.kind || '') + (row.message ? ' · ' + row.message : '');
      box.append(line);
    });
    host.append(box);
  });
}
function renderResult(state) {
  const previews = state.previews || [];
  $('quality-summary').textContent = (state.execution_complete ? '执行已完成。' : '执行尚未全部完成。')
    + (state.quality_status === 'accepted' ? ' 内容质量已通过。' : state.quality_status === 'needs_review'
      ? ' 内容质量待复核。' : ' 内容质量尚未确认。')
    + (previews.length ? ' 可阅读预览。' : ' 等待产物生成后预览。')
    + (state.release_ready ? ' 已满足正式交付条件。' : ' 尚未达到正式交付条件。');
  $('result-out').textContent = JSON.stringify({status: state.status, exit_code: state.exit_code,
    quality_status: state.quality_status, release_ready: state.release_ready, failure_stage: state.failure_stage,
    error: state.error, stop_reason: state.stop_reason, recovery_hint: state.recovery_hint, usage: state.usage}, null, 2);
  $('preview-links').replaceChildren();
  const diagnose = button('生成诊断预览（未验收）', async () => {
    diagnose.disabled = true;
    try {
      await api('/api/runs/' + S.runId + '/preview', {method:'POST',body:JSON.stringify({})});
      await poll();
    } catch (error) { showError(error); diagnose.disabled = false; }
  });
  diagnose.disabled = Boolean(state.live_worker);
  $('preview-links').append(diagnose);
  const frame = $('preview-frame');
  previews.forEach(preview => {
    const open = () => { frame.hidden = false; frame.src = preview.url; };
    $('preview-links').append(button(preview.title || preview.chapter, open));
    const link = document.createElement('a'); link.href = preview.url; link.target = '_blank';
    link.rel = 'noopener noreferrer'; link.textContent = '独立阅读 ' + preview.chapter; $('preview-links').append(link);
  });
  if (previews.length) {
    if (!previews.some(preview => frame.getAttribute('src') === preview.url)) frame.src = previews[0].url;
    frame.hidden = false;
  } else { frame.hidden = true; frame.removeAttribute('src'); }
}
async function poll(observation = S.observation, restore = false) {
  const runId = S.runId;
  if (!runId) return;
  clearTimeout(S.timer);
  try {
    const [events, state] = await Promise.all([api('/api/runs/' + runId + '/events?after=' + S.lastSeq), api('/api/runs/' + runId)]);
    if (observation !== S.observation) return;
    if (restore) restoreRequest(state.request);
    (events.events || []).forEach(event => {
      S.lastSeq = Math.max(S.lastSeq, event.seq);
      const li = document.createElement('li'); li.textContent = '[' + event.seq + '] ' + event.kind + ' ' + (event.stage || '') + ' ' + (event.message || '');
      $('events').append(li);
    });
    S.active = ACTIVE.has(state.status) || Boolean(state.book_active);
    $('run-summary').textContent = runId + ' · ' + (STATUS[state.status] || state.status || '状态待确认');
    $('cancel-btn').disabled = !state.live_worker || state.status === 'cancelling';
    $('resume-btn').disabled = S.active || state.status === 'succeeded';
    $('run-btn').disabled = S.submitting || S.active;
    renderProgress('preparation-progress', state.progress?.preparation);
    renderProgress('selected-progress', state.progress?.selected); renderResult(state);
    S.runState = state; renderFeedback(state);
    if (!ACTIVE.has(state.status) && !state.book_active) return;
  } catch (error) { if (observation !== S.observation) return; showError(error); }
  S.timer = setTimeout(() => poll(observation), 1000);
}
function showError(error) { $('run-summary').textContent = error.message; }
$('scope-mode').onchange = selectionView;
$('plan-btn').onclick = () => showPlan().catch(error => { $('plan-summary').textContent = error.message; });
$('run-btn').onclick = () => startRun().catch(showError);
$('cancel-btn').onclick = async () => { try { const data = await api('/api/runs/' + S.runId + '/cancel', {method: 'POST', body: '{}'}); $('run-summary').textContent = data.note; await poll(); } catch (error) { showError(error); } };
function resumePayload() { return {}; }
$('resume-btn').onclick = async () => { $('resume-btn').disabled = true; try { const payload = resumePayload(); const data = await api('/api/runs/' + S.runId + '/resume', {method: 'POST', body: JSON.stringify(payload)}); if (data && data.note) $('run-summary').textContent = data.note; else if (!Object.keys(payload).length) $('run-summary').textContent = '续跑：已派发尝试和返修次数保持原样。'; await observeRun(S.runId); await refreshHistory(); } catch (error) { showError(error); $('resume-btn').disabled = false; } };
loadBooks().catch(error => { $('book-list').textContent = '加载教材失败：' + error.message; });
