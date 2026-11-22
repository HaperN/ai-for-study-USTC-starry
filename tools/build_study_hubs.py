"""Build chapter practice and paper-selection pages from reviewed course records."""
from __future__ import annotations

import argparse
from collections import OrderedDict
from html import escape
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from aiforstudy.practice.render import build_page, public_question


CSS = '''
:root{color-scheme:light;--ink:#253b31;--muted:#5b6b62;--green:#205c43;--line:#d7e1d8;--wash:#f5f3ed}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--wash);color:var(--ink);font:16px/1.7 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
body>nav{max-width:1080px;margin:auto;padding:12px 28px 0;font-size:13px}main{max-width:1080px;margin:auto;padding:24px 28px 56px}a{color:var(--green);text-underline-offset:3px}a:focus-visible,summary:focus-visible{outline:3px solid #9c6c24;outline-offset:4px}
.course-nav{display:flex;flex-wrap:wrap;gap:8px 24px;margin:0 0 24px;font-size:14px}.course-nav a{display:inline-block;padding:7px 0;text-decoration:none}.course-nav a[aria-current=page]{font-weight:700;border-bottom:2px solid var(--green)}
.hero{padding:30px 34px;border:1px solid var(--line);border-radius:18px;background:#fff}.eyebrow{margin:0 0 8px;font-size:13px;letter-spacing:.1em;font-weight:650;color:var(--green)}h1{margin:0;font-size:clamp(30px,5vw,40px);line-height:1.3;letter-spacing:-.03em}h2{font-size:25px;line-height:1.4}h3{font-size:20px;line-height:1.5}.lead{margin:12px 0 0;color:var(--muted)}.countline{margin:14px 0 0;color:var(--muted);font-size:14px}
.editions{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:22px 0 30px}.edition{display:block;border:1px solid #c5d7c9;border-radius:12px;padding:20px 24px;background:#f8faf7;text-decoration:none}.edition:hover{background:#edf4ec}.edition strong{display:block;font-size:21px}.edition span{display:block;color:var(--muted);font-size:14px;margin-top:5px}
.section-head{margin:30px 0 16px}.section-head h2{margin:0 0 6px}.section-head p{margin:0;color:var(--muted);font-size:14px}.chapter{margin:12px 0;background:#fff;border:1px solid var(--line);border-radius:13px;overflow:hidden}.chapter>summary{cursor:pointer;display:flex;align-items:center;gap:14px;list-style:none;padding:19px 22px}.chapter>summary::-webkit-details-marker{display:none}.chapter>summary::after{content:"＋";font-size:22px;margin-left:8px;color:var(--green)}.chapter[open]>summary::after{content:"−"}.chapter-name{flex:1;min-width:0;font-size:18px;font-weight:650}.chapter-count{font-size:13px;color:var(--muted);white-space:nowrap}.unit-list{list-style:none;padding:0 22px 10px;margin:0}.unit-row{border-top:1px solid #e8ede6}.unit-link{display:flex;align-items:center;justify-content:space-between;gap:18px;min-height:57px;padding:13px 4px;text-decoration:none}.unit-link:hover .unit-title{text-decoration:underline}.unit-title{min-width:0}.unit-kind{display:inline-block;color:var(--muted);font-size:11px;border:1px solid var(--line);padding:0 6px;border-radius:4px;margin-left:9px;vertical-align:1px}.unit-count{font-size:13px;color:var(--muted);white-space:nowrap}.unit-empty .unit-count{font-size:12px}
.exam-hero{background:var(--green);border-color:var(--green);color:white}.exam-hero .eyebrow,.exam-hero .lead{color:#e1ece4}.focus-badge{display:inline-block;font-size:12px;vertical-align:middle;letter-spacing:.04em;margin-left:13px;padding:2px 10px;border:1px solid #8fb49b;border-radius:999px;color:#eff9d4;font-weight:650}.exam-hero .countline{color:#e1ece4}.type-nav{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:22px 0}.type-link{display:block;background:#fff;border:1px solid var(--line);border-radius:12px;padding:17px 20px;text-decoration:none}.type-link strong{display:block;font-size:19px}.type-link span{font-size:13px;color:var(--muted)}
.paper-section{scroll-margin-top:20px;margin:34px 0}.paper-section>header{margin-bottom:15px}.paper-section h2{margin:0 0 6px}.paper-section>header>p{margin:0;color:var(--muted);font-size:14px}.paper-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.paper-card{min-width:0;display:flex;flex-direction:column;padding:24px;background:#fff;border:1px solid var(--line);border-radius:14px}.paper-card h3{margin:9px 0 8px}.paper-meta{font-size:13px;color:var(--muted);margin:0}.paper-label{font-size:12px;font-weight:650;color:var(--green)}.paper-purpose{margin:14px 0 12px}.paper-reference,.paper-obstacle{margin:0 0 12px;color:var(--muted);font-size:13px}.paper-links{display:flex;flex-wrap:wrap;gap:10px;margin-top:auto;padding-top:10px}.paper-links a{font-size:14px;text-decoration:none;padding:8px 12px;border:1px solid #c5d7c9;border-radius:7px;background:#f8faf7}.paper-links a:first-child{background:var(--green);border-color:var(--green);color:#fff}.paper-links a:hover{filter:brightness(.96)}.trial-section{border-top:1px solid var(--line);padding-top:30px}.trial-section .paper-card{background:#fafbf8}.originals{padding:24px 26px;background:#fff;border:1px solid var(--line);border-radius:14px}.originals h2{font-size:22px;margin:0 0 8px}.originals p{margin:0 0 12px;color:var(--muted);font-size:14px}.originals a{display:inline-block;padding:8px 0;font-weight:650}.empty{padding:22px;border:1px solid var(--line);border-radius:12px;background:#fff;color:var(--muted)}.footnote{font-size:13px;color:var(--muted);margin:24px 2px 0}
@media(max-width:650px){body>nav{padding-inline:16px}main{padding:16px 16px 36px}.course-nav{gap:6px 21px;margin-bottom:17px}.hero{padding:24px 22px;border-radius:15px}.lead{font-size:15px}.editions{gap:12px;margin-top:16px}.edition{padding:16px}.edition strong{font-size:19px}.edition span{font-size:13px}.chapter>summary{display:grid;grid-template-columns:minmax(0,1fr) auto;padding:17px 16px;gap:4px 12px}.chapter-name{font-size:17px;grid-column:1}.chapter-count{grid-column:1;margin-right:0}.chapter>summary::after{grid-column:2;grid-row:1 / span 2;margin:0}.unit-list{padding:0 16px 8px}.unit-link{gap:12px;font-size:15px;align-items:flex-start}.unit-count{padding-top:2px}.unit-kind{margin-left:0;margin-right:7px}.unit-empty .unit-count{white-space:normal;min-width:60px;text-align:right}.type-nav{gap:8px}.type-link{padding:14px 12px}.type-link strong{font-size:17px}.paper-grid{grid-template-columns:1fr;gap:14px}.paper-card{padding:21px}.paper-card h3{font-size:19px}.paper-links{display:grid;grid-template-columns:1fr 1fr;gap:8px}.paper-links a{padding:9px 10px;text-align:center}.paper-section{margin:28px 0}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
'''

UNIT_CSS = '''<style>
:root{--ink:#253b31;--muted:#5b6b62;--line:#d7e1d8;--blue:#205c43;--wash:#f5f3ed}
body.practice-page>nav{max-width:1100px;margin:auto;padding:12px 24px 0;font-size:13px}.practice-hero{border-bottom-color:#b8d0be}.practice-page a{color:#205c43}.practice-nav{display:flex;flex-wrap:wrap;gap:8px 24px}.practice-chip{background:#edf4ec}.practice-note{background:#edf4ec;border-left-color:#8ba997}.practice-toolbar{background:#f5f3edee}.practice-toolbar input,.practice-toolbar select,.practice-toolbar button,.practice-link{border-color:#c5d7c9}.practice-q{min-width:0}.practice-q .markdown{overflow-wrap:anywhere}.practice-q .katex-display{max-width:100%}.practice-q summary{color:#205c43}a:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid #9c6c24;outline-offset:3px}
</style>'''


def _id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Invalid course or paper identity')
    return value


def _document(title: str, content: str) -> str:
    active = {'日常巩固': '日常巩固.html', '押题': '押题.html'}[title]
    nav = ''.join(
        f'<a href="{href}"' + (' aria-current="page"' if href == active else '') + f'>{label}</a>'
        for label, href in [('教材解析', '教材/index.html?practice=full'), ('日常巩固', '日常巩固.html'), ('押题', '押题.html')]
    )
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · 复变函数 A</title><style>{CSS}</style></head><body><main><nav class="course-nav" aria-label="课程导航">{nav}</nav>{content}</main></body></html>'''


def _reference(paper: dict) -> str:
    reference = paper.get('reference')
    if isinstance(reference, str):
        return reference
    if not isinstance(reference, dict):
        return ''
    parts = [str(reference[key]) for key in ('course', 'year', 'title') if reference.get(key)]
    pages = reference.get('physical_pages')
    if isinstance(pages, list) and pages:
        parts.append('原卷第 ' + '、'.join(str(page) for page in pages) + ' 页')
    return ' · '.join(parts)


def _paper_card(paper: dict, *, trial: bool = False) -> str:
    edition = {'basic': '基础训练', 'full': '扩展训练', 'custom': '定制训练'}.get(paper.get('edition'), '复习训练')
    label = '结构参照试制 · 不计入正式拟合卷' if trial else edition
    metadata = f"{len(paper['items'])} 题 · 训练分值 {paper['total_points']:g} 分"
    if paper.get('training_minutes'):
        metadata += f" · 建议 {paper['training_minutes']} 分钟"
    reference = _reference(paper)
    reference_html = '<p class="paper-reference">参考母卷：' + escape(reference) + '</p>' if reference else ''
    obstacle = '<p class="paper-obstacle">训练难点：' + escape(paper['obstacle']) + '</p>' if paper.get('obstacle') else ''
    links = ''.join(
        f'<a href="试卷包/生成试卷/{paper["id"]}-{suffix}.{extension}">{label}</a>'
        for suffix, extension, label in [('题卷', 'html', '开始作答'), ('题卷', 'pdf', '题卷 PDF'), ('解析', 'html', '查看解析'), ('解析', 'pdf', '解析 PDF')]
    )
    return f'''<article class="paper-card" data-paper-id="{paper['id']}" data-status="{'reference_trial' if trial else 'formal_training'}"><span class="paper-label">{label}</span><h3>{escape(paper['title'])}</h3><p class="paper-meta">{metadata}</p><p class="paper-purpose">{escape(paper['purpose'])}</p>{reference_html}{obstacle}<nav class="paper-links" aria-label="{escape(paper['title'], quote=True)}">{links}</nav></article>'''


def _nested_questions(questions: list[dict]) -> list[dict]:
    rows = [public_question(question) for question in questions]
    for question in rows:
        question.pop('section',None)  # A unit page already supplies its own heading.
        for owner, field in [(question, 'public_source_path'), (question, 'public_textbook_path'), (question['source'], 'public_path')]:
            value = owner.get(field)
            if value and not urlsplit(value).scheme and not value.startswith(('/', '#')):
                owner[field] = '../' + value
    return rows


def build_hubs(out: str | Path, units: list[dict], questions: list[dict], papers: list[dict]) -> dict:
    """Write two hubs and every unit page without altering the input records.

    Call after the delivery builder has created shared assets, workbooks, paper
    HTML/PDFs, and the source-paper index. Questions and papers must already have
    passed the catalog and paper validators. Zero-question units stay visible.
    """
    out = Path(out)
    chapters: dict[str, dict] = OrderedDict()
    by_unit: dict[str, list] = {}
    for unit in units:
        uid, chapter = _id(unit['unit_id']), _id(unit['chapter_id'])
        if uid in by_unit:
            raise ValueError('Duplicate unit: ' + uid)
        by_unit[uid] = []
        chapters.setdefault(chapter, {'title': unit['chapter_title'], 'units': []})['units'].append(unit)
    for question in questions:
        mounted = question.get('mount_unit_id') or question['unit_id']
        if mounted not in by_unit:
            raise ValueError(question['id'] + ': unknown mounted unit ' + str(mounted))
        by_unit[mounted].append(question)
        seen={mounted}
        for site in question.get('additional_mounts',[]):
            uid=site['unit_id']
            if uid not in by_unit:raise ValueError(question['id']+': unknown related unit '+uid)
            if uid not in seen:
                by_unit[uid].append({**question,'focus_note':site.get('focus_note','')})
                seen.add(uid)
    paper_ids = set()
    for paper in papers:
        pid = _id(paper['id'])
        if pid in paper_ids:
            raise ValueError('Duplicate paper: ' + pid)
        paper_ids.add(pid)
        if paper['kind'] not in ('coverage', 'fitted', 'pressure'):
            raise ValueError('Unknown paper type')
    basic_count = sum(question.get('basic') is True for question in questions)
    pages = {}
    chapter_rows = []
    for chapter_number, (chapter_id, chapter) in enumerate(chapters.items(), 1):
        rows = []
        for unit in chapter['units']:
            uid = unit['unit_id']
            assigned = by_unit[uid]
            kind = {'chapter_overview': '本章导读', 'section_overview': '本节概览'}.get(unit.get('kind'))
            tag = '<span class="unit-kind">' + kind + '</span>' if kind else ''
            count = f'{len(assigned)} 题' if assigned else '暂无配套题'
            rows.append(f'<li class="unit-row{" unit-empty" if not assigned else ""}" data-unit-id="{uid}"><a class="unit-link" href="章节练习/{uid}.html"><span class="unit-title">{tag}{escape(unit["title"])}</span><span class="unit-count">{count}</span></a></li>')
            description = (f'本单元收录 {len(assigned)} 道练习。先独立作答，再展开答案与解析。' if assigned else '本单元目前暂无独立配套题，可以先回到教材复习概念与例题。')
            page = build_page('复变函数 A · ' + unit['title'], _nested_questions(assigned), description=description, assets='../assets', home='../日常巩固.html')
            page = page.replace('<body class="practice-page">', UNIT_CSS + '<body class="practice-page">', 1)
            page = page.replace('返回总目录</a>', '返回日常巩固</a>', 1)
            page = page.replace('复变函数 A · 2026-11-20 制作 · 分级表示', '复变函数 A · 单元练习 · 分级表示', 1)
            page = page.replace('</nav>', f'<a href="../教材/index.html?practice=full#/{uid}">回到教材</a></nav>', 1)
            pages['章节练习/' + uid + '.html'] = page
        amount = len({q['id'] for unit in chapter['units'] for q in by_unit[unit['unit_id']]})
        opened = ' open' if chapter_number == 1 else ''
        chapter_rows.append(f'<details class="chapter" id="{chapter_id}"{opened}><summary><span class="chapter-name">第 {chapter_number} 章 · {escape(chapter["title"])}</span><span class="chapter-count">{len(chapter["units"])} 个单元 · {amount} 题</span></summary><ul class="unit-list">{"".join(rows)}</ul></details>')
    daily = f'''<header class="hero"><p class="eyebrow">复变函数 A</p><h1>日常巩固</h1><p class="lead">沿教材章节练习。先自己完成，再展开解析，回看对应讲解。</p><p class="countline">{len(chapters)} 章 · {len(units)} 个教学单元 · 当前 {len(questions)} 道配套题</p></header><nav class="editions" aria-label="按练习版本选择"><a class="edition" href="基础版-习题集.html"><strong>基础版</strong><span>{basic_count} 题 · 核心方法</span></a><a class="edition" href="完整版-习题集.html"><strong>完整版</strong><span>{len(questions)} 题 · 拓展训练</span></a></nav><section aria-labelledby="chapter-heading"><header class="section-head"><h2 id="chapter-heading">按单元练习</h2><p>展开章节，直接进入对应单元。单元题量按当前收录内容显示。</p></header>{''.join(chapter_rows)}</section><p class="footnote">单元页包含该单元的全部配套题；答案与解析默认收起。</p>'''
    daily=daily.replace('单元题量按当前收录内容显示。','同一道综合题可在相关单元出现，并标明本节练习重点；全书和章题量按题号去重。')
    if (out/'定制版-习题集.html').is_file():
        custom='<a class="edition" href="定制版-习题集.html"><strong>定制版</strong><span>按教师资料与个人选择练习</span></a>'
        daily=daily.replace('</nav><section aria-labelledby="chapter-heading">',custom+'</nav><section aria-labelledby="chapter-heading">',1)
    pages['日常巩固.html'] = _document('日常巩固', daily)

    formal = [paper for paper in papers if paper.get('publication_status') != 'reference_trial']
    trials = [paper for paper in papers if paper.get('publication_status') == 'reference_trial']
    kinds = [('coverage', '覆盖卷', '检查知识点与基本方法，找出需要回看的内容。'), ('fitted', '拟合卷', '按注明的历史母卷结构训练，关注题型与设问顺序。'), ('pressure', '压力卷', '练习条件判断、方法选择与综合推理。')]
    jump_links = ''.join(f'<a class="type-link" href="#{kind}"><strong>{name}</strong><span>{sum(p["kind"] == kind for p in formal)} 套训练卷</span></a>' for kind, name, _ in kinds)
    sections = []
    for kind, name, purpose in kinds:
        selected = [paper for paper in formal if paper['kind'] == kind]
        cards = '<div class="paper-grid">' + ''.join(_paper_card(paper) for paper in selected) + '</div>' if selected else '<p class="empty">当前尚未收录此类正式训练卷。</p>'
        sections.append(f'<section class="paper-section" id="{kind}" aria-labelledby="{kind}-heading"><header><h2 id="{kind}-heading">{name}</h2><p>{purpose}</p></header>{cards}</section>')
    if trials:
        sections.append('<section class="paper-section trial-section" id="reference-trials"><header><h2>结构参照试制卷</h2><p>以下试制卷用于补充练习，任务链未与母卷逐位完全对应，不计入上方的正式拟合卷。</p></header><div class="paper-grid">' + ''.join(_paper_card(paper, trial=True) for paper in trials) + '</div></section>')
    exam = f'''<header class="hero exam-hero"><p class="eyebrow">复变函数 A · 集中复习</p><h1>押题<span class="focus-badge">重点</span></h1><p class="lead">按训练目的选卷。先独立作答，再核对解析。</p><p class="countline">{len(formal)} 套正式训练卷{f' · 另有 {len(trials)} 套结构参照试制卷' if trials else ''}</p></header><nav class="type-nav" aria-label="按试卷类型选择">{jump_links}</nav>{''.join(sections)}<section class="originals"><h2>往年原卷与试卷总目录</h2><p>查看原始试卷，核对参考母卷。各课程原卷分别存档。</p><a href="试卷包/index.html">打开原卷与试卷总目录 →</a></section><p class="footnote">训练分值和建议时长用于复习安排；历史题型参照不代表未来考试命中保证。</p>'''
    pages['押题.html'] = _document('押题', exam)
    for relative, page in pages.items():
        path = out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page, encoding='utf-8')
    return {'daily': str(out/'日常巩固.html'), 'exam': str(out/'押题.html'), 'chapters': len(chapters), 'units': len(units), 'questions': len(questions), 'basic_questions': basic_count, 'formal_papers': len(formal), 'reference_trials': len(trials), 'empty_units': sum(not rows for rows in by_unit.values())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('out', 'units', 'catalog', 'papers'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    unit_data = json.loads(args.units.read_text(encoding='utf-8'))
    units = unit_data['units'] if isinstance(unit_data, dict) else unit_data
    report = build_hubs(args.out, units, json.loads(args.catalog.read_text(encoding='utf-8')), json.loads(args.papers.read_text(encoding='utf-8')))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
