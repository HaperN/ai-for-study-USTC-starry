import json
from types import SimpleNamespace


def test_cross_unit_concepts_keep_definitions_without_becoming_current_obligations(tmp_path, monkeypatch):
    from aiforstudy.pipeline import content_stages as stages
    from aiforstudy.blocks.content.source_context import source_context_from_frozen
    (tmp_path/'unit.md').write_text('本节记号 n 表示非负整数。')
    unit = {'unit_id': 'U2', 'original_text': 'unit.md'}
    (tmp_path/'chapters.json').write_text(json.dumps({'lesson_units':[unit]}))
    (tmp_path/'concepts.json').write_text(json.dumps({'concepts':[
        {'id':'C1','name':'旧定义','formal':'整数包含零。','occurrences':[{'unit_id':'U1','role':'introduced'}]},
        {'id':'C2','name':'当前记号','formal':'n 表示非负整数','occurrences':[{'unit_id':'U2','role':'introduced'}]},
    ]}, ensure_ascii=False))
    monkeypatch.setattr(stages._t(), '_unit_definition', lambda *_args:unit)
    ctx=SimpleNamespace(node=SimpleNamespace(scope_key='U2'),context=SimpleNamespace(parse=tmp_path,book_id='B'))
    frozen=stages._collect_source(ctx)
    assert [row['id'] for row in frozen['required_concepts']] == ['C2']
    source=source_context_from_frozen(frozen)
    old=next(row for row in source.reference_catalog.entries if row.id=='C1')
    assert old.markdown == '整数包含零。'
    assert old.unit_id == 'U1'
    assert 'C1' not in {row.concept_id for row in source.source_obligations}
