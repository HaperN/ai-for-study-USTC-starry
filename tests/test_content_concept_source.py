import json

from aiforstudy.blocks.content.source_context import source_context_from_frozen
from aiforstudy.pipeline import content_stages as stages
from test_content_generation_v3 import context, scripted_client


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def test_author_uses_current_run_concept_identities_and_explicit_ownership(tmp_path):
    ctx = context(tmp_path)
    old = ctx.context.parse / 'concepts.json'
    _write(old, {'concepts': [{'id': 'C-OLD', 'name': '定理1', 'formal': '错误合并的旧内容'}]})
    product = tmp_path / 'work/products/concepts/concepts.json'
    _write(product, {'payload': {'concepts': [
        {'id': 'C-A', 'name': '定理1', 'type': 'theorem', 'ownership_unit': 'U',
         'formal': '本节条件', 'occurrences': [{'unit_key': 'OTHER', 'role': 'introduced'}]},
        {'id': 'C-B', 'name': '定理1', 'type': 'theorem', 'ownership_unit': 'CH02-S01',
         'formal': '另一章的结论', 'occurrences': [{'unit_key': 'CH02-S01', 'role': 'introduced'}]},
    ]}})
    frozen = stages._collect_source(ctx)
    source = source_context_from_frozen(frozen)
    entries = {entry.id: entry for entry in source.reference_catalog.entries if entry.kind == 'CONCEPT'}
    assert set(entries) == {'C-A', 'C-B'}
    assert (entries['C-A'].unit_id, entries['C-A'].markdown) == ('U', '本节条件')
    assert (entries['C-B'].unit_id, entries['C-B'].markdown) == ('CH02-S01', '另一章的结论')
    assert [row['id'] for row in frozen['required_concepts']] == ['C-A']
    assert {row.concept_id for row in source.source_obligations if row.concept_id} == {'C-A'}
    before = source.digest
    _write(old, {'concepts': [{'id': 'DIFFERENT-OLD', 'name': '仍是旧内容'}]})
    assert source_context_from_frozen(stages._collect_source(ctx)).digest == before
    _write(product, {'payload': {'concepts': []}})
    assert source_context_from_frozen(stages._collect_source(ctx)).digest != before


def test_old_parse_concepts_keep_unit_id_and_new_unit_key_occurrences(tmp_path):
    ctx = context(tmp_path)
    _write(ctx.context.parse / 'concepts.json', {'concepts': [
        {'id': 'C-A', 'name': '旧条目', 'formal': '本节条件',
         'occurrences': [{'unit_id': 'U', 'role': 'introduced'}]},
        {'id': 'C-B', 'name': '新版出现位置', 'formal': '别节定义',
         'occurrences': [{'unit_key': 'CH02-S01', 'role': 'introduced'}]},
    ]})
    frozen = stages._collect_source(ctx)
    assert {row['id']: row['first_unit'] for row in frozen['concept_index']['concepts']} == {
        'C-A': 'U', 'C-B': 'CH02-S01'}
    assert frozen['source_inputs']['concepts.json']


def test_lesson_records_selected_product_instead_of_unused_parse_index(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    old = ctx.context.parse / 'concepts.json'
    _write(old, {'concepts': []})
    product = tmp_path / 'work/products/concepts/concepts.json'
    _write(product, {'payload': {'concepts': []}})
    monkeypatch.setattr(stages._t(), '_gateway_client', lambda *_a, **_k: (scripted_client(), 'offline'))
    result = stages.lesson_stage(ctx)
    assert result.execution_state.value == 'succeeded', result.error
    assert str(product) in result.input_files
    assert str(old) not in result.input_files
    frozen = stages._frozen_context(ctx)
    assert str(product) in frozen['source_inputs']
    assert 'concepts.json' not in frozen['source_inputs']
