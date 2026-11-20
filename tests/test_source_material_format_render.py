"""Formatting is an optional display projection of unchanged source materials."""
from copy import deepcopy

import pytest

from test_source_materials import book
from aiforstudy.blocks.render.source_materials import project_source_materials
from aiforstudy.core.source_materials import write_source_materials


def reader_data():
    return {'tree': [], 'units': {}, 'counts': {}}


def test_current_formatting_preserves_source_identity_and_packages_both_versions(book):
    root, _, _ = book
    (root / 'images').mkdir()
    (root / 'images/raw.png').write_bytes(b'original pixels')
    (root / 'images/formatted.png').write_bytes(b'formatted pixels')
    raw = (root / 'document.md').read_bytes().replace(
        '资料题目原文。'.encode(), '资料题目原文。 ![](images/raw.png)'.encode())
    (root / 'document.md').write_bytes(raw)
    manifest = write_source_materials(root)
    source = next(row for row in manifest['materials'] if row['id'] == 'material:X1')
    formatted = {'materials': {source['id']: {
        'source_sha256': source['source_sha256'], 'status': 'formatted',
        'display_markdown': '## 综合习题\n\n![](images/formatted.png) ![](../private.png)',
        'notes': [' 第 2 题有无法辨认的符号。 ', '', None],
    }, 'unknown': {'source_sha256': source['source_sha256'], 'status': 'formatted',
                   'display_markdown': '不属于目录的资料'}}}
    before = deepcopy(formatted)
    data = reader_data()

    paths = project_source_materials(data, root, formatted_payload=formatted)

    row = data['source_materials'][source['id']]
    assert {key: row[key] for key in source} == source
    assert row['display_markdown'].startswith('## 综合习题')
    assert row['formatting'] == {'status': 'formatted', 'notes': ['第 2 题有无法辨认的符号。']}
    assert set(paths) == {'images/raw.png', 'images/formatted.png'}
    assert set(data['source_assets']) == set(paths)
    assert 'unknown' not in data['source_materials']
    assert 'review_outcome' not in row
    assert formatted == before
    assert (root / 'document.md').read_bytes() == raw


@pytest.mark.parametrize('replacement', [
    None, [], {'materials': []}, {'materials': {'material:X1': None}},
    {'source_sha256': 'sha256:stale'}, {'status': 'original'},
    {'display_markdown': ''}, {'display_markdown': '   '}, {'display_markdown': []},
])
def test_unavailable_or_stale_formatting_keeps_original_material(book, replacement):
    root, _, _ = book
    manifest = write_source_materials(root)
    source = next(row for row in manifest['materials'] if row['id'] == 'material:X1')
    valid = {'source_sha256': source['source_sha256'], 'status': 'formatted',
             'display_markdown': '不可展示的整理版', 'notes': ['不可展示的旧疑点']}
    formatted = (replacement if not isinstance(replacement, dict) or 'materials' in replacement
                 else {'materials': {source['id']: {**valid, **replacement}}})
    data = reader_data()

    project_source_materials(data, root, formatted_payload=formatted)

    assert data['source_materials'][source['id']] == source
