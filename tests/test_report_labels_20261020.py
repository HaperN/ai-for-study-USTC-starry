"""修改计划 D2/D3/G1（2026-10-20）：疑点三档措辞、媒体 verified 含义、状态分维度与统一失败类别。"""
import pytest

from aiforstudy.blocks.render.readiness import (MEDIA_VERIFIED_SCOPE, failure_category, media_dimensions,
                                                media_doubt_tier, reader_problem_summary)
from aiforstudy.core.review_mode import review_mode


@pytest.mark.parametrize('verdicts, tier', [
    ([{'status': 'fail', 'reason': '方向画反'}], '确定错误'),
    ([{'status': 'needs_review', 'reason': 'dx 区间', 'action': '把下限改为 0'}], '可修疑点'),
    ([{'status': 'needs_review', 'reason': '角标太小看不清'}], '未确认（仅记录）'),
    ([{'status': 'pass', 'reason': '对应'}], ''),
])
def test_media_doubts_have_three_tiers(verdicts, tier):
    assert media_doubt_tier({'validation': {'verdicts': verdicts}}) == tier


def test_media_dimensions_and_unified_failure_names():
    blocked = {'resources': [{'relative_path': 'a.png'}], 'review_outcome': {'status': 'unverified'},
               'validation': {'capture_failed': True, 'review_failure': {'kind': 'capture'}}}
    assert media_dimensions(blocked) == {'generation': 'done', 'review': 'capture_failed', 'mount': 'pending_check',
                                         'playback': 'not_checked', 'failure_category': '截图环境'}
    stopped = {'code_attempts': [{'state': 'failed', 'failure_kind': 'empty_response'}]}
    assert media_dimensions(stopped)['failure_category'] == '空答复'
    assert [failure_category(k) for k in ('network', 'truncation', 'shape', 'interrupted_unknown')] == \
        ['网络', '截断', '形状', '未知中断']
    assert '音画同步' in MEDIA_VERIFIED_SCOPE and '浏览器实播' in MEDIA_VERIFIED_SCOPE


def test_problem_summary_prefixes_media_tier():
    data = {'units': {'U': {'reader_document': {'blocks': []}, 'review_outcome': {'status': 'unverified'},
                            'diagnostic_problems': [{'reason': 'dx 区间不对应', 'tier': '可修疑点'}]}}}
    with review_mode('balanced'):
        rows = reader_problem_summary(data, ['U'])
    assert '【可修疑点】dx 区间不对应' in rows[0]['reasons']


def test_channel_status_counts_active_calls_and_attempt_outcomes(tmp_path):
    """修改计划 E3/E5：各通道在途数、等待原因与尝试统计。"""
    from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy, ExecutionScope
    control = ExecutionControl(ExecutionPolicy(parallelism=4, content_parallelism=2, media_api_parallelism=3))
    control.bind(tmp_path / 'execution.json', run_id='run')
    text = control.acquire_call(ExecutionScope('run', 'U', 'lesson', ''), fingerprint='a')
    media = control.acquire_call(ExecutionScope('run', 'U', 'figures', 'R'), fingerprint='b')
    status = control.channel_status()
    assert status['channels']['content'] == {'limit': 2, 'active': 1, 'waiting_for_channel': 0, 'waiting_for_total': 0}
    assert status['channels']['media_api']['active'] == 1 and status['total'] == {'limit': 4, 'active': 2}
    text.finish('response_received'); media.finish('transport_failed')
    status = control.channel_status()
    assert status['total']['active'] == 0
    assert status['attempts']['replied'] == 1 and status['attempts']['failed'] == {'transport_failed': 1}
