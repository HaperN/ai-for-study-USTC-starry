"""Independent image TOC → normalized dual-source comparison → image arbitration.

This is the production PDF route. Original MinerU files are immutable inputs;
only an accepted, completely covered decision may replace teaching authority.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from collections import Counter
from pathlib import Path

from .contract import (ContractError, SOURCE_PRINTED, SOURCE_VISUAL, build_toc,
                       file_sha256, read_json, sha256_text, validate_toc, write_json)
from .sources import canonical_number, extract_printed, mathematical_title_key
from .toc_review import render_toc_pages

PROMPTS = Path(__file__).parent / 'prompts'
VERSION = 'dual-toc-3'
BATCH_PAGES = 4
MAX_REVIEW_ENTRIES = 100
MAX_REVIEW_CHARS = 24000
CATEGORIES = {'teaching', 'exercise', 'answers', 'frontmatter', 'reference'}
WARNING_CODES = {'source_anomaly', 'batch_boundary', 'volume_numbering'}
BLOCKING_CODES = {'unreadable', 'uncertain_classification',
                  'incomplete_transcription', 'unresolved_difference'}
OUTPUTS = ('toc.v2.json', 'toc.excluded.json', 'toc.visual.json',
           'toc.mineru-normalized.json', 'toc.comparison.json', 'toc.arbitration.json')


def _text(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value or ''))).strip()


def _category(title):
    """Only exact resource titles; teaching about examples/exercises stays teaching."""
    key = re.sub(r'\s+', '', title)
    if re.fullmatch(r'(?:第[一二三四五六七八九十百零〇\d]+章)?(?:综合习题|总习题|习题|练习题|复习题|练习)(?:[\d.\-一二三四五六七八九十]+)?', key):
        return 'exercise'
    if re.fullmatch(r'(?:习题|练习题)?(?:参考答案|答案|解答)(?:[\d.\-]+)?', key):
        return 'answers'
    if key in {'参考文献', '参考书目', '索引', 'Bibliography', 'Index'}:
        return 'reference'
    if key in {'目录', '目次', '前言', '序言', '序', '出版说明', '版权', 'Contents'}:
        return 'frontmatter'
    return 'teaching'


def normalize_entry(row, *, source, index):
    if not isinstance(row, dict):
        raise ContractError('目录条目必须是对象')
    title = re.sub(r'\s+', ' ', unicodedata.normalize('NFC', str(row.get('title') or ''))).strip()
    title = ''.join(chr(ord(c)-0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in title)
    if not title:
        raise ContractError('目录条目缺 title')
    level = row.get('level')
    if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 4:
        raise ContractError(f'{title}: level 必须是 1..4 整数')
    page = row.get('toc_page')
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ContractError(f'{title}: 缺目录所在 PDF 页 toc_page')
    category = row.get('category') or _category(title)
    if category not in CATEGORIES:
        raise ContractError(f'{title}: 非法 category {category}')
    printed = row.get('printed_page')
    number = _text(row.get('number'))
    marker = re.match(r'^\*+', number)
    number = (marker.group(0) if marker else '') + canonical_number(number)
    return dict(entry_id=f'{source}{index:04d}', number=number,
                title=title, level=level, order=index,
                printed_page=_text(printed) if printed not in (None, '') else None,
                # An image of the directory proves the printed label, not a body PDF offset.
                pdf_page=None, toc_page=page, category=category,
                reason=_text(row.get('reason')))


def comparison_key(row):
    # Never use sources.identity here: it strips mathematical operators.
    return (row['number'], mathematical_title_key(row['title']), row['level'],
            row['printed_page'], row['category'], mathematical_title_key(row.get('parent_key', '')),
            row['toc_page'])


def _parents(rows):
    stack = []
    for row in rows:
        while stack and stack[-1]['level'] >= row['level']:
            stack.pop()
        row['parent_key'] = '/'.join(f"{r['number']}:{r['title']}" for r in stack)
        stack.append(row)


def make_authority(rows, *, book_title, slug, pdf_hash, document_hash):
    nodes, all_entries, stack, identities = [], [], [], set()
    for order, original in enumerate(rows, 1):
        row = {k: original.get(k) for k in ('entry_id', 'number', 'title', 'level',
                'printed_page', 'pdf_page', 'toc_page', 'category', 'reason', 'source_ids', 'basis')}
        row['order'] = order
        while stack and stack[-1]['level'] >= row['level']:
            stack.pop()
        if stack and stack[-1]['category'] != 'teaching' and row['category'] == 'teaching':
            row['category'] = stack[-1]['category']
            row['reason'] = f"随非教学父条目分流：{stack[-1]['title']}；{row['reason']}"
        row['toc_id'] = None
        row['parent_entry_id'] = stack[-1]['entry_id'] if stack else None
        if row['category'] == 'teaching':
            if row['level'] > 1 and not stack:
                raise ContractError(f"教学节点缺父章：{row['title']}")
            parent = stack[-1]['toc_id'] if stack else None
            identity = (parent, row['number'], row['title'])
            if identity in identities:
                raise ContractError(f"重复教学节点：{row['number']} {row['title']}")
            identities.add(identity)
            row['toc_id'] = f'T-{len(nodes)+1:04d}'
            nodes.append(dict(toc_id=row['toc_id'], level=row['level'], number=row['number'],
                title=row['title'], parent=parent, page={'printed':row['printed_page'], 'pdf':row['pdf_page']},
                kind='chapter' if row['level']==1 else 'section', confirmed=True,
                evidence={'from':[SOURCE_PRINTED, SOURCE_VISUAL] if original.get('agreed') else [SOURCE_VISUAL],
                          'agree':bool(original.get('agreed'))}))
            if original.get('basis') == 'mineru':
                nodes[-1]['evidence']['from'] = [SOURCE_PRINTED]
        row['owner_toc_id'] = row['toc_id'] or next(
            (parent['toc_id'] for parent in reversed(stack) if parent.get('toc_id')), None)
        all_entries.append(row)
        stack.append(row)
    if not nodes:
        raise ContractError('没有已确认教学节点，不能输出空教学目录')
    toc = build_toc(slug=slug, book_title=book_title, nodes=nodes,
        sources_used=[SOURCE_PRINTED, SOURCE_VISUAL], confidence='high',
        pdf_sha256=pdf_hash, document_sha256=document_hash,
        reviewed_by={'workflow':VERSION, 'evidence':'toc.dual-source.json'})
    validate_toc(toc)
    return toc, {'schema_version':'1.0', 'entries':[r for r in all_entries if r['toc_id'] is None],
                 'all_entries':all_entries}


def _fingerprint(parse, pdf):
    files = {name:file_sha256(parse/name) if (parse/name).is_file() else None
             for name in ('toc.json', 'document.md', 'headings.json', 'toc.preflight.json')}
    return {'version':VERSION, 'pdf':file_sha256(pdf), 'files':files,
            'implementation':{n:file_sha256(Path(__file__).parent/n) for n in
                ('dual_source.py','contract.py','sources.py','toc_review.py')},
            'prompts':{p.name:file_sha256(p) for p in sorted(PROMPTS.glob('directory_*.md'))}}


def is_current(parse_dir, pdf):
    parse = Path(parse_dir)
    try:
        report = read_json(parse/'toc.dual-source.json')
        return (report.get('status')=='accepted' and report.get('inputs')==_fingerprint(parse, Path(pdf))
                and report.get('outputs')=={n:file_sha256(parse/n) for n in OUTPUTS}
                and bool(report.get('images'))
                and all(file_sha256(Path(s['path']))==s['sha256'] for s in report['images']))
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _pages(parse, source, pdf):
    pages = {r['from_page'] for r in source.get('printed_entries', [])
             if isinstance(r, dict) and isinstance(r.get('from_page'), int) and r['from_page']>0}
    path = parse/'toc.preflight.json'
    if path.is_file():
        pages.update((read_json(path).get('toc_pages') or {}).get('pdf_pages') or [])
    # Independent PDF discovery must run even if MinerU found some pages: an
    # entire missing OCR page is precisely what the visual source must recover.
    from .cli import _find_toc_pages
    pages.update(_find_toc_pages(pdf))
    return sorted(p for p in pages if isinstance(p, int) and not isinstance(p, bool) and p>0)


def _mineru(source, pages):
    rows = []
    for raw in source.get('printed_entries') or []:
        if not isinstance(raw, dict):
            raise ContractError('MinerU 目录行缺 from_page 定位信息，需重新适配')
        extracted = extract_printed([raw])
        if extracted.candidates:
            c = extracted.candidates[0]
            row = dict(title=c.title, number=c.number, level=int(raw.get('level') or c.level),
                       printed_page=c.printed, toc_page=raw.get('from_page'), reason=c.raw)
        else:
            # Preserve failed OCR as comparison material; never quietly omit it.
            row = dict(title=raw.get('text'), number='', level=int(raw.get('level') or 1),
                       printed_page=raw.get('toc_page'), toc_page=raw.get('from_page'),
                       reason='MinerU 行未能可靠拆分，保留原文交由图像裁决')
        if row['toc_page'] not in pages:
            raise ContractError('MinerU 目录行缺已渲染目录页定位')
        rows.append(normalize_entry(row, source='M', index=len(rows)+1))
    _parents(rows)
    return rows


def _schema(data):
    if not isinstance(data, dict) or data.get('schema_version') != '1.0':
        raise ContractError('模型 JSON 必须为 schema_version="1.0" 的对象')


def _reject(code, reason, *, action='按响应契约重新复核并返回结构化结果',
            acceptance='字段、问题等级及引用范围均符合契约'):
    raise ContractError(f'[{code}] {reason}；下一步：{action}；通过标准：{acceptance}')


def _decision_issues(data, pages):
    """Only explicitly typed, scoped source observations may be non-blocking.

    Never infer severity from free text. Old problems/unresolved remain blocking
    even when complete=true or the new issues array contains only warnings.
    """
    for legacy in ('problems', 'unresolved'):
        if data.get(legacy):
            _reject(f'legacy_{legacy}',
                    f'旧版 {legacy} 无法区分来源说明与阻断问题：'
                    + json.dumps(data[legacy], ensure_ascii=False),
                    action='依据原图将每项明确分类为 issues 中的 warning 或 blocking',
                    acceptance='旧问题均有明确等级和页面依据，且无未解决 blocking')
    issues = data.get('issues', [])
    if not isinstance(issues, list):
        _reject('issue_contract', 'issues 必须是数组')
    for index, item in enumerate(issues, 1):
        label = f'issues 第{index}项'
        if not isinstance(item, dict):
            _reject('issue_contract', f'{label}必须是对象')
        severity, code = item.get('severity'), item.get('code')
        if severity not in ('warning', 'blocking'):
            _reject('issue_severity', f'{label}缺有效 severity（warning/blocking）')
        if not isinstance(code, str) or code not in WARNING_CODES | BLOCKING_CODES:
            _reject('issue_code', f'{label}含未知问题 code：{code}')
        expected = 'warning' if code in WARNING_CODES else 'blocking'
        if severity != expected:
            _reject('issue_severity', f'{label}的 {code} 必须为 {expected}，不能改写等级')
        scope, affected = item.get('scope'), item.get('pdf_pages')
        if (scope not in ('batch', 'page', 'entry') or not isinstance(affected, list)
                or not affected or any(type(p) is not int or p not in pages for p in affected)
                or len(affected) != len(set(affected))
                or (scope == 'batch' and Counter(affected) != Counter(pages))
                or (scope == 'entry' and (not isinstance(item.get('entry'), str)
                                          or not item['entry'].strip()))):
            _reject('issue_scope', f'{label}缺有效 scope、所送 PDF 页或具体条目标识')
        if not isinstance(item.get('reason'), str) or not item['reason'].strip():
            _reject('issue_reason', f'{label}缺具体 reason')
    blockers = [item for item in issues if item['severity'] == 'blocking']
    if blockers:
        details = '；'.join(f"{item['code']} PDF页{item['pdf_pages']} "
                           f"{item.get('entry') or item['scope']}：{item['reason']}"
                           for item in blockers)
        _reject('blocking_issues', details,
                action='按列出的页和条目补充清晰图像或复核分类、转录与源间差异',
                acceptance='列出的不确定项均有图像依据并解决，且全部所送页和源条目完整覆盖')
    return issues


def _visual(data, pages, offset):
    _schema(data)
    statuses = data.get('pages')
    if (not isinstance(statuses, list) or any(not isinstance(p, dict)
            or type(p.get('pdf_page')) is not int or p['pdf_page'] < 1
            or p.get('status') not in ('toc', 'not_toc', 'unreadable') for p in statuses)):
        _reject('visual_page_contract', 'pages 必须列出整数 PDF 页号和有效 status')
    got, wanted = Counter(p['pdf_page'] for p in statuses), Counter(pages)
    if got != wanted:
        _reject('visual_page_coverage',
                f'页覆盖不匹配：缺少{list((wanted-got).elements())}，'
                f'额外或重复{list((got-wanted).elements())}；所送页{pages}',
                action='按所送页号补全逐页状态并消除重复或额外页',
                acceptance='每个所送 PDF 页在 pages 中恰好出现一次')
    unreadable = [p['pdf_page'] for p in statuses if p['status'] == 'unreadable']
    if unreadable:
        _reject('visual_unreadable', f'模型报告 PDF 页{unreadable}不可读',
                action='提供这些页的清晰图像后复核',
                acceptance='这些页可可靠辨读，并完整转录全部目录行')
    _decision_issues(data, pages)
    if data.get('complete') is not True:
        _reject('visual_incomplete', '模型未确认本批所送页已完整转录（complete 不为 true）',
                action='仅针对本批补全转录，并明确记录仍未解决的问题',
                acceptance='本批全部可见目录行已转录，complete=true，且无 blocking')
    rows = []
    valid = {p['pdf_page'] for p in statuses if p['status']=='toc'}
    for item in data.get('entries') or []:
        row = normalize_entry(item, source='V', index=offset+len(rows)+1)
        if row['toc_page'] not in valid or not row['reason']:
            raise ContractError('视觉条目缺有效图像页或依据')
        rows.append(row)
    if any(not any(r['toc_page']==p for r in rows) for p in valid):
        raise ContractError('视觉声称是目录页却没有抽出任何条目')
    return rows


def _compare(data, mineru, visual):
    _schema(data)
    _decision_issues(data, sorted({r['toc_page'] for r in mineru + visual}))
    m = {r['entry_id']:r for r in mineru}; v = {r['entry_id']:r for r in visual}
    seen, differences = [], []
    previous = -1
    matches = data.get('matches') or []
    for match in sorted(matches, key=lambda x: m.get(x.get('mineru_id'),{}).get('order',float('inf'))):
        mid, vid = match.get('mineru_id'), match.get('visual_id')
        if ((mid is None and vid is None) or (mid is not None and mid not in m)
                or (vid is not None and vid not in v)):
            raise ContractError('比较结果含不存在的源 ID 或空匹配')
        seen.extend(i for i in (mid, vid) if i is not None)
        same = bool(mid and vid and comparison_key(m[mid])==comparison_key(v[vid]))
        if same:
            # Matching sequence must agree as well, including repeated section labels.
            rank = v[vid]['order']
            if rank <= previous:
                same = False
            previous = rank
        if match.get('status') != 'same' or not same:
            differences.append(match)
    if Counter(seen)!=Counter([*m, *v]):
        raise ContractError('比较未逐条完整覆盖两份目录（缺失、重复或未知源 ID）')
    return differences


def _arbitrate(data, mineru, visual, pages):
    _schema(data)
    _decision_issues(data, pages)
    sources = {r['entry_id']: r for r in mineru+visual}
    source_ids = set(sources)
    mineru_ids = {r['entry_id'] for r in mineru}
    visual_ids = {r['entry_id'] for r in visual}
    seen, rows = [], []
    for item in data.get('entries') or []:
        ids = item.get('source_ids')
        if not isinstance(ids, list) or any(i not in source_ids for i in ids):
            raise ContractError('复核条目必须明确引用有效 source_ids，图中新补漏项用 []')
        basis = item.get('basis')
        if basis not in ('visual','mineru','both') or not _text(item.get('reason')):
            raise ContractError('复核条目缺选择依据和理由')
        row = normalize_entry(item, source='A', index=len(rows)+1)
        if row['toc_page'] not in pages:
            raise ContractError('复核条目引用未发送图像页')
        if basis == 'both':
            pair = (len(ids) == 2 and len(set(ids)) == 2
                    and len(set(ids) & mineru_ids) == 1 and len(set(ids) & visual_ids) == 1)
            agreed = pair and comparison_key(sources[ids[0]]) == comparison_key(sources[ids[1]])
            # The output hierarchy is rebuilt from the adjudicated sequence;
            # its title/number/level/page/category must still match BOTH inputs.
            output_agrees = agreed and comparison_key(row)[:-2] == comparison_key(sources[ids[0]])[:-2]
            if not output_agrees or (pair and row['toc_page'] != sources[ids[0]]['toc_page']):
                detail = {i: {k: sources[i].get(k) for k in
                          ('number', 'title', 'level', 'printed_page', 'category', 'parent_key', 'toc_page')}
                          for i in ids}
                _reject('both_conflict', f"{row['number']} {row['title']} 的 both 未证明两源及输出一致："
                        + json.dumps(detail, ensure_ascii=False),
                        action='按原图明确选择 basis=visual 或 basis=mineru，并给出位置、正确内容和差异理由',
                        acceptance='both 恰好引用两源各一条且规范条目一致；有分歧时明确选源并完整交代来源')
            row['agreed'] = True
        row['source_ids'] = list(ids)
        row['basis'] = basis
        seen.extend(ids)
        rows.append(row)
    for discarded in data.get('discarded') or []:
        ids = discarded.get('source_ids')
        if (not isinstance(ids,list) or not ids or any(i not in source_ids for i in ids)
                or not _text(discarded.get('reason')) or discarded.get('toc_page') not in pages):
            raise ContractError('舍弃 OCR/视觉误读必须留源 ID、图像页和具体理由')
        seen.extend(ids)
    if Counter(seen)!=Counter(source_ids):
        raise ContractError('复核没有完整交代全部源条目的去向')
    return rows


def _request_fingerprint(data):
    return sha256_text(json.dumps(data, ensure_ascii=False, sort_keys=True))


def _review_batches(mineru, visual, pages):
    """Page scope is evidenced by both sources; never guess entry alignment to split it."""
    return [{'pdf_pages': [page],
             'mineru': [r for r in mineru if r['toc_page'] == page],
             'visual': [r for r in visual if r['toc_page'] == page]}
            for page in pages]


def _preceding_ancestors(rows):
    stack = []
    for row in rows:
        while stack and stack[-1]['level'] >= row['level']:
            stack.pop()
        stack.append(row)
    # Context is already adjudicated, never another source entry to account for.
    return [{k: row[k] for k in ('number', 'title', 'level', 'toc_page', 'category')}
            for row in stack]


def _review_size(data):
    counts = {name: len(data.get(name, [])) for name in ('mineru', 'visual')}
    chars = len(json.dumps(data, ensure_ascii=False))
    pages = data.get('pdf_pages') or sorted({r['toc_page'] for name in counts for r in data.get(name, [])})
    if sum(counts.values()) > MAX_REVIEW_ENTRIES or chars > MAX_REVIEW_CHARS:
        _reject('review_batch_limit',
                f'PDF页{pages}复核输入含{counts}条、{chars}字符，超过单页上限'
                f'（{MAX_REVIEW_ENTRIES}条/{MAX_REVIEW_CHARS}字符）',
                action='检查该页重复抽取或异常长文本；若原页确实密集，提供有明确栏/行边界的分区证据后复核',
                acceptance='在不遗漏、合并或猜测源条目对应关系的前提下，单页复核输入符合上限')
    return counts, chars, pages


def generate_directory(parse_dir, pdf, client, log=None):
    parse, pdf = Path(parse_dir), Path(pdf)
    if is_current(parse, pdf):
        return {**read_json(parse/'toc.dual-source.json'), 'reused':True}
    report = {'schema_version':'1.0', 'status':'needs_review', 'problems':[], 'warnings':[], 'calls':[]}
    def save(name, data):
        write_json(parse/name, data)
    def record_warnings(data):
        # Called only after the full response passes its contract. The raw issue
        # remains in the hashed visual/arbitration artifact as audit evidence.
        report['warnings'].extend({'tag':report['calls'][-1]['tag'], 'issue':item}
                                  for item in data.get('issues', []) if item['severity']=='warning')
    def call(kind, data, images=None):
        if kind != 'visual':
            counts, chars, scope = _review_size(data)
        else:
            counts, chars, scope = {}, len(json.dumps(data, ensure_ascii=False)), data['pdf_pages']
        tag = f'toc-{kind}:{len(report["calls"])+1}'
        prompt = (PROMPTS/f'directory_{kind}.md').read_text(encoding='utf-8')
        if log:
            log(f'目录 {kind}：{len(images or [])} 张图像', tag=tag)
        previous = getattr(client, 'resource_id', '')
        client.resource_id = tag
        started = time.monotonic()
        record = {'tag': tag, 'images': list(images or []), 'pdf_pages': scope,
                  'entry_counts': counts, 'request_chars': chars,
                  'request_fingerprint': _request_fingerprint(data), 'status': 'running'}
        report['calls'].append(record)
        save('toc.dual-source.json', report)
        try:
            result = client.chat_json([{'role':'system','content':prompt},
                    {'role':'user','content':json.dumps(data, ensure_ascii=False)}], tag=tag, images=images)
            record.update(status='succeeded', usage=result.get('usage'), cached=result.get('cached'),
                          fingerprint=result.get('fingerprint'))
        except Exception as exc:
            record.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            raise
        finally:
            client.resource_id = previous
            record['seconds'] = round(time.monotonic()-started, 3)
            save('toc.dual-source.json', report)
        return result['data']
    try:
        report['inputs'] = _fingerprint(parse,pdf)
        save('toc.dual-source.json',report)  # A failed new attempt invalidates old acceptance.
        source = read_json(parse/'toc.json')
        pages = _pages(parse,source,pdf)
        if not pages:
            raise ContractError('找不到完整 PDF 目录页；请补充目录页定位，不回退纯文本视觉复核')
        shots = render_toc_pages(pdf,pages,parse/'toc-images')
        if Counter(s['pdf_page'] for s in shots)!=Counter(pages) or any(not Path(s['path']).is_file() for s in shots):
            raise ContractError('PDF 目录截图不完整，禁止无图视觉生成')
        by_page = {s['pdf_page']:s for s in shots}
        report['images'] = [{'pdf_page':p, 'path':str(by_page[p]['path']),
                             'sha256':file_sha256(by_page[p]['path']),
                             **{key:by_page[p][key] for key in
                                ('width','height','bytes','source_images','effective_dpi','render_policy','fallback_reason')
                                if key in by_page[p]}} for p in pages]
        mineru = _mineru(source,pages)
        save('toc.mineru-normalized.json', {'schema_version':'1.0','entries':mineru})
        visual, batches = [], []
        for start in range(0,len(pages),BATCH_PAGES):
            group = pages[start:start+BATCH_PAGES]
            raw = call('visual', {'pdf_pages':group}, [str(by_page[p]['path']) for p in group])
            batches.append({'pdf_pages':group,'response':raw})
            save('toc.visual.json',{'schema_version':'1.0','batches':batches,'entries':visual})
            visual.extend(_visual(raw,group,len(visual)))
            record_warnings(raw)
        if not visual:
            raise ContractError('视觉目录为空，无法建立教学目录')
        _parents(visual)
        save('toc.visual.json',{'schema_version':'1.0','batches':batches,'entries':visual})
        groups = _review_batches(mineru, visual, pages)
        comparison = {'schema_version': '1.0', 'matches': [], 'batches': []}
        for group in groups:
            data = {name: group[name] for name in ('mineru', 'visual', 'pdf_pages')}
            raw = call('compare', data)
            comparison['batches'].append({'pdf_pages': group['pdf_pages'],
                'request_fingerprint': _request_fingerprint(data), 'response': raw})
            save('toc.comparison.json', comparison)
            _compare(raw, group['mineru'], group['visual'])
            comparison['matches'].extend(raw.get('matches') or [])
            record_warnings(raw)
        # Per-page contracts do not replace global source coverage/order checks.
        save('toc.comparison.json', comparison)
        differences = _compare(comparison, mineru, visual)
        if differences:
            arbitration = {'schema_version': '1.0', 'entries': [], 'discarded': [], 'batches': []}
            final = []
            for group in groups:
                ids = {r['entry_id'] for name in ('mineru', 'visual') for r in group[name]}
                data = {**group, 'differences': [d for d in differences
                    if d.get('mineru_id') in ids or d.get('visual_id') in ids],
                    'context': {'preceding_ancestors': _preceding_ancestors(final)}}
                raw = call('arbitrate', data, [str(by_page[p]['path']) for p in group['pdf_pages']])
                arbitration['batches'].append({'pdf_pages': group['pdf_pages'],
                    'request_fingerprint': _request_fingerprint(data), 'response': raw})
                save('toc.arbitration.json', arbitration)
                final.extend(_arbitrate(raw, group['mineru'], group['visual'], group['pdf_pages']))
                arbitration['entries'].extend(raw.get('entries') or [])
                arbitration['discarded'].extend(raw.get('discarded') or [])
                record_warnings(raw)
            save('toc.arbitration.json', arbitration)
            final = _arbitrate(arbitration, mineru, visual, pages)
            _parents(final)
            sources = {r['entry_id']: r for r in mineru + visual}
            for row in final:
                if row.get('basis') == 'both' and comparison_key(row) != comparison_key(sources[row['source_ids'][0]]):
                    _reject('both_parent_conflict',
                            f"{row['number']} {row['title']} 合并后的父路径 {row['parent_key']} 与 both 来源不一致",
                            action='依据前批裁决的父级与本页图像明确选源，不能沿用旧父级的两源一致声明',
                            acceptance='全部批次合并后的真实父路径与 both 引用的两源一致')
        else:
            save('toc.arbitration.json',{'schema_version':'1.0','status':'not_needed','reason':'两源逐条及顺序一致'})
            final = [{**r,'agreed':True} for r in visual]
        toc,excluded = make_authority(final,book_title=source.get('book_title',''),
            slug=(source.get('_meta') or {}).get('slug') or parse.name,
            pdf_hash=report['inputs']['pdf'], document_hash=report['inputs']['files']['document.md'])
        if report['inputs'] != _fingerprint(parse,pdf):
            raise ContractError('目录生成期间源文件变化，禁止发布过期目录')
        save('toc.v2.json',toc); save('toc.excluded.json',excluded)
        report.update(status='accepted', teaching_nodes=len(toc['nodes']), excluded_entries=len(excluded['entries']),
                      differences=len(differences), outputs={n:file_sha256(parse/n) for n in OUTPUTS})
    except Exception as exc:
        from aiforstudy.pipeline.services import reraise_control_error
        report['problems'].append(f'{type(exc).__name__}: {exc}')
        save('toc.dual-source.json',report)
        reraise_control_error(exc)
    save('toc.dual-source.json',report)
    return report
