/* Portable, read-only course sources. No question solving or inferred metadata. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else {
    root.CourseLibrary = api;
    if (root.__DATA__ && root.__DATA__.course_library) api.mount(root.document, root.__DATA__.course_library);
  }
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const yearOf = s => String(s.academic_year || s.year_as_listed || '未知');
  const pdfUrl = (source, page) => source && /^assets\/source\/[a-f0-9]{64}\.pdf$/.test(source.url || '') && Number.isInteger(page) && page > 0 ? source.url + '#page=' + page : null;
  function locatorUrl(catalog, id) {
    const locator = catalog.locators.find(l => l.id === id);
    return locator ? pdfUrl(catalog.sources.find(s => s.id === locator.source_id), locator.pdf_pages[0]) : null;
  }
  function records(catalog) {
    return [...catalog.textbook_pool.chapters.map(chapter => ({kind:'textbook', course:'教材', year:'', chapter})),
      ...catalog.exam_sessions.map(exam => ({kind:'exam', course:exam.course, year:yearOf(exam), exam}))].map(item => {
      item.searchText = (JSON.stringify(item) + (item.exam && item.exam.known_defective_items.length ? ' 错题' : '')).toLocaleLowerCase();
      return item;
    });
  }
  function matches(item, filters) {
    if (filters.course && item.course !== filters.course) return false;
    if (filters.year && item.year !== filters.year) return false;
    const s = item.exam;
    if (filters.type === 'textbook' && item.kind !== 'textbook') return false;
    if (filters.type === 'questions' && (!s || !s.question_locator_id)) return false;
    if (filters.type === 'answers' && (!s || !s.answer_locator_id)) return false;
    if (filters.type === 'answers-only' && (!s || s.question_locator_id || !s.answer_locator_id)) return false;
    return String(filters.search || '').trim().toLocaleLowerCase().split(/\s+/).filter(Boolean).every(t => item.searchText.includes(t));
  }
  function externalLink(url, label) {
    if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) return '';
    return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`;
  }
  function renderCard(catalog, item, index) {
    function link(id, label) {
      const url = locatorUrl(catalog, id);
      if (!url) return '';
      const pages = catalog.locators.find(l => l.id === id).pdf_pages;
      return `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}<small>PDF ${pages[0] === pages[1] ? pages[0] : pages.join('–')} 页</small></a>`;
    }
    if (item.kind === 'textbook') {
      const c = item.chapter;
      const answers = c.reference_answers;
      const source = catalog.sources.find(s => s.id === catalog.textbook_pool.source_id);
      const questions = (c.pages || []).flatMap(page => (page.new_question_numbers || []).map(number => {
        const url = pdfUrl(source, page.pdf_page);
        return url ? `<a href="${esc(url)}" target="_blank" rel="noopener" aria-label="第${c.chapter}章第${number}题，PDF第${page.pdf_page}页">${number}</a>` : '';
      })).join('');
      return `<article class="course-card" data-record="${index}"><p class="course-meta">教材 · ${c.top_level_question_count} 道顶层习题</p>
<h3>第${c.chapter}章 · ${esc(c.title)}</h3><div class="course-links">${link(c.chapter_locator_id,'章节正文')}${link(c.exercise_locator_id,'章末习题')}${link(c.answer_locator_id,'参考结果 / 提示')}</div>
<details><summary>按题号跳转 · ${c.top_level_question_count} 题</summary><div class="course-question-links">${questions}</div></details>
<p class="course-note">已布置题号未知。参考结果涉及 ${(answers.printed_parent_question_numbers || []).length} 个顶层题号：${esc((answers.printed_parent_question_numbers || []).join('、'))}；不保证子题齐全或完整过程，未验算正确性。</p></article>`;
    }
    const s = item.exam;
    const source = catalog.sources.find(row => row.id === s.source_id);
    const role = !s.question_locator_id ? '仅解答 · 无完整题卷' : s.answer_locator_id ? '题卷与解答' : '题卷 · 未发现成套解答';
    const defects = (s.known_defective_items || []).map(d => `已标错题：PDF第${d.pdf_page}页，第${d.question}题。${d.evidence} 自动判分应隔离。`);
    const notes = [...defects,...(s.content_quality_notes || []), ...(source.provenance || []).map(p => p.answer_note).filter(Boolean)];
    return `<article class="course-card" data-record="${index}"><p class="course-meta">${esc(s.course)} · ${esc(s.class_label || s.course_track || '')}</p>
<h3>${esc(item.year)}${s.academic_year ? ' 学年' : '（网页年份）'} · ${esc(s.exam_type)}${s.makeup ? ' · 补考' : ''}</h3>
<p class="course-tag ${!s.question_locator_id ? 'course-warning' : ''}">${role}</p>
<p class="course-meta">${esc(s.teacher_specific || '该场教师未明确')}</p>
${defects.length ? `<p class="course-warning">${esc(defects.join(' '))}</p>` : ''}
<div class="course-links">${link(s.question_locator_id,'打开题卷')}${link(s.answer_locator_id,'打开解答')}</div>
<details><summary>来源与校核说明</summary>
<p>${externalLink(s.source_url,'来源PDF')} ${externalLink(s.source_page,'来源网页')}</p>
<p>${esc((source.provenance || []).map(p => p.source_kind).filter(Boolean).join('；'))}</p>
<p>网页同一行教师：${esc(s.teachers_row_as_listed || '未列明')}。此列表不代表该场授课教师。</p>
${s.teacher_note ? `<p>${esc(s.teacher_note)}</p>` : ''}
<p>准确考试日期与历史可得时间未知。</p><ul>${notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>
<p class="course-hash">SHA-256 ${esc(source.sha256)}</p></details></article>`;
  }
  function renderStudy(catalog) {
    const study = catalog.exam_study;
    if (!study) return '';
    const sourceLink = (sourceId, page, text) => {
      const url = pdfUrl(catalog.sources.find(s => s.id === sourceId), page);
      return url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)} · PDF ${page}页</a>` : '';
    };
    const percent = x => x == null ? '未知' : (x * 100).toFixed(1) + '%';
    const sessions = Object.fromEntries(catalog.exam_sessions.map(s => [s.id,s]));
    return `<section class="exam-study"><h3>分课程复习单 · 固定6道原子任务</h3>
<p>每题预计20分钟，共120分钟，仅为设计估算。每卡只覆盖一个具体操作；先读原题的域、方向、支路和条件，再独立作答。历史频次不是未来命中率。</p>
${study.courses.map(group => `<details><summary>${esc(group.course)} · ${group.session_count}场正考 · 展开复习单</summary><ol>${group.recommendation.cards.map(card => {
      const t = study.templates[card.template_id];
      const book = study.textbook_links.find(b => b.template_id === card.template_id);
      return `<li><strong>${esc(t.operation)}</strong><p>${esc(t.topic)} · ${esc(t.method)}；${card.frequency_sessions}/${group.session_count}场出现。</p><p>必要条件：${esc(t.necessary_conditions)}</p>
<p>${sourceLink(card.source_id,card.pdf_page,yearOf(sessions[card.session_id])+' 第'+card.printed_label+'题')}</p>
${book ? `<p>配套候选习题（布置状态未知）：${sourceLink(catalog.textbook_pool.source_id,book.pdf_page,'教材第'+book.chapter+'章 '+book.number+book.subquestion+'题')}</p>${book.solution ? `<details><summary>独立校核结果（作答后展开）</summary><p>${esc(book.solution.text)}</p><p>${esc(book.solution.origin)}</p></details>` : ''}` : '<p>对应教材具体小题尚待核，可从下方章末习题池查找。</p>'}
<p class="course-note">考试原题解答未验算，不自动判分。</p></li>`;
    }).join('')}</ol></details>`).join('')}
<details><summary>真实历史顺序回顾 · A四折 / B两折 · 查看三种固定预算基线</summary>
<p>已审阅当前整理版，非盲测。各折只从更早学年的同课程正考选择；三场热身，固定六题（两道共同基础任务：解析函数重建、Taylor展开；其余四道排序），无同年材料、补考或专业复分析。频次按出现的场次计；近期加权每早一场乘0.75；固定顺序基线按预设模板次序。未用结果调参。</p>
<div style="overflow-x:auto"><table><thead><tr><th>课程 / 留出学年</th><th>基线</th><th>已标任务</th><th>知识覆盖</th><th>模板覆盖</th><th>去已核重复后</th></tr></thead><tbody>${study.folds.map(f => Object.entries(f.metrics).map(([m,v]) => `<tr><td>${esc(f.course)} ${esc(yearOf(sessions[f.target_session_id]))}</td><td>${{frequency:'频次',recency:'近期加权',fixed:'固定顺序'}[m]}</td><td>${v.eligible_items}/${v.all_items}</td><td>${percent(v.topic_coverage)}</td><td>${v.template_covered_items}/${v.eligible_items} (${percent(v.template_coverage)})</td><td>${percent(v.coverage_excluding_verified_repeats)}</td></tr>`).join('')).join('')}</tbody></table></div>
<p>分值覆盖：未知（小问分值不全）；新题族覆盖：未知（仅核了三组重复，其余未证实新颖）。去重复列仅排除已核重复，不能叫新题命中率。严格历史可得性 strict_asof 不成立；当前抓取不能证明当时可见。</p></details>
<details><summary>逐题定位与待核记录 · ${study.questions.length}个原子任务</summary>
<p>精确公式保存在原PDF；下列是任务标注，不是完整转写或已验证解答。题号后a/b或序数用于区分同一印刷题号下的独立操作。</p>
${['复变函数A','复变函数B'].map(course=>`<details><summary>${esc(course)}</summary>${catalog.exam_sessions.filter(s=>s.course===course).map(s=>`<details><summary>${esc(yearOf(s))}${s.makeup?' 补考（不参与排序与回测）':''}</summary><ul>${study.questions.filter(q=>q.session_id===s.id).map(q=>`<li>${sourceLink(q.source_id,q.pdf_page,'第'+q.printed_label+'题')} — ${esc(study.templates[q.annotation.template_id].operation)}${q.annotation.status==='pending'?` <strong>待核 / 不评分：${esc(q.annotation.note)}</strong>`:''}</li>`).join('')}</ul></details>`).join('')}</details>`).join('')}
</details></section>`;
  }

  function mount(doc, catalog) {
    const entry = doc.getElementById('course-library-open');
    const dialog = doc.getElementById('course-library-dialog');
    if (!entry || !dialog) return;
    const items = records(catalog);
    const stats = catalog.summary;
    const options = values => values.map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join('');
    dialog.innerHTML = `<header class="course-library-header"><div><p class="course-meta">本地课程资料</p><h2 id="course-library-title">教材习题与历年考试</h2></div><button type="button" id="course-library-close" aria-label="关闭课程资料">关闭</button></header>
<div class="course-library-body"><p>${stats.unique_exam_pdf_files} 份考试PDF / ${stats.exam_sessions} 场考试 · ${stats.textbook_chapters} 章教材 / ${stats.textbook_top_level_questions} 道顶层习题</p>
<p class="course-notice">实际布置题号未知；教材参考结果/提示不等于完整解答。通修A、通修B与数学专业复分析分别记录。试卷来自学生/毕业生维护的目录，非学校官方。</p>
<p class="course-note">点击资料在新标签打开本地PDF。页码为从1开始的PDF物理页；若查看器忽略跳页，请手动输入链接标示页码。搜索仅查目录元数据。</p>
${renderStudy(catalog)}
<div class="course-filters"><label>搜索目录<input id="course-library-search" type="search" placeholder="章节、年份、教师或错题"></label>
<label>课程<select id="course-library-course"><option value="">全部课程</option>${options([...new Set(items.map(i => i.course))])}</select></label>
<label>学年 / 网页年份<select id="course-library-year"><option value="">全部年份</option>${options([...new Set(items.map(i => i.year).filter(Boolean))].sort().reverse())}</select></label>
<label>资料类型<select id="course-library-type"><option value="">全部类型</option><option value="textbook">教材习题</option><option value="questions">考试题卷</option><option value="answers">考试解答</option><option value="answers-only">仅解答</option></select></label>
<button type="button" id="course-library-reset">清除筛选</button></div>
<p id="course-library-status" role="status" aria-live="polite"></p><div class="course-cards">${items.map((item,index) => renderCard(catalog,item,index)).join('')}</div></div>`;
    const fields = ['search','course','year','type'];
    const cards = [...dialog.querySelectorAll('.course-card')];
    const update = () => {
      const filters = Object.fromEntries(fields.map(key => [key,doc.getElementById('course-library-'+key).value]));
      let count = 0;
      cards.forEach((card,index) => {card.hidden = !matches(items[index], filters); if (!card.hidden) count++;});
      doc.getElementById('course-library-status').textContent = count ? `显示 ${count} / ${items.length} 条目录记录（按章或场次计）` : '没有匹配资料，请调整筛选或清除筛选。';
    };
    fields.forEach(key => doc.getElementById('course-library-'+key).addEventListener(key === 'search' ? 'input' : 'change', update));
    doc.getElementById('course-library-reset').addEventListener('click', () => {fields.forEach(key => {doc.getElementById('course-library-'+key).value='';}); update();});
    const close = () => dialog.close();
    doc.getElementById('course-library-close').addEventListener('click',close);
    dialog.addEventListener('click', event => {if (event.target === dialog) close();});
    dialog.addEventListener('close', () => {entry.setAttribute('aria-expanded','false');entry.focus();});
    entry.hidden = false;
    entry.addEventListener('click', () => {dialog.showModal();entry.setAttribute('aria-expanded','true');doc.getElementById('course-library-search').focus();});
    update();
  }
  return {records,matches,locatorUrl,renderCard,renderStudy,mount};
});
