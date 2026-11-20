"""Historical lesson writer positives retired by Q10.

Exact retired-case inventory and current equivalents: Q10-test-migration.md.
Historical files remain readable; current production must never execute them.
"""
import pytest
from legacy_graph_support import assert_read_only_run


@pytest.mark.parametrize('version',['3.0','4.0'])
def test_historical_lesson_writer_is_read_only_and_resume_writes_nothing(tmp_path,version):
    assert_read_only_run(tmp_path,version,stage='lesson')

import socket
import urllib.request
from aiforstudy.errors import BudgetExceeded, CapabilityError, ConfigError
from aiforstudy.core.usage import UsageLedger
from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy
from aiforstudy.providers.gateway import CallRequest, Gateway, replay_transport
from aiforstudy.providers.transport import RealTransport
from support.book_factory import BOOK, UNIT


def test_real_transport_refuses_without_approved_budget_or_key(monkeypatch):
    """真实传输在**构造期**就拒绝：没有批准额度 / 没有密钥，都发不出请求。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def tripwire(*_args, **_kwargs):
        raise AssertionError("构造真实传输时不该碰网络")

    monkeypatch.setattr(socket, "socket", tripwire)
    monkeypatch.setattr(urllib.request, "urlopen", tripwire)

    with pytest.raises(ConfigError):
        RealTransport(model="deepseek-flash")


def test_replay_missing_cache_raises_instead_of_falling_back(tmp_path):
    """回放缺缓存：`CapabilityError` **立即阻断**，并且那一笔也记账（unknown，不填零）。"""
    class Role:
        model, effort, max_tokens = "m1", "high", 1000

    guard=ExecutionControl(ExecutionPolicy(parallelism=1));guard.bind(tmp_path/"execution.json",run_id="run-replay")
    gateway = Gateway(transport=replay_transport({}), roles={"judge": Role()},
                      control=guard, run_id="run-replay", book_id=BOOK)
    with pytest.raises(CapabilityError):
        gateway.call(CallRequest(role="judge", messages=[{"role": "user", "content": "甲"}],
                                 stage="audit", unit_key=UNIT, bucket="audit"))
    rows=guard.snapshot()['attempts']
    assert len(rows)==1 and rows[0]['state']=='blocked'
    assert not (tmp_path/'usage.jsonl').exists()
