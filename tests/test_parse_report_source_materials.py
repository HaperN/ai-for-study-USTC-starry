"""The parser report describes preserved source regions, never extracted questions."""
import json

from test_source_materials import book, save
from aiforstudy.core.source_materials import write_source_materials
from tools.parse_report import main, report


def test_report_reads_current_material_regions_and_ignores_old_question_index(book, capsys):
    root, _, _ = book
    write_source_materials(root)
    save(root / 'chapters.json', {'lesson_units': []})
    save(root / 'exercises.json', {'统计': {'习题': 999, '有答案的题': 998}})
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    result = report(root)
    materials = result['原书资料']
    assert materials == {'状态': 'current', '资料区': 3, '习题区': 2, '答案区': 1,
                         '已记录': 3, '未定位': []}
    assert not {'习题', '答案', '配上答案', '位置分布'} & result.keys()
    main([str(root)])
    output = capsys.readouterr().out
    assert '原书目录资料 3 区' in output and '未定位 0 区' in output
    assert '配上' not in output and '999' not in output
    assert {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()} == before


def test_report_keeps_unresolved_directory_material_visible(book):
    root, _, _ = book
    excluded = json.loads((root / 'toc.excluded.json').read_text())
    for key in ('entries', 'all_entries'):
        for row in excluded[key]:
            if row['entry_id'] == 'X2':
                row['title'] = '目录有但源文无此标题'
    save(root / 'toc.excluded.json', excluded)
    write_source_materials(root)
    materials = report(root)['原书资料']
    assert materials['状态'] == 'current' and materials['已记录'] == 2
    assert len(materials['未定位']) == 1
    assert materials['未定位'][0]['id'] == 'material:X2'
    assert materials['未定位'][0]['reason']


def test_report_marks_missing_material_index_without_using_legacy_counts(tmp_path):
    save(tmp_path / 'exercises.json', {'统计': {'习题': 23}})
    materials = report(tmp_path)['原书资料']
    assert materials['状态'] == 'missing' and materials['资料区'] is None


def test_report_marks_tampered_material_index_stale(book):
    root, _, _ = book
    data = write_source_materials(root)
    data['materials'][0]['raw_markdown'] = '篡改原文'
    save(root / 'source_materials.json', data)
    assert report(root)['原书资料']['状态'] == 'stale'


def test_report_marks_broken_material_index_invalid(tmp_path):
    (tmp_path / 'source_materials.json').write_text('{broken')
    materials = report(tmp_path)['原书资料']
    assert materials['状态'] == 'invalid' and materials['资料区'] is None
