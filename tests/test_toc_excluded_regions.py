"""The visual teaching TOC must exclude exercises without losing their source."""
import json
import importlib
from pathlib import Path

import pytest

from blocks.parse.toc import align
from blocks.parse.structure import structure
from aiforstudy.core.source_materials import build_source_materials
export = importlib.import_module('blocks.parse.export.export_parse')


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def entry(entry_id, number, title, level, order, category='teaching'):
    return dict(entry_id=entry_id, number=number, title=title, level=level,
                order=order, category=category, toc_id=entry_id if category == 'teaching' else None,
                printed_page=1, pdf_page=1, toc_page=99, reason='测试分类')


def book(tmp_path, lines, heads, entries):
    (tmp_path / 'document.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    write(tmp_path / 'headings.json', {'headings': [dict(line=n, text=lines[n - 1],
          page=1, level=level, region='body') for n, level in heads], 'page_region': {'1': 'body'}})
    toc = {'nodes': [dict(toc_id=e['toc_id'], number=e['number'], title=e['title'],
                        level=e['level'], kind='chapter' if e['level'] == 1 else 'section',
                        page={'pdf': 1, 'printed': 1}, order=e['order'])
                     for e in entries if e['category'] == 'teaching']}
    write(tmp_path / 'toc.v2.json', toc)
    write(tmp_path / 'toc.excluded.json', {'schema_version': '1.0', 'entries': [
        e for e in entries if e['category'] != 'teaching'], 'all_entries': entries})
    return toc


@pytest.fixture
def chapter(tmp_path):
    lines = ['[第 1 页]', '第1章 向量', '教学导言。', '§1.1 线性运算',
             '例1：两个向量相加。这是教学例题。', '第2章 习题', '1. 求题目甲。',
             '§2.1 练习组', '2. 证明题目乙。', '第3章 矩阵', '下一章正文。']
    entries = [entry('T1', '1', '向量', 1, 1), entry('T2', '1.1', '线性运算', 2, 2),
               entry('X1', '2', '习题', 1, 3, 'exercise'), entry('T3', '3', '矩阵', 1, 4)]
    toc = book(tmp_path, lines, [(2, 1), (4, 2), (6, 1), (8, 2), (10, 1)], entries)
    return tmp_path, toc, lines


def test_whole_exercise_chapter_is_not_reintroduced_by_numbered_extras(chapter):
    root, toc, _ = chapter
    decisions = align.heading_decisions(align.align(toc, root))
    assert [d['line'] for d in decisions] == [2, 4, 10]


def test_structure_filters_entire_exercise_chapter_but_preserves_examples(chapter):
    root, toc, _ = chapter
    decisions = align.heading_decisions(align.align(toc, root))
    result = structure.build_from_file(root / 'document.md', authority=decisions)
    assert '题目甲' not in result.structured_markdown
    assert '题目乙' not in result.structured_markdown
    assert '教学例题' in result.structured_markdown
    assert '下一章正文' in result.structured_markdown




def test_same_page_section_exercise_remains_before_next_teaching_heading(tmp_path):
    lines = ['[第 1 页]', '第1章 向量', '§1.1 线性运算', '教学内容。',
             '习题 1.1', '1. 第一题。', '§1.2 坐标', '坐标正文。']
    toc = book(tmp_path, lines, [(2, 1), (3, 2), (5, 2), (7, 2)], [
        entry('T1', '1', '向量', 1, 1), entry('T2', '1.1', '线性运算', 2, 2),
        entry('X1', '', '习题 1.1', 2, 3, 'exercise'), entry('T3', '1.2', '坐标', 2, 4)])
    decisions = align.heading_decisions(align.align(toc, tmp_path))
    result = structure.build_from_file(tmp_path / 'document.md', authority=decisions)
    assert '第一题' in result.structured_markdown
    assert '坐标正文' in result.structured_markdown




def test_missing_exercise_anchor_cannot_be_silently_accepted(chapter):
    root, toc, _ = chapter
    sidecar = json.loads((root / 'toc.excluded.json').read_text())
    for collection in ('entries', 'all_entries'):
        for row in sidecar[collection]:
            if row['entry_id'] == 'X1':
                row['title'] = '无法定位的专题训练'
    write(root / 'toc.excluded.json', sidecar)
    assert align.align(toc, root)['excluded_regions']['unresolved']


def test_export_guard_excludes_exercise_chapters_and_subsections():
    def node(node_id, kind, title):
        return export.Node(node_id, kind, None, title, 1,
                           blocks=[{'raw_markdown': '# ' + title + '\n正文。'}])
    practice = node('CH02', 'chapter', '综合习题')
    lesson = node('CH01', 'chapter', '典型例题与方法')
    section = node('CH01-S01', 'section', '习题课')
    section.children = [node('CH01-S01-SS01', 'subsection', '习题'),
                        node('CH01-S01-SS02', 'subsection', '解题方法')]
    lesson.children = [section]
    assert [u['title'] for u in export.build_lesson_units([lesson, practice])] == [
        '典型例题与方法', '习题课', '解题方法']


def test_toc_page_text_cannot_be_used_as_exercise_body_anchor(tmp_path):
    lines = ['[第 1 页]', '目录', '综合练习', '[第 2 页]', '第1章 向量', '正文。']
    toc = book(tmp_path, lines, [(2, 1), (3, 1), (5, 1)], [
        entry('X1', '', '综合练习', 1, 1, 'exercise'), entry('T1', '1', '向量', 1, 2)])
    sidecar = json.loads((tmp_path / 'toc.excluded.json').read_text())
    for row in sidecar['entries'] + sidecar['all_entries']:
        row['pdf_page'] = None
    write(tmp_path / 'toc.excluded.json', sidecar)
    write(tmp_path / 'headings.json', {'headings': [
        {'line': 2, 'text': '目录', 'page': 1, 'region': 'toc'},
        {'line': 3, 'text': '综合练习', 'page': 1, 'region': 'toc'},
        {'line': 5, 'text': '第1章 向量', 'page': 2, 'region': 'body'}],
        'page_region': {'1': 'toc', '2': 'body'}})
    assert align.align(toc, tmp_path)['excluded_regions']['unresolved']


def test_missing_child_heading_is_covered_by_confirmed_exercise_parent(chapter):
    root, toc, _ = chapter
    sidecar = json.loads((root / 'toc.excluded.json').read_text())
    sidecar['all_entries'][-1]['order'] = 5
    child = entry('X2', '2.2', '综合探究', 2, 4, 'exercise')
    sidecar['all_entries'].insert(-1, child)
    sidecar['entries'].append(child)
    write(root / 'toc.excluded.json', sidecar)
    decisions = align.heading_decisions(align.align(toc, root))
    assert [d['line'] for d in decisions] == [2, 4, 10]










@pytest.mark.parametrize('old_region', ['toc', 'front', 'exercise', 'answer', 'back'])
@pytest.mark.parametrize('body_is_mineru_title', [True, False])
def test_non_teaching_copy_cannot_steal_canonical_chapter_anchor(
        tmp_path, old_region, body_is_mineru_title):
    lines = ['[第 1 页]', '目录', '第一章 向量', '[第 2 页]', '第一章 向量', '真正文。']
    toc = book(tmp_path, lines, [], [entry('T1', '1', '向量', 1, 1)])
    toc['nodes'][0]['page']['pdf'] = None
    write(tmp_path / 'toc.v2.json', toc)
    heads = [{'line': 3, 'text': '第一章 向量', 'page': 1, 'region': old_region},
             {'line': 4, 'text': '', 'page': 2, 'region': 'body'}]
    if body_is_mineru_title:
        heads.append({'line': 5, 'text': '第一章 向量', 'page': 2, 'region': 'body'})
    write(tmp_path / 'headings.json', {'headings': heads,
          'page_region': {'1': old_region, '2': 'body'}})
    result = align.align(toc, tmp_path)
    assert result['matches'][0]['line'] == 5
    assert result['matches'][0]['number'] == '1'


def test_teaching_authority_restores_misclassified_examples_until_real_exercises(tmp_path):
    lines = ['[第 1 页]', '第一章 典型例题与方法', '1. 先分析例题，再讲解思路。',
             '习题', '1. 真正练习题。', '第二章 向量', '向量正文。']
    toc = book(tmp_path, lines, [], [entry('T1', '1', '典型例题与方法', 1, 1),
          entry('T2', '2', '向量', 1, 2)])
    for node in toc['nodes']:
        node['page']['pdf'] = None
    write(tmp_path / 'toc.v2.json', toc)
    write(tmp_path / 'headings.json', {'headings': [
        {'line': 2, 'text': lines[1], 'page': 1, 'region': 'exercise'},
        {'line': 4, 'text': '习题', 'page': 1, 'region': 'exercise'},
        {'line': 6, 'text': lines[5], 'page': 1, 'region': 'body'}],
        'page_region': {'1': 'exercise'}})
    decisions = align.heading_decisions(align.align(toc, tmp_path))
    result = structure.build_from_file(tmp_path / 'document.md', authority=decisions)
    assert '先分析例题，再讲解思路' in result.structured_markdown
    assert '真正练习题' in result.structured_markdown


def test_exclusion_matching_preserves_superscript_math_semantics(tmp_path):
    lines = ['[第 1 页]', '第1章 向量', '正文。', '训练 x2', '1. 题目。']
    toc = book(tmp_path, lines, [(2, 1), (4, 1)], [entry('T1', '1', '向量', 1, 1),
        entry('X1', '', '训练 x²', 1, 2, 'exercise')])
    assert align.align(toc, tmp_path)['excluded_regions']['unresolved']


def test_same_named_sections_are_matched_inside_their_own_chapters(tmp_path):
    lines = ['[第 1 页]', '第1章 向量', '总结与应用方法', '向量小结正文。',
             '第2章 矩阵', '总结与应用', '矩阵小结正文。']
    toc = book(tmp_path, lines, [(2, 1), (3, 2), (5, 1), (6, 2)], [
        entry('T1', '1', '向量', 1, 1), entry('T2', '', '总结与应用', 2, 2),
        entry('T3', '2', '矩阵', 1, 3), entry('T4', '', '总结与应用', 2, 4)])
    for row in toc['nodes']:
        row['page']['pdf'] = None
    write(tmp_path / 'toc.v2.json', toc)
    result = align.align(toc, tmp_path)
    assert {m['toc_id']: m['line'] for m in result['matches']} == {'T1': 2, 'T2': 3, 'T3': 5, 'T4': 6}


def test_same_numberless_titles_do_not_cross_inside_one_chapter(tmp_path):
    lines = ['[第 1 页]', '第1章 向量', '总结与应用方法', '第一节。', '总结与应用', '第二节。']
    toc = book(tmp_path, lines, [(2, 1), (3, 2), (5, 2)], [
        entry('T1', '1', '向量', 1, 1), entry('T2', '', '总结与应用', 2, 2),
        entry('T3', '', '总结与应用', 2, 3)])
    for row in toc['nodes']:
        row['page']['pdf'] = None
    write(tmp_path / 'toc.v2.json', toc)
    result = align.align(toc, tmp_path)
    assert {m['toc_id']: m['line'] for m in result['matches']} == {'T1': 2, 'T2': 3, 'T3': 5}


def test_exclusion_resolution_ignores_typography_candidates_without_lines(chapter, monkeypatch):
    root, toc, _ = chapter
    original = align.load_candidates
    monkeypatch.setattr(align, 'load_candidates', lambda parse: original(parse) + [{
        'line': None, 'text': '第2章 习题', 'page': 1, 'region': 'body', 'from': 'typography'}])
    result = align.align(toc, root)
    assert result['excluded_regions']['spans'][0]['source_lines'] == [6, 9]


@pytest.mark.parametrize('wanted,actual', [
    ('向量表达式 x+y', '向量表达式 x-y'), ('幂函数 x²', '幂函数 x2'),
    ('幂函数 x²', '幂函数 x³'), ('向量表达式 x+y', '向量表达式 x+z'),
    (r'运算 $x\times y$', r'运算 $x\div y$'),
    ('向量表达式 x(y+z)', '向量表达式 xy+z'),
    ('向量表达式 |x+y|', '向量表达式 x+y'),
    ('幂函数与指数运算 x^{a+b}', '幂函数与指数运算 x^a+b')])
@pytest.mark.parametrize('has_heading', [True, False])
def test_math_title_mismatch_is_not_hidden_by_exact_or_fuzzy_matching(tmp_path, wanted, actual, has_heading):
    lines = ['[第 1 页]', '第1章 向量', '§1.1 ' + actual, '讲解。']
    toc = book(tmp_path, lines, [(2, 1)] + ([(3, 2)] if has_heading else []), [
        entry('T1', '1', '向量', 1, 1), entry('T2', '1.1', wanted, 2, 2)])
    result = align.align(toc, tmp_path)
    assert [m['toc_id'] for m in result['matches']] == ['T1']
    assert result['unmatched_authority'][0]['toc_id'] == 'T2'


def test_region_recovery_cannot_take_a_section_from_next_chapters_answers(tmp_path):
    lines = ['[第 1 页]', '第1章 向量', '正文。', '第2章 矩阵', '参考答案', '1.1 向量运算']
    toc = book(tmp_path, lines, [(2, 1), (4, 1), (6, 2)], [
        entry('T1', '1', '向量', 1, 1), entry('T2', '1.1', '向量运算', 2, 2),
        entry('T3', '2', '矩阵', 1, 3)])
    heads = json.loads((tmp_path / 'headings.json').read_text())
    heads['headings'][-1]['region'] = 'answer'
    write(tmp_path / 'headings.json', heads)
    result = align.align(toc, tmp_path)
    assert {m['toc_id']: m['line'] for m in result['matches']} == {'T1': 2, 'T3': 4}
    assert result['unmatched_authority'][0]['toc_id'] == 'T2'


def test_unlisted_exercise_marker_missed_by_mineru_stays_in_its_chapter(tmp_path):
    lines = ['[第 1 页]', '第1章 向量', '这是习题课的讲解，部分留作习题。',
             '习题二', '习题二', '1. 第一题。', '第2章 矩阵', '下一章正文。']
    toc = book(tmp_path, lines, [(2, 1), (7, 1)], [
        entry('T1', '1', '向量', 1, 1), entry('T2', '2', '矩阵', 1, 2)])
    result = structure.build_from_file(tmp_path / 'document.md',
              authority=align.heading_decisions(align.align(toc, tmp_path)))
    assert '第一题' in result.structured_markdown
    assert '这是习题课的讲解，部分留作习题。' in result.structured_markdown
    assert '下一章正文。' in result.structured_markdown


@pytest.mark.parametrize('old_region,title', [('front', '绪论 电磁学的建立和应用'), ('back', '补充数学知识')])
def test_exact_visual_teaching_chapter_can_restore_misclassified_front_or_back(tmp_path, old_region, title):
    lines = ['[第 1 页]', title, '本章完整教学内容。', '[第 2 页]', '第1章 静电场', '静电场正文。']
    toc = book(tmp_path, lines, [], [entry('T1', '', title, 1, 1), entry('T2', '1', '静电场', 1, 2)])
    for node in toc['nodes']:
        node['page']['pdf'] = None
    write(tmp_path / 'toc.v2.json', toc)
    write(tmp_path / 'headings.json', {'headings': [
        {'line': 2, 'text': title, 'page': 1, 'region': old_region},
        {'line': 5, 'text': lines[4], 'page': 2, 'region': 'body'}],
        'page_region': {'1': old_region, '2': 'body'}})
    result = align.align(toc, tmp_path)
    assert {m['toc_id']: m['line'] for m in result['matches']} == {'T1': 2, 'T2': 5}
    text = structure.build_from_file(tmp_path / 'document.md',
                                    authority=align.heading_decisions(result)).structured_markdown
    assert '本章完整教学内容' in text


def test_recovery_never_uses_a_confirmed_directory_image_page(tmp_path):
    lines = ['[第 1 页]', '绪论 电磁学的建立和应用', '[第 2 页]', '第1章 静电场', '正文。']
    entries = [entry('T1', '', lines[1], 1, 1), entry('T2', '1', '静电场', 1, 2)]
    for e in entries:
        e['toc_page'] = 1
    toc = book(tmp_path, lines, [], entries)
    for node in toc['nodes']:
        node['page']['pdf'] = None
    write(tmp_path / 'toc.v2.json', toc)
    write(tmp_path / 'headings.json', {'headings': [
        {'line': 2, 'text': lines[1], 'page': 1, 'region': 'front'},
        {'line': 4, 'text': lines[3], 'page': 2, 'region': 'body'}],
        'page_region': {'1': 'front', '2': 'body'}})
    result = align.align(toc, tmp_path)
    assert [m['toc_id'] for m in result['matches']] == ['T2']


def test_single_exponent_tex_braces_match_unicode_superscript(tmp_path):
    lines = ['[第 1 页]', '第1章 向量', '§1.1 幂函数与指数运算 $x^{2}$', '讲解。']
    toc = book(tmp_path, lines, [(2, 1), (3, 2)], [entry('T1', '1', '向量', 1, 1),
        entry('T2', '1.1', '幂函数与指数运算 x²', 2, 2)])
    assert {m['toc_id']: m['line'] for m in align.align(toc, tmp_path)['matches']} == {'T1': 2, 'T2': 3}


def test_excluded_chapter_without_model_is_preserved_as_source_material(chapter):
    root, _, _ = chapter
    before=(root/'document.md').read_bytes()
    result=build_source_materials(root)
    assert result['schema_version']=='1.0'
    assert result['materials'][0]['state']=='recorded'
    assert result['materials'][0]['owner_toc_id'] is None
    assert '题目甲' in result['materials'][0]['raw_markdown']
    assert (root/'document.md').read_bytes()==before
