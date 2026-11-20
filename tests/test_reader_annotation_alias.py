"""Display-only recovery of author markers with an exact existing target."""
from copy import deepcopy

import pytest

from aiforstudy.blocks.content.reader_document import project_reader
from aiforstudy.core.review_mode import review_mode
from test_content_v4_reader_contract import candidate, catalog


@pytest.mark.parametrize('mode', ['full', 'balanced', 'json_only'])
@pytest.mark.parametrize('marker,label', [
    ('[[ANNOTATION:N|展开 $\\bar z_2$]]', r'展开 $\bar z_2$'),
    ('[[ANNOTATION:N]]', '说明·批注'),
])
def test_annotation_alias_links_exact_existing_note_without_changing_content(mode, marker, label):
    content = candidate()
    content['body_markdown'] = '😀说明：' + marker + '。'
    before = deepcopy(content)
    with review_mode(mode):
        doc = project_reader(content, catalog())
    body = next(b for b in doc.blocks if b['json_pointer'] == '/body_markdown')
    note = next(b for b in doc.blocks if b['json_pointer'] == '/annotations/0/markdown')
    assert body['markdown'] == f'😀说明：[{label}](#{note["anchor"]})。'
    assert body['visible_text'] == '😀说明：' + label + '。'
    assert len(body['source_map']) == len(body['visible_text'])
    span = body['marker_spans'][0]
    assert body['raw_text'][span['raw_start']:span['raw_end']] == marker
    assert content == before


@pytest.mark.parametrize('mode', ['full', 'balanced', 'json_only'])
def test_unknown_annotation_alias_stays_literal_without_guessing_target(mode):
    content = candidate()
    content['body_markdown'] = '[[ANNOTATION:missing|不能猜目标]]'
    with review_mode(mode):
        doc = project_reader(content, catalog())
    body = next(b for b in doc.blocks if b['json_pointer'] == '/body_markdown')
    assert body['markdown'] == content['body_markdown']
    assert body['marker_spans'] == []


def test_unknown_annotation_alias_inside_catalog_stays_literal_in_full_mode():
    refs = catalog().as_dict()
    refs['entries'][0]['markdown'] = '说明[[ANNOTATION:missing|不能猜目标]]'
    with review_mode('full'):
        doc = project_reader(candidate(), refs)
    block = next(b for b in doc.blocks if b['json_pointer'].endswith('/0/markdown') and b['group'] == 'references')
    assert block['markdown'] == refs['entries'][0]['markdown']
