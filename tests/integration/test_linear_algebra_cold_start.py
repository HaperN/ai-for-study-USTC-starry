"""Regressions exposed by the real linear-algebra cold run."""
import json
import subprocess
import sys
from pathlib import Path
import pytest


@pytest.mark.parametrize('declared_child', [True, False])
@pytest.mark.parametrize('own_prose', ['', '父节有自己的教学导言。', None])
def test_concept_freeze_only_exempts_readable_heading_only_parents(tmp_path, declared_child, own_prose):
    from blocks.parse.toc.body import build_body
    from aiforstudy.blocks.parse.catalog import build_units
    from aiforstudy.blocks.concepts.index import read_parts
    from aiforstudy.core.identity import Catalog
    child = {'id': 'CH01-S01-SS01', 'kind': 'subsection', 'title': '子节',
             'anchor': '1.1.1', 'original_text': 'child.md', 'content_sha256': 'child'}
    parent = {'id': 'CH01-S01', 'kind': 'section', 'title': '父节', 'anchor': '1.1',
              'original_text': 'parent.md', 'content_sha256': 'parent', 'subsections': [child]}
    chapter = {'id': 'CH01', 'kind': 'chapter', 'title': '章', 'anchor': '1',
               'original_text': 'chapter.md', 'content_sha256': 'chapter', 'sections': [parent]}
    (tmp_path / 'chapters.json').write_text(json.dumps({'chapters': [chapter]}))
    (tmp_path / 'chapter.md').write_text('# 第1章 章\n章导言。')
    (tmp_path / 'child.md').write_text('### 1.1.1 子节\n子节正文。')
    if own_prose is not None:
        (tmp_path / 'parent.md').write_text('## §1.1 父节\n' + own_prose)
    nodes = [{'toc_id': 'T-1', 'level': 1, 'kind': 'chapter', 'title': '章', 'number': '第1章'},
             {'toc_id': 'T-2', 'level': 2, 'kind': 'section', 'title': '父节', 'number': '1.1'}]
    if declared_child:
        nodes.append({'toc_id': 'T-3', 'level': 3, 'kind': 'subsection', 'title': '子节', 'number': '1.1.1'})
    body = build_body(toc={'nodes': nodes}, parse_dir=tmp_path)
    units, exclusions, ambiguities = build_units(body)
    catalog = Catalog(book_id='test', revision='r1', units=units, exclusions=exclusions, ambiguities=tuple(ambiguities))
    _, statuses = read_parts(tmp_path, catalog)
    by_id = {s.unit_key: s for s in statuses}
    assert by_id['CH01-S01'].blocks_freeze is (own_prose != '')
    assert by_id['CH01-S01-SS01'].blocks_freeze is True  # Never exempt actual child content.
    assert by_id['CH01'].blocks_freeze is True  # Chapter introduction remains teaching content.


def test_sandbox_probe_reports_unavailable_when_host_denies_spawn(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from aiforstudy.providers import capabilities as cap
    from aiforstudy.blocks.visual import worker
    from aiforstudy.core.workspace import Workspace
    monkeypatch.setattr(cap, 'resolve_worker_dir', lambda *a: tmp_path)
    monkeypatch.setattr(cap, 'resolve_manim_interpreter', lambda *a: sys.executable)
    monkeypatch.setattr(worker, 'select_isolation', lambda *a: SimpleNamespace(available=True, backend='bwrap'))
    def denied(**kwargs):
        raise PermissionError('host denied worker spawn')
    monkeypatch.setattr(worker, 'probe_isolation', denied)
    result = cap._probe_sandbox('worker', {}, Workspace(root=tmp_path))
    assert result.available is False
    assert 'PermissionError' in result.detail


@pytest.mark.parametrize('missing', [None, 'toc_ref', 'display_anchor', 'source_span', 'overlap'])
def test_same_title_with_independent_source_identities_is_not_merged_or_blocked(missing):
    from dataclasses import replace
    from aiforstudy.core.identity import UnitRef, detect_ambiguities
    a = UnitRef(unit_key='CH01-S01', chapter_key='CH01', content_sha256='a',
                teaching_role='section', title='向量的线性运算', toc_ref='T-0002',
                display_anchor='1.1', source_span=(5, 6))
    b = replace(a, unit_key='CH01-S01-SS02', content_sha256='b', toc_ref='T-0004',
                display_anchor='1.1.2', source_span=(11, 15))
    if missing == 'overlap':
        b = replace(b, source_span=(6, 15))
    elif missing:
        b = replace(b, **{missing: None if missing == 'source_span' else ''})
    findings = detect_ambiguities((a, b))
    assert any(f.kind == 'duplicate-title' for f in findings) == (missing is not None)


def test_authority_line_search_recovers_section_sign_without_relaxing_fuzzy_threshold():
    from blocks.parse.toc.align import _search_line
    assert _search_line(['上一节正文。', '§3.2 行列式的性质', '正文。'],
                        '3.2 行列式的性质', 1, 4) == (1.0, 2, '§3.2 行列式的性质')


@pytest.mark.parametrize('earlier', ['5.6 子空间运算', '子空间运算'])
def test_line_search_prefers_correct_number_over_earlier_same_title(earlier):
    from blocks.parse.toc.align import _search_line
    assert _search_line([earlier, '上一节的尾段。', '§5.7 子空间运算'],
                        '5.7 子空间运算', 1, 4) == (1.0, 3, '§5.7 子空间运算')


def test_anchor_export_keeps_authoritative_title_repeated_as_running_header(tmp_path):
    source = tmp_path / 'document.md'
    source.write_text('\n'.join([
        '[第 1 页]', '第五章 线性空间', '章导言。',
        '§5.7 子空间运算*', '本节讨论子空间运算。',
        '[第 2 页]', '§5.7 子空间运算*',
        '§5.7.1 子空间的交*', '两个子空间的交仍是子空间。',
        '[第 3 页]', '§5.7 子空间运算*', '证明的后续部分。',
    ]))
    decisions = tmp_path / 'toc.decisions.json'
    decisions.write_text(json.dumps({'decisions': [
        {'line': 2, 'level': 1, 'number': '第五章', 'title': '线性空间'},
        {'line': 4, 'level': 2, 'number': '5.7', 'title': '子空间运算*'},
        {'line': 8, 'level': 3, 'number': '5.7.1', 'title': '子空间的交*'},
    ]}))
    subprocess.run([sys.executable, '-m', 'blocks.parse.structure.cli', 'structure',
                    str(source), '--out', str(tmp_path), '--authority', str(decisions)],
                   cwd=Path(__file__).resolve().parents[2], check=True, capture_output=True)
    result = (tmp_path / 'structured.anchor3.md').read_text()
    assert '## §5.7 子空间运算*' in result
    assert '### 5.7.1 子空间的交*' in result
    assert result.index('## §5.7 ') < result.index('### 5.7.1 ')


def test_concept_preparation_records_actual_unit_in_execution_attempts(tmp_path):
    from support.preparation_book import _mini_book, _request
    from aiforstudy.pipeline.engine import build_engine, execute
    from aiforstudy.pipeline.services import Providers
    ws = _mini_book(tmp_path)
    (ws.root / 'book/parse/concepts.parts/CH01-S01.json').unlink()
    def transport(**kwargs):
        return json.dumps({'concepts': [{'name': '甲定义', 'type': 'definition',
                            'formal': '甲', 'source_quote': '甲之一节'}],
                           'named_results': ['甲定义']}), {'total_tokens': 10}
    result, store = execute(build_engine(_request('mini-book'), ws, until='concepts',
                           providers=Providers(llm=transport, is_stub=True)))
    rows = json.loads((store.root/'execution.json').read_text())['attempts']
    calls = [r for r in rows if r['scope']['stage'] == 'concepts']
    assert len(calls) == 1
    assert calls[0]['scope']['unit_id'] == 'CH01-S01'
    assert calls[0]['state'] == 'response_received' and calls[0]['attempt'] == 1
    assert result.status == 'succeeded'
    assert not (store.root/'usage.jsonl').exists()


@pytest.mark.parametrize('deadline', [1500.0, None], ids=['finite', 'unlimited'])
def test_whole_book_concept_time_is_not_one_units_stage_time(tmp_path, deadline):
    from support.preparation_book import _mini_book, _request
    from aiforstudy.pipeline.engine import build_engine, execute
    from aiforstudy.pipeline.services import Providers
    ws = _mini_book(tmp_path)
    policy_path = ws.root / 'config/execution.json'
    policy = json.loads(policy_path.read_text())
    policy['profiles']['release']['api_deadline_seconds'] = deadline
    policy_path.write_text(json.dumps(policy))
    for part in (ws.root / 'book/parse/concepts.parts').glob('*.json'):
        part.unlink()
    clock = [100.0]
    timeouts = []
    def transport(**kwargs):
        timeouts.append(kwargs['timeout'])
        # Each 600s call fits its own 900s lease. The 1200s book exceeds one
        # lease while fitting the separate 1500s run limit (or unlimited run).
        clock[0] += 600
        return json.dumps({'concepts': [], 'named_results': []}), {'total_tokens': 10}
    engine = build_engine(_request('mini-book'), ws, until='concepts',
                          providers=Providers(llm=transport, is_stub=True))
    engine.services.control.clock = lambda: clock[0]
    result, store = execute(engine)
    assert result.status == 'succeeded'
    assert timeouts == [900.0, 900.0]
    attempts = json.loads((store.root/'execution.json').read_text())['attempts']
    assert {r['scope']['unit_id'] for r in attempts} == {'CH01', 'CH01-S01'}
    assert all(r['state'] == 'response_received' for r in attempts)
    assert len(list((engine.services.context.parse / 'concepts.parts').glob('*.json'))) == 2
