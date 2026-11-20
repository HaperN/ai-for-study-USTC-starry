"""用户 2026-10-20 定的放宽：只有正文显示严格，其他技术检查记警告、不拦运行。"""
from types import SimpleNamespace

from aiforstudy.blocks.concepts.duplicates import resolve_duplicate_ids
from aiforstudy.blocks.concepts.index import ConceptEntry, ConceptIndex
from aiforstudy.pipeline.preparation import _RetaggedClient


def _entry(cid, name, chapter, formal='x'):
    return ConceptEntry(concept_id=cid, name=name, type='definition', ownership_unit=chapter + '-S01',
                        ownership_chapter=chapter, formal=formal)


class Judge:
    def __init__(self, same):
        self.same, self.calls = same, []

    def chat_json(self, messages, *, tag=''):
        self.calls.append(tag)
        if isinstance(self.same, Exception):
            raise self.same
        return {'data': {'same': self.same, 'reason': '依据'}}


def _index():
    return ConceptIndex(book_id='b', concepts=(_entry('C-0001', '解析函数', 'CH02'),
                                               _entry('C-0001', '解析函数', 'CH03'),
                                               _entry('C-0002', '调和函数', 'CH04')))


def test_duplicate_ids_reviewed_and_merged_when_same():
    index, judge = _index(), Judge(True)
    records = resolve_duplicate_ids(index, judge)
    assert judge.calls == ['concept-duplicate:C-0001']
    assert [c.concept_id for c in index.concepts] == ['C-0001', 'C-0002']
    assert records[0]['decision'] == 'merged' and index.verify()['ok']


def test_duplicate_ids_separated_when_different_or_review_fails():
    for judge in (Judge(False), Judge(RuntimeError('网络')), None):
        index = _index()
        records = resolve_duplicate_ids(index, judge)
        assert records[0]['decision'] == 'separated'
        assert sorted(c.concept_id for c in index.concepts) == ['C-0001', 'C-0002', 'C-0003']
        assert index.verify()['ok']


def test_reextraction_uses_new_request_tags():
    seen = []
    base = SimpleNamespace(model='m', effort='high', unit_key='',
                           chat_json=lambda messages, tag='', **kw: seen.append(tag) or {'data': {}})
    client = _RetaggedClient(base, 're1')
    client.chat_json([], tag='concepts-CH01-S01')
    client.unit_key = 'CH01-S02'
    assert seen == ['concepts-CH01-S01:re1'] and base.unit_key == 'CH01-S02' and client.model == 'm'


def test_lesson_only_blocks_units_with_ambiguous_source(tmp_path):
    from aiforstudy.core.jsonio import write_json_atomic
    from aiforstudy.pipeline import content_stages as stage
    products = tmp_path / 'work' / 'products'
    write_json_atomic(products / 'structure' / 'structure.json', {'payload': {'blocked_units': ['CH02-S03']}})
    ctx = SimpleNamespace(node=SimpleNamespace(stage='lesson', scope_key='CH02-S03'),
                          services=SimpleNamespace(cancel=SimpleNamespace(checkpoint=lambda: None)))
    stage_io = stage._t()
    original = stage_io._store_root
    stage_io._store_root = lambda _ctx: tmp_path / 'work'
    try:
        result = stage.lesson_stage(ctx)
        assert result.execution_state.value == 'blocked' and '歧义' in result.error['reason']
        assert stage._structure_blocked_units(SimpleNamespace(node=SimpleNamespace(scope_key='CH02-S04'))) == {'CH02-S03'}
    finally:
        stage_io._store_root = original


def test_text_review_transport_failure_is_recorded_unverified_and_rerun_on_resume(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent / 'integration'))
    from test_balanced_content_pipeline import Client, artifact, context
    from aiforstudy.core.review_mode import review_mode
    from aiforstudy.pipeline import content_stages as stage, stage_io
    from aiforstudy.pipeline.gateway_client import ModelCallFailed
    client = Client([{'body_markdown': '正文可读。'}, ModelCallFailed('IncompleteRead', failure_kind='network')])
    monkeypatch.setattr(stage_io, '_gateway_client', lambda *a, **k: (client, 'offline'))
    with review_mode('balanced'):
        stage.lesson_stage(context(tmp_path))
        result = stage.text_audit_stage(context(tmp_path, 'text_audit'))
    assert result.execution_state.value == 'succeeded' and result.quality_state.value == 'needs_review'
    assert [f['code'] for f in result.findings] == ['REVIEW_INCOMPLETE']
    assert artifact(tmp_path)['review_outcome']['status'] == 'unverified'
    assert artifact(tmp_path)['review_failure']['kind'] == 'network'
