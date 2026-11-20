"""A syntax-corrected full-mode candidate is reused after rule changes."""
import json

from aiforstudy.core.jsonio import read_json
from aiforstudy.pipeline.engine import resume_request
from aiforstudy.pipeline.services import Providers
from support.runner_v5 import run_book, TeachingTransport, UNIT
from test_candidate_revalidation_resume import change_implementation


class SyntaxAuthorTransport(TeachingTransport):
    def __init__(self):
        super().__init__()
        self.syntax_calls = 0

    def _answer(self, system, user):
        payload = json.loads(user)
        if payload.get('role_task') == 'local_generation_correction':
            self.syntax_calls += 1
            assert 'frozen_context' not in payload
            return payload['original_response'][:-1]
        text = super()._answer(system, user)
        return text + '}' if payload.get('role_task') == 'author' else text


def test_corrected_candidate_revalidation_makes_no_new_author_call(tmp_path, monkeypatch):
    result, store, engine, transport = run_book(tmp_path, transport=SyntaxAuthorTransport())
    assert result.status == 'succeeded', result.recovery_hint
    assert transport.author_calls == transport.syntax_calls == 1
    head = store.work / f'products/content/{UNIT}/current.json'
    original = head.read_bytes()
    change_implementation(monkeypatch)
    again, _, _ = resume_request(engine.request, store.run_id, engine.services.workspace,
        providers=Providers(llm=transport), until='text_audit')
    assert again.status == 'succeeded', again.recovery_hint
    assert head.read_bytes() == original
    assert transport.author_calls == transport.syntax_calls == 1
    lesson = read_json(store.work / f'products/lesson/{UNIT}.json')['payload']
    assert lesson['recheck_only'] is True and lesson['model_calls'] == 0
