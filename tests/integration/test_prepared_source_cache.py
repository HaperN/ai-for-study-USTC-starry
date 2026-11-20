"""Imported inputs keep their identity; prepared output mutations invalidate reuse."""
import json

import pytest

from aiforstudy.errors import ContractError
from aiforstudy.pipeline.engine import resume_request
from aiforstudy.pipeline.services import Providers
from support.runner_v5 import UNIT, run_book


def test_prepared_copy_is_not_a_new_import_on_resume(tmp_path):
    result, store, engine, transport = run_book(tmp_path)
    assert result.status == 'succeeded'
    assert engine.services.context.parse == store.work / 'prepared-parse'
    before = len(transport.calls)
    again, _, _ = resume_request(engine.request, store.run_id, engine.services.workspace,
        providers=Providers(llm=transport), until='text_audit')
    assert again.status == 'succeeded', again.recovery_hint
    assert len(transport.calls) == before


def test_changed_original_cannot_resume_the_old_prepared_copy(tmp_path):
    result, store, engine, transport = run_book(tmp_path)
    assert result.status == 'succeeded'
    original = engine.services.registry.resolve(engine.request.book_id).parse
    (original / 'document.md').write_text('changed source', encoding='utf-8')
    before = len(transport.calls)
    with pytest.raises(ContractError, match='原始解析输入已变化'):
        resume_request(engine.request, store.run_id, engine.services.workspace,
            providers=Providers(llm=transport), until='text_audit')
    assert len(transport.calls) == before


def test_changed_prepared_lesson_source_cannot_reuse_old_content(tmp_path):
    result, store, engine, transport = run_book(tmp_path)
    assert result.status == 'succeeded'
    parse = engine.services.context.parse
    chapters = json.loads((parse / 'chapters.json').read_text())
    source = next(u['original_text'] for u in chapters['lesson_units'] if u['unit_id'] == UNIT)
    (parse / source).write_text('原文已经变化，旧正文不能复用。', encoding='utf-8')
    before = len(transport.calls)
    again, resumed, _ = resume_request(engine.request, store.run_id, engine.services.workspace,
        providers=Providers(llm=transport), until='text_audit')
    lesson = resumed.load_state()['nodes']['lesson:' + UNIT]
    assert lesson['execution_state'] != 'cached'
    # A failed regeneration is honest; silently blessing the old candidate is not.
    assert len(transport.calls) > before or again.status != 'succeeded'
