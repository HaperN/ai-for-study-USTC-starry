"""修改计划 B1/E1（2026-10-20）：恢复时按失败位置续做；原为 tmp/intent-policy-20261016 下的离线探针。"""
import json
import pytest
from aiforstudy.core.review_mode import review_mode
from aiforstudy.errors import Cancelled
from aiforstudy.blocks.media.code_execution import execute_code, OUTPUT_LIMIT_HINT
from test_balanced_media_review import CONTENT, PROGRAM, REQUEST, ROOT, Maker, Reviewer, verdict
from test_remaining_rules_20261016 import doubt, _truncated

def kwargs(tmp_path, maker, reviewer):
    return dict(content=CONTENT,approval={},out_dir=tmp_path,client=maker,reviewer=reviewer,workspace_root=ROOT)

@pytest.mark.parametrize('responses', [[_truncated(),_truncated()],[_truncated(),PROGRAM]])
def test_length_budget_not_refunded_by_reentry(tmp_path,responses):
    maker, reviewer=Maker(responses),Reviewer([verdict('pass')])
    kw=kwargs(tmp_path,maker,reviewer)
    with review_mode('balanced'):
        row=execute_code(REQUEST,**kw)
        replay=execute_code(REQUEST,previous=row,**kw)
    assert len(maker.calls)==2
    assert len(replay['code_attempts'])==2

def test_doubt_budget_not_refunded_by_reentry(tmp_path):
    maker,reviewer=Maker(),Reviewer([doubt('将图例中的 +φ 改成 −φ')])
    kw=kwargs(tmp_path,maker,reviewer)
    with review_mode('balanced'):
        row=execute_code(REQUEST,**kw)
        replay=execute_code(REQUEST,previous=row,**kw)
    assert len(maker.calls)==2
    assert replay['review_outcome']['status']=='unverified'

class StopBeforeSecondAttempt:
    def __init__(self):self.calls=0
    def checkpoint(self):
        self.calls+=1
        if self.calls>=2:raise Cancelled('offline interruption between code attempts')

class StopBeforeSecondAttemptAfterRender:
    def __init__(self):self.calls=0
    def checkpoint(self):
        self.calls+=1
        if self.calls>=3:raise Cancelled('offline interruption after first media review')

def test_interrupted_length_retry_retains_hint(tmp_path):
    maker,reviewer=Maker([_truncated(),PROGRAM]),Reviewer([verdict('pass')])
    kw=kwargs(tmp_path,maker,reviewer)
    with review_mode('balanced'):
        with pytest.raises(Cancelled):execute_code(REQUEST,cancel=StopBeforeSecondAttempt(),**kw)
        prior=json.loads((tmp_path/'result.json').read_text())
        assert len(prior['code_attempts'])==1 and prior['code_attempts'][0]['output_limit']
        replay=execute_code(REQUEST,previous=prior,**kw)
    assert len(maker.calls)==2
    assert OUTPUT_LIMIT_HINT in maker.calls[-1][-1]['content']

def test_interrupted_doubt_retry_gets_its_reserved_repair(tmp_path):
    maker,reviewer=Maker(),Reviewer([doubt('将图例中的 +φ 改成 −φ'),verdict('pass')])
    kw=kwargs(tmp_path,maker,reviewer)
    with review_mode('balanced'):
        with pytest.raises(Cancelled):execute_code(REQUEST,cancel=StopBeforeSecondAttemptAfterRender(),**kw)
        prior=json.loads((tmp_path/'result.json').read_text())
        assert len(prior['code_attempts'])==1 and prior['code_attempts'][0]['needs_review_repair']
        replay=execute_code(REQUEST,previous=prior,**kw)
    assert len(maker.calls)==2
    assert replay['review_outcome']['status']=='verified'


# ── 修改计划 D6：出图前的技术异常走轻量修复，不新开候选

BROKEN = {'code': PROGRAM['code'] + "        raise TypeError('Arrow got an unexpected keyword argument tips')\n"}


def test_technical_error_is_fixed_without_a_new_candidate(tmp_path):
    maker, reviewer = Maker([BROKEN, PROGRAM]), Reviewer([verdict('pass')])
    with review_mode('balanced'):
        row = execute_code(REQUEST, **kwargs(tmp_path, maker, reviewer))
    assert row['state'] == 'fulfilled'
    assert len(row['code_attempts']) == 1
    assert row['code_attempts'][0]['technical_fixes'] == 1
    fix_request = json.loads(maker.calls[1][-1]['content'])
    assert fix_request['role_task'] == 'media_code_technical_fix'
    assert 'unexpected keyword argument tips' in fix_request['error_tail']
    assert fix_request['original_program']['code'] == BROKEN['code']


def test_technical_fixes_are_capped_at_two_then_use_candidates(tmp_path):
    maker, reviewer = Maker([BROKEN, BROKEN, BROKEN, PROGRAM]), Reviewer([verdict('pass')])
    with review_mode('balanced'):
        row = execute_code(REQUEST, **kwargs(tmp_path, maker, reviewer))
    assert sum(a.get('technical_fixes', 0) for a in row['code_attempts']) == 2
    assert len(row['code_attempts']) == 2 and row['state'] == 'fulfilled'


# ── 修改计划 D5/E1：截图环境失败不重画；恢复时只补截图与审核

from aiforstudy.blocks.media import validation as media_validation  # noqa: E402
from aiforstudy.errors import CapabilityError  # noqa: E402


def _flaky_capture(monkeypatch, fail_calls):
    real, calls = media_validation.capture_display, []

    def capture(*args, **kw):
        calls.append(1)
        if len(calls) in fail_calls:
            raise CapabilityError('target-size media browser failed: SIGTRAP')
        return real(*args, **kw)
    monkeypatch.setattr(media_validation, 'capture_display', capture)
    return calls


def test_capture_failure_blocks_without_redraw_and_resume_only_reviews(tmp_path, monkeypatch):
    _flaky_capture(monkeypatch, {2})          # 1 = 预检试拍成功；2 = 成品截图失败
    maker, reviewer = Maker([PROGRAM]), Reviewer([verdict('pass')])
    kw = kwargs(tmp_path, maker, reviewer)
    with review_mode('balanced'):
        row = execute_code(REQUEST, **kw)
        assert row['state'] == 'blocked' and row['validation']['capture_failed']
        assert len(maker.calls) == 1 and len(row['code_attempts']) == 1 and not reviewer.calls
        resumed = execute_code(REQUEST, previous=row, **kw)
    assert resumed['state'] == 'fulfilled'
    assert len(maker.calls) == 1 and len(resumed['code_attempts']) == 1
    assert resumed['code_attempts'][0]['review_resumed'] and len(reviewer.calls) == 1


def test_capture_preflight_failure_is_recorded_and_media_still_made(tmp_path, monkeypatch):
    """用户 2026-10-20 定放宽：试拍失败只记录，照常制作；恢复时只补截图与审核。"""
    _flaky_capture(monkeypatch, {1})
    maker, reviewer = Maker([PROGRAM]), Reviewer([verdict('pass')])
    with review_mode('balanced'):
        row = execute_code(REQUEST, **kwargs(tmp_path, maker, reviewer))
    assert row['state'] == 'fulfilled' and len(maker.calls) == 1
    assert json.loads((tmp_path / 'preflight.json').read_text())['capture_preflight'].startswith('failed')


# ── 用户 2026-10-20 定放宽：网络断流不让媒体永久停下（真实运行 CH05-S03 的 4 项媒体曾因此全部停住）

from aiforstudy.pipeline.gateway_client import ModelCallFailed  # noqa: E402


def _drop():
    return ModelCallFailed('模型请求没拿到可用响应（ConnectionError: IncompleteRead）', failure_kind='network')


def test_transport_failure_uses_the_next_candidate_instead_of_stopping(tmp_path):
    maker, reviewer = Maker([_drop(), PROGRAM]), Reviewer([verdict('pass')])
    with review_mode('balanced'):
        row = execute_code(REQUEST, **kwargs(tmp_path, maker, reviewer))
    assert row['state'] == 'fulfilled' and len(row['code_attempts']) == 2
    assert not row['code_attempts'][0].get('generation_stopped')


def test_old_transport_stopped_record_is_regenerated_on_resume(tmp_path):
    maker, reviewer = Maker([_drop()]), Reviewer([verdict('pass')])
    kw = kwargs(tmp_path, maker, reviewer)
    with review_mode('balanced'):
        row = execute_code(REQUEST, **kw)
        # 模拟 10-16 运行留下的旧记录：停止标记在、failure_kind 缺失、原因里是 IncompleteRead
        for attempt in row['code_attempts']:
            attempt.update(generation_stopped=True, failure_kind=None)
        row['code_attempts'] = row['code_attempts'][:1]
        (tmp_path / 'result.json').write_text(json.dumps(row, ensure_ascii=False), encoding='utf-8')
        maker.responses = [PROGRAM]
        resumed = execute_code(REQUEST, previous=row, **kw)
    assert resumed['state'] == 'fulfilled'
    assert resumed['code_attempts'][-1].get('resumed_regeneration')
