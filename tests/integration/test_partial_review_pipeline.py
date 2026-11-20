"""Current valid semantic failures survive sibling protocol errors in Runner."""
import json
from pathlib import Path

import pytest

from aiforstudy.core.jsonio import read_json
from aiforstudy.pipeline.engine import resume_request
from aiforstudy.pipeline.services import Providers
from support.runner_v5 import TeachingTransport, run_book, UNIT


class PartialReviewer(TeachingTransport):
    def __init__(self, known_error):
        super().__init__(reject_first=known_error, refuse_repair=known_error)

    def _answer(self, system, user):
        data = json.loads(user)
        answer = json.loads(super()._answer(system, user))
        if data.get('role_task') == 'reviewer':
            # One unrelated protocol defect must not discard SOURCE-FIDELITY.
            row = next(row for row in answer['verdicts']
                       if not row['criterion_id'].startswith('SOURCE-FIDELITY:'))
            row['evidence_refs'] = [{'evidence_id': 'invalid-sibling-evidence', 'quote': '无效引用'}]
        if data.get('role_task') == 'repair' and self.refuse_repair:
            answer['content']['body_markdown'] = 'n 表示正整数，不含零。' + ' 请按这个条件判断。' * self.patch_calls
        return json.dumps(answer, ensure_ascii=False)


@pytest.mark.parametrize('known_error', [True, False])
def test_partial_review_preserves_known_error_or_explicit_unverified_preview(tmp_path, known_error):
    transport = PartialReviewer(known_error)
    result, store, engine, _ = run_book(tmp_path, transport=transport, until='delivery')
    products = Path(store.work) / 'products'
    audit = read_json(products / 'text_audit' / (UNIT + '.json'))['payload']
    partial = next(report for report in audit['check_reports']
                   if report['schema_version'] == 'PartialReviewReportV1')
    assert not audit['approval'] and partial['complete'] is False
    assert partial['decision'] == 'needs_review'
    assert audit['review_outcome']['status'] == ('known_error' if known_error else 'unverified')
    assert any(ticket['owner'] == 'reviewer' for ticket in audit['tickets'])
    assert any(row['status'] == 'pass' for row in partial['verdicts'])
    assert partial['unresolved_criteria']
    exchanges = sorted((products / 'content' / UNIT / 'review_exchanges').glob('*.json'))
    assert exchanges and all(read_json(path)['schema_version'] == 'ContentReviewExchangeV1'
                             for path in exchanges)
    raw = read_json(exchanges[-1])
    assert raw == partial['exchanges'][-1]
    assert any(ref['evidence_id'] == 'invalid-sibling-evidence'
               for row in raw['response']['verdicts'] for ref in row['evidence_refs'])
    render = read_json(products / 'render/CH01.json')['payload']
    assert render['schema_version'] == 'ReaderProjectionV1'
    assert UNIT in render['rendered_units']
    out = Path(render['out_dir'])
    assert (out / 'index.html').is_file()
    site_data = json.loads((out / 'data/data.js').read_text(encoding='utf-8')
                           .split('window.__DATA__ = ', 1)[1].rsplit(';', 1)[0])
    assert site_data['preview_only'] is False and site_data['production_accepted'] is False
    summary = next(row for row in site_data['problem_summary'] if row['unit_id'] == UNIT)
    assert summary['status'] == ('known_error' if known_error else 'unverified')
    assert summary['reasons']
    current = read_json(products / 'lesson' / (UNIT + '.json'))['payload']['content']
    blocks = site_data['units'][UNIT]['reader_document']['blocks']
    assert next(row['raw_text'] for row in blocks if row['json_pointer'] == '/body_markdown') == current['body_markdown']
    if known_error:
        assert transport.patch_calls == 2, 'changed content shares the current two-round budget'
        assert any(row['status'] == 'fail' for row in partial['verdicts'])
        assert any(ticket['owner'] == 'lesson' for ticket in audit['tickets'])
        # R7：标签只留在运行产物里，读者页不再展示
        assert summary['label'] == '确定错误'
        assert any('恢复含零条件' in reason for reason in summary['reasons'])
        state = store.load_state()['repair_states'][UNIT]
        assert state['content_rounds_by_artifact']['lesson:' + UNIT] == 2
        assert '次数已用尽' in state['stopped_reason']
    else:
        assert transport.patch_calls == 0
        assert not any(row['status'] == 'fail' for row in partial['verdicts'])
        assert summary['label'] == '未核实'
    before = len(transport.calls)
    resume_request(engine.request, store.run_id, engine.services.workspace,
        providers=Providers(llm=transport), until='delivery')
    assert len(transport.calls) == before, 'resume cannot reset existing review/repair limits'
    assert not result.scope_accepted


class UnknownReviewer(TeachingTransport):
    def _answer(self, system, user):
        data=json.loads(user);answer=json.loads(super()._answer(system,user))
        if data.get('role_task')=='reviewer':
            for row in answer['verdicts']:
                if row['criterion_id'].startswith('SOURCE-FIDELITY:'):
                    row.update(status='unknown', reason='来源有争议，当前依据无法核实。', reason_code='source_dispute')
        return json.dumps(answer,ensure_ascii=False)


def test_source_dispute_unknown_gets_bounded_repair_and_stays_unverified(tmp_path):
    transport=UnknownReviewer()
    _,store,_,_=run_book(tmp_path,transport=transport,until='text_audit')
    audit=read_json(Path(store.work)/'products/text_audit'/(UNIT+'.json'))['payload']
    assert audit['review_outcome']['status']=='unverified'
    assert '来源有争议' in ' '.join(audit['review_outcome']['reasons'])
    assert audit['tickets'] and not audit['approval']
    assert 1 <= transport.patch_calls <= 2
    state=store.load_state().get('repair_states',{}).get(UNIT,{})
    assert state['content_rounds_by_artifact']['lesson:'+UNIT] == transport.patch_calls
    assert not state.get('evidence_attempts_by_verdict')
