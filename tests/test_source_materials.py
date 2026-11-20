"""TOC-owned source preservation, without exercise extraction or projections."""
import hashlib
import json
from pathlib import Path

import pytest

from blocks.parse.structure import structure
from blocks.parse.toc import align
from blocks.parse.toc.dual_source import make_authority, normalize_entry


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


@pytest.fixture
def book(tmp_path):
    lines = ['[第 1 页]', '第1章 向量', '1.1 线性运算', '理论正文。', '例1：相加的完整讲解。',
             '习题 1.1', '1. 内嵌习题原文。', '[第 2 页]', '跨页续题，不拆小问。',
             '第2章 综合习题', '独立习题章说明。', '2.1 第一组', '2. 资料题目原文。',
             '[第 3 页]', '第3章 矩阵', '矩阵正文。', '[第 4 页]', '参考答案', '书上已有答案原文。']
    rows = [
        ('T1', '1', '向量', 1, 'teaching', 1),
        ('T2', '1.1', '线性运算', 2, 'teaching', 1),
        ('I1', '', '习题 1.1', 2, 'exercise', 1),
        ('X1', '2', '综合习题', 1, 'exercise', 2),
        ('X2', '2.1', '第一组', 2, 'exercise', 2),
        ('T3', '3', '矩阵', 1, 'teaching', 3),
        ('A1', '', '参考答案', 1, 'answers', 4),
    ]
    entries = [dict(entry_id=eid, number=number, title=title, level=level,
        order=i, category=kind, toc_id=eid if kind == 'teaching' else None,
        pdf_page=page, printed_page=str(page), toc_page=99, reason='目录分类')
        for i, (eid, number, title, level, kind, page) in enumerate(rows, 1)]
    toc = {'nodes': [dict(toc_id=e['toc_id'], number=e['number'], title=e['title'], level=e['level'],
        kind='chapter' if e['level'] == 1 else 'section', page={'pdf':e['pdf_page']}, order=e['order'])
        for e in entries if e['category'] == 'teaching']}
    (tmp_path/'document.md').write_bytes(('\r\n'.join(lines)+'\r\n').encode())
    save(tmp_path/'toc.v2.json', toc)
    save(tmp_path/'toc.excluded.json', {'schema_version':'1.0', 'all_entries':entries,
        'entries':[e for e in entries if e['category'] != 'teaching']})
    save(tmp_path/'headings.json', {'headings': [
        {'line':line, 'text':lines[line-1], 'page':page, 'level':level, 'region':region}
        for line,page,level,region in [(2,1,1,'body'),(3,1,2,'body'),(6,1,2,'exercise'),
            (10,2,1,'exercise'),(12,2,2,'exercise'),(15,3,1,'body'),(18,4,1,'answer')]],
        'page_region':{'1':'exercise','2':'exercise','3':'body','4':'answer'}})
    return tmp_path, toc, lines


def materials_module():
    from aiforstudy.core import source_materials
    return source_materials


@pytest.mark.parametrize('old_index', ['{broken', '{"_meta":{"schema_version":"3.0"}}'])
def test_structure_retains_inline_and_cross_page_exercises_despite_old_projection(book, old_index):
    root, toc, _ = book
    (root/'exercises.json').write_text(old_index)
    (root/'document.selected.md').write_text('过时删题投影')
    (root/'source.projection.json').write_text('过时投影身份')
    raw = (root/'document.md').read_bytes()
    result = structure.build_from_file(root/'document.md', authority=align.heading_decisions(align.align(toc,root)))
    assert '内嵌习题原文' in result.structured_markdown
    assert '跨页续题，不拆小问' in result.structured_markdown
    assert '相加的完整讲解' in result.structured_markdown
    assert '资料题目原文' not in result.structured_markdown
    assert '书上已有答案' not in result.structured_markdown
    assert (root/'document.md').read_bytes() == raw
    assert (root/'document.selected.md').read_text() == '过时删题投影'


def test_old_region_labels_alone_cannot_remove_inline_source(tmp_path):
    (tmp_path/'document.md').write_text('[第 1 页]\n第1章 向量\n例1：讲解。\n习题\n原题完整保留。\n')
    save(tmp_path/'headings.json', {'headings':[{'line':2,'region':'body'},{'line':4,'region':'exercise'}]})
    result=structure.build_from_file(tmp_path/'document.md',authority=[{'line':2,'level':1,'number':'1','title':'向量'}])
    assert '原题完整保留' in result.structured_markdown


@pytest.mark.parametrize('marker', ['习题', '参考答案', 'Exercises'])
def test_repeated_material_headings_keep_their_source_boundaries(marker):
    lines = []
    authority = []
    for page in range(1, 4):
        lines += [f'[第 {page} 页]', '重复书眉', f'第{page}章 正文{page}',
                  f'本章独有的正文{page}', marker, f'{page}. 本章题目原文。']
        authority.append({'line':len(lines)-3, 'level':1, 'number':str(page), 'title':f'正文{page}'})
    result = structure.build_structure('\n'.join(lines), authority=authority)
    assert result.structured_markdown.splitlines().count(marker) == 3
    assert '重复书眉' not in result.structured_markdown
    assert len(result.headings) == 3


def test_excluded_region_pages_only_reports_removed_source():
    text = '[第 1 页]\n前言原文。\n[第 2 页]\n第1章 向量\n教学正文。\n习题\n1. 保留题目。\n'
    result = structure.build_structure(text,
        regions=structure.RegionMap(by_line={2:'front',4:'body',6:'exercise'}, by_page={}),
        authority=[{'line':4,'level':1,'number':'1','title':'向量'}])
    assert '保留题目' in result.structured_markdown
    assert result.stats['excluded_region_lines'] == 1
    assert result.stats['excluded_region_pages'] == [1]


def test_directory_materials_preserve_exact_bytes_order_and_parent_identity(book):
    root, _, lines = book
    before={p.name:p.read_bytes() for p in root.iterdir()}
    result=materials_module().build_source_materials(root)
    assert result['schema_version']=='1.0'
    assert [r['entry_id'] for r in result['directory_entries']]==['T1','T2','I1','X1','X2','T3','A1']
    assert [r['entry_id'] for r in result['materials']]==['X1','X2','A1']
    rows={r['entry_id']:r for r in result['materials']}
    parent=rows['X1']
    assert parent['source_lines']==[10,14]
    assert parent['raw_markdown']=='\r\n'.join(lines[9:14])+'\r\n'
    assert parent['source_sha256']=='sha256:'+hashlib.sha256(parent['raw_markdown'].encode()).hexdigest()
    assert parent['source_pages']==[2]
    assert parent['owner_toc_id'] is None and parent['parent_entry_id'] is None
    assert parent['state']=='recorded'
    assert rows['X2']['parent_entry_id']=='X1'
    assert rows['X2']['source_lines']==[12,14]
    assert rows['A1']['raw_markdown']=='参考答案\r\n书上已有答案原文。\r\n'
    assert {p.name:p.read_bytes() for p in root.iterdir()}==before


def test_missing_material_heading_keeps_directory_and_page_hints_without_previous_owner(book):
    root, _, _ = book
    excluded=json.loads((root/'toc.excluded.json').read_text())
    for collection in ('entries','all_entries'):
        for row in excluded[collection]:
            if row['entry_id']=='X1': row['title']='目录有但原文未定位'
    save(root/'toc.excluded.json',excluded)
    row=next(r for r in materials_module().build_source_materials(root)['materials'] if r['entry_id']=='X1')
    assert row['state']=='unresolved' and row['source_lines'] is None
    assert row['raw_markdown']=='' and row['source_sha256'] is None
    assert row['owner_toc_id'] is None and row['order']==4
    assert row['source_hint']['pdf_page']==2
    assert row['source_hint']['toc_page']==99
    assert row['reason']


def test_unlocated_child_remains_listed_and_parent_preserves_full_source(book):
    root, toc, _=book
    excluded=json.loads((root/'toc.excluded.json').read_text())
    for collection in ('entries','all_entries'):
        for row in excluded[collection]:
            if row['entry_id']=='X2': row['title']='子标题没有唯一锚点'
    save(root/'toc.excluded.json',excluded)
    rows={r['entry_id']:r for r in materials_module().build_source_materials(root)['materials']}
    assert rows['X2']['state']=='unresolved'
    assert rows['X2']['parent_entry_id']=='X1'
    assert rows['X1']['state']=='recorded' and '资料题目原文' in rows['X1']['raw_markdown']
    result=structure.build_from_file(root/'document.md',authority=align.heading_decisions(align.align(toc,root)))
    assert '内嵌习题原文' in result.structured_markdown
    assert '资料题目原文' not in result.structured_markdown


@pytest.mark.parametrize('kind', ['exercise', 'answers'])
@pytest.mark.parametrize('missing', ['start', 'end'])
def test_unresolved_independent_material_cannot_become_previous_chapter_source(book, kind, missing):
    root, toc, _ = book
    excluded=json.loads((root/'toc.excluded.json').read_text())
    for entries in (excluded['entries'], excluded['all_entries']):
        for entry in entries:
            if entry['entry_id']=='X1': entry['category']=kind
            if entry['entry_id']==('X1' if missing=='start' else 'T3'):
                entry['title']='正文没有这个目录标题'
    if missing=='end':
        for node in toc['nodes']:
            if node['toc_id']=='T3': node['title']='正文没有这个目录标题'
        save(root/'toc.v2.json',toc)
    save(root/'toc.excluded.json',excluded)
    raw=(root/'document.md').read_bytes()
    manifest=materials_module().write_source_materials(root)
    unresolved=next(row for row in manifest['materials'] if row['entry_id']=='X1')
    assert unresolved['state']=='unresolved' and unresolved['owner_toc_id'] is None
    decisions=align.heading_decisions(align.align(toc,root))
    with pytest.raises(ValueError,match='独立资料来源定位失败.*X1'):
        structure.build_from_file(root/'document.md',authority=decisions)
    assert (root/'document.md').read_bytes()==raw
    assert materials_module().is_current(root)


@pytest.mark.parametrize('name',['document.md','toc.excluded.json','toc.v2.json','headings.json'])
def test_materials_freshness_tracks_every_source_input(book,name):
    root,_,_=book
    module=materials_module()
    module.write_source_materials(root)
    assert module.is_current(root)
    with (root/name).open('a') as stream: stream.write('\n')
    assert not module.is_current(root)


def test_old_exercise_files_do_not_change_materials_and_tampering_is_rejected(book):
    root,_,_=book
    module=materials_module()
    first=module.write_source_materials(root)
    (root/'exercises.json').write_text('{bad')
    (root/'document.selected.md').write_text('不应进入资料')
    (root/'.exercises-v3').mkdir()
    assert module.is_current(root)
    assert module.build_source_materials(root)==first
    first['materials'][0]['raw_markdown']='伪造原文'
    save(root/'source_materials.json',first)
    assert not module.is_current(root)


def test_directory_authority_records_ancestry_without_assigning_independent_material_to_prior_chapter():
    data=[dict(number=n,title=t,level=l,category=c,printed_page='1',pdf_page=1,toc_page=99,reason='目录')
        for n,t,l,c in [('1','向量',1,'teaching'),('1.1','习题',2,'exercise'),
                       ('2','综合习题',1,'exercise'),('2.1','第一组',2,'exercise'),('3','矩阵',1,'teaching')]]
    rows=[normalize_entry(r,source='V',index=i) for i,r in enumerate(data,1)]
    _,excluded=make_authority(rows,book_title='测试',slug='test',pdf_hash='p',document_hash='d')
    entries=excluded['all_entries']
    assert [r['order'] for r in entries]==[1,2,3,4,5]
    assert entries[1]['owner_toc_id']==entries[0]['toc_id']
    assert entries[1]['parent_entry_id']==entries[0]['entry_id']
    assert entries[2]['owner_toc_id'] is None and entries[2]['parent_entry_id'] is None
    assert entries[3]['owner_toc_id'] is None and entries[3]['parent_entry_id']==entries[2]['entry_id']
