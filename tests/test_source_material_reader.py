from pathlib import Path
import hashlib

import pytest

def test_standalone_exercise_chapter_keeps_original_order_without_teaching_unit():
    from aiforstudy.blocks.render.source_materials import merge_material_tree
    tree = [dict(id='CH01', title='第一章', unit=True, children=[]),
            dict(id='CH03', title='第三章', unit=True, children=[])]
    entries = [dict(entry_id='a', toc_id='T1', order=1, level=1),
               dict(entry_id='e', order=2, level=1),
               dict(entry_id='b', toc_id='T3', order=3, level=1)]
    materials = [dict(id='material:e', entry_id='e', title='第二章 综合习题',
                      order=2, level=1, kind='exercise', parent_entry_id=None)]
    catalog = [dict(toc_id='T1', node_key='CH01'), dict(toc_id='T3', node_key='CH03')]
    result = merge_material_tree(tree, materials, entries, catalog)
    assert [n['title'] for n in result] == ['第一章', '第二章 综合习题', '第三章']
    assert result[1]['material_id'] == 'material:e'
    assert result[1]['unit'] is False
    assert tree[0].keys() == {'id', 'title', 'unit', 'children'}


def test_material_children_stay_under_their_own_chapter():
    from aiforstudy.blocks.render.source_materials import merge_material_tree
    materials = [dict(id='m2', entry_id='e2', title='题组二', order=3, level=2,
                      kind='exercise', parent_entry_id='e1'),
                 dict(id='m1', entry_id='e1', title='综合习题', order=2, level=1,
                      kind='exercise', parent_entry_id=None)]
    result = merge_material_tree([], materials, [], [])
    assert [n['id'] for n in result] == ['m1']
    assert [n['id'] for n in result[0]['children']] == ['m2']


@pytest.mark.parametrize('selected', [['CH01','CH01-S01','CH01-S01-SS01','CH03'],
                                    ['CH01-S01-SS01','CH01-S01-SS02','CH03']])
def test_extra_source_subsections_stay_with_their_declared_parent_order(selected):
    from aiforstudy.blocks.render.source_materials import merge_material_tree
    tree=[dict(id=key,title=key,unit=True,children=[]) for key in selected]
    entries=[dict(toc_id='T1',order=1),dict(toc_id='T2',order=2),dict(toc_id='T3',order=4)]
    catalog=[dict(toc_id=tid,node_key=key) for tid,key in [('T1','CH01'),('T2','CH01-S01'),('T3','CH03')]]
    materials=[dict(id='material:X',entry_id='X',title='独立习题',order=3,kind='exercise',parent_entry_id=None)]
    result=merge_material_tree(tree,materials,entries,catalog)
    assert [n['id'] for n in result] == [*selected[:-1],'material:X','CH03']


def test_source_asset_export_preserves_local_pixels_and_refuses_escape(tmp_path):
    from aiforstudy.blocks.render.source_materials import source_asset_paths, source_asset_names, copy_source_assets
    root = tmp_path / 'parse'; root.mkdir()
    image = root / 'images' / 'question.png'; image.parent.mkdir()
    # A real 1x1 PNG; copied byte-for-byte without renderer resampling.
    import base64
    blob = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jN1sAAAAASUVORK5CYII=')
    image.write_bytes(blob)
    (tmp_path / 'private.png').write_bytes(blob)
    texts = ['题图 ![](images/question.png) ![](../private.png) ![](https://example.org/a.png)']
    paths = source_asset_paths(texts, root)
    assets = source_asset_names(paths)
    copy_source_assets(paths, assets, tmp_path / 'out')
    assert list(assets) == ['images/question.png']
    target = tmp_path / 'out' / assets['images/question.png']
    assert target.read_bytes() == blob
    assert target.name == hashlib.sha256(blob).hexdigest() + '.png'


def test_recorded_exercise_source_is_visible_but_not_a_media_target():
    from support.content_case import teaching_case
    from aiforstudy.blocks.content.content_contracts import LessonContentV4
    from aiforstudy.blocks.content.reader_document import project_reader
    from aiforstudy.blocks.content.source_context import FrozenReferenceCatalog
    from aiforstudy.blocks.media.media_requirements import visible_fields
    c, _ = teaching_case()
    raw = '习题\n1. 证明命题；[[NOTE:原书字样]] <script>x</script>\n'
    c['exercise_regions'] = [dict(id='ER-1', title='习题', kind='exercise',
        source_lines=[1, 2], raw_markdown=raw,
        source_sha256='sha256:' + hashlib.sha256(raw.encode()).hexdigest())]
    reader = project_reader(LessonContentV4.model_validate(c), FrozenReferenceCatalog(entries=[]))
    block = next(b for b in reader.blocks if b['json_pointer'].endswith('/raw_markdown'))
    assert block['raw_text'] == block['visible_text'] == block['markdown'] == raw
    assert block['marker_spans'] == []
    assert block['source_lines'] == [1, 2]
    assert not any(r['pointer'].startswith('/exercise_regions/') for r in visible_fields(c))
