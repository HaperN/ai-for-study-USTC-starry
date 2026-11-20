"""Reader navigation reuses textbook structure without selecting more lessons."""
from copy import deepcopy

import pytest

from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
from aiforstudy.blocks.render.snapshot_sources import validate_snapshot_sources
from aiforstudy.core.digest import digest_file
from aiforstudy.core.jsonio import write_json_atomic
from aiforstudy.errors import ContractError
from test_diagnostic_preview_boundary import run_fixture
from test_diagnostic_animation_preview import data_at


def rows(tree):
    return [node for item in tree for node in [item, *rows(item.get('children', []))]]


def test_partial_snapshot_restores_real_ancestor_titles_and_tracks_structure_bytes(tmp_path):
    run = run_fixture(tmp_path)
    structure = run/'work/products/structure/structure.json'
    payload = {'units': [
        {'unit_key': 'C', 'title': '原教材章名', 'display_anchor': '4', 'level': 'chapter'},
        {'unit_key': 'S', 'title': '原教材节名', 'display_anchor': '4.2', 'level': 'section', 'parent_unit_key': 'C'},
        {'unit_key': 'U', 'title': '原教材小节名', 'display_anchor': '4.2.1', 'level': 'subsection', 'parent_unit_key': 'S'},
        {'unit_key': 'V', 'title': '尚未生成的小节', 'display_anchor': '4.2.2', 'level': 'subsection', 'parent_unit_key': 'S'},
        {'unit_key': 'W', 'title': '没有选中的小节', 'display_anchor': '4.2.3', 'level': 'subsection', 'parent_unit_key': 'S'},
    ]}
    write_json_atomic(structure, {'payload': payload})
    report = build_reader_snapshot(run, unit_ids=['U', 'V'], out_dir=tmp_path/'reader', require_complete_units=True)
    data = data_at(report)
    assert report['rendered_units'] == ['U']
    assert [(r['id'], r['title'], r['unit']) for r in rows(data['tree'])] == [
        ('C', '原教材章名', False), ('S', '原教材节名', False), ('U', '原教材小节名', True)]
    assert [r['anchor'] for r in rows(data['tree'])] == ['4', '4.2', '4.2.1']
    assert set(data['units']) == {'U'}
    assert report['source_hashes']['work/products/structure/structure.json'] == digest_file(structure)
    assert validate_snapshot_sources(report, run, run/'work/prepared-parse')
    payload['units'][1]['title'] = '修正后的教材节名'
    write_json_atomic(structure, {'payload': payload})
    with pytest.raises(ContractError, match='reader source changed'):
        validate_snapshot_sources(report, run, run/'work/prepared-parse')
    updated = build_reader_snapshot(run, unit_ids=['U', 'V'], out_dir=tmp_path/'reader', require_complete_units=True)
    assert updated['snapshot_hash'] != report['snapshot_hash']
    assert data_at(updated)['tree'][0]['children'][0]['title'] == '修正后的教材节名'


def test_missing_structure_preserves_the_existing_flat_navigation(tmp_path):
    run = run_fixture(tmp_path)
    report = build_reader_snapshot(run, unit_ids=['U'], out_dir=tmp_path/'reader', require_complete_units=True)
    data = data_at(report)
    assert data['tree'] == [{'id': 'U', 'title': data['units']['U']['title'], 'unit': True, 'children': []}]
    assert report['source_hashes']['work/products/structure/structure.json'] == 'missing'


def test_multiple_chapters_keep_only_selected_lessons_and_their_ancestors():
    from aiforstudy.blocks.render.diagnostics import _reader_structure_tree
    tree = [{'id': key, 'title': key, 'unit': True, 'children': []}
            for key in ['CH04', 'CH04-S02-SS02', 'CH06-S01-SS01']]
    structure = {'units': [
        {'unit_key': 'CH04', 'title': '第四章原名', 'level': 'chapter', 'display_anchor': '4'},
        {'unit_key': 'CH04-S02', 'title': '第二节原名', 'level': 'section', 'display_anchor': '4.2', 'parent_unit_key': 'CH04'},
        {'unit_key': 'CH04-S02-SS01', 'title': '未选中', 'parent_unit_key': 'CH04-S02'},
        {'unit_key': 'CH04-S02-SS02', 'title': '选中正文', 'parent_unit_key': 'CH04-S02'},
        {'unit_key': 'CH05', 'title': '没有选中的章'},
        {'unit_key': 'CH06', 'title': '第六章原名', 'level': 'chapter', 'display_anchor': '6'},
        {'unit_key': 'CH06-S01', 'title': '第一节原名', 'parent_unit_key': 'CH06'},
        {'unit_key': 'CH06-S01-SS01', 'title': '另一章的正文', 'parent_unit_key': 'CH06-S01'},
    ]}
    original = deepcopy((tree, structure))
    result = _reader_structure_tree(tree, structure)
    assert [r['id'] for r in result] == ['CH04', 'CH06']
    assert [(r['id'], r['unit']) for r in rows(result)] == [
        ('CH04', True), ('CH04-S02', False), ('CH04-S02-SS02', True),
        ('CH06', False), ('CH06-S01', False), ('CH06-S01-SS01', True)]
    assert (tree, structure) == original
