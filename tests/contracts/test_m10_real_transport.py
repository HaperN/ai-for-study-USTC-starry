"""Offline real-transport contracts: credentials, image bytes, worker and request shape."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from aiforstudy.errors import ConfigError
from aiforstudy.providers.transport import (SECRETS_REL, RealTransport,
                                            _messages_with_images, resolve_api_key)

MESSAGES = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "看图"}]


def _png(tmp_path: Path, name: str = "page.png", blob: bytes = b"\x89PNG\r\n\x1a\nfake"):
    path = tmp_path / name
    path.write_bytes(blob)
    return path


# ────────────────────────────────────────── 密钥与构造边界

def test_a_real_transport_constructs_without_an_approved_budget():
    transport=RealTransport(model='m',api_key='offline-placeholder')
    assert transport.calls == 0
    assert 'approved_budget_tokens' not in transport.report()


def test_a_real_transport_refuses_without_a_key(monkeypatch, tmp_path):
    """没密钥 → 明确报错，并**说清是哪儿找过了**（环境变量 + 密钥文件）。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ConfigError) as exc:
        RealTransport(model="m", workspace_root=tmp_path)
    assert "DEEPSEEK_API_KEY" in str(exc.value)
    assert SECRETS_REL.as_posix() in str(exc.value), "要写明还找过密钥文件"


def test_the_report_never_contains_the_key(monkeypatch):
    """§5：密钥不回传、不写日志。报告里只允许出现**引用名**。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret-value-123")
    transport = RealTransport(model="m")
    report = json.dumps(transport.report(), ensure_ascii=False)
    assert "sk-secret-value-123" not in report
    assert report.count("env:DEEPSEEK_API_KEY") == 1


def test_the_key_source_matches_the_proven_legacy_order(monkeypatch, tmp_path):
    """密钥来源顺序必须与旧 `lib/model.py` **一致**：环境变量 → 密钥文件。

    第一版只读环境变量，于是会出"密钥就在 `.secrets` 里、新引擎报缺密钥"
    这种最难查的分裂。
    """
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / "deepseek.env").write_text(
        "# 注释\nDEEPSEEK_API_KEY=sk-from-file\n", encoding="utf-8")
    key, ref = resolve_api_key(tmp_path)
    assert key == "sk-from-file"
    assert ref == f"file:{SECRETS_REL.as_posix()}#DEEPSEEK_API_KEY"

    # 环境变量优先
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    key2, ref2 = resolve_api_key(tmp_path)
    assert key2 == "sk-from-env" and ref2 == "env:DEEPSEEK_API_KEY"


# ────────────────────────────────────────── 图片输入

def test_images_are_embedded_as_data_urls(tmp_path):
    """图片要真的进请求体（`content` 变 parts 数组），格式与旧客户端一致。"""
    img = _png(tmp_path)
    out = _messages_with_images(MESSAGES, [str(img)], 128)
    user = [m for m in out if m["role"] == "user"][0]
    assert isinstance(user["content"], list)
    assert user["content"][0] == {"type": "text", "text": "看图"}
    assert user["content"][1]["type"] == "image_url"
    url = user["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == img.read_bytes()


def test_a_missing_image_is_a_hard_error_not_a_silent_drop(tmp_path):
    """缺图**不许静默丢**：那会让审核看到缺图的产物却以为完整。"""
    with pytest.raises(ConfigError, match="图片不存在"):
        _messages_with_images(MESSAGES, [str(tmp_path / "nope.png")], 128)


def test_messages_are_untouched_when_there_are_no_images():
    """无图时**不改动**消息形状 —— 免得给纯文本请求平白塞一个 parts 数组。"""
    out = _messages_with_images(MESSAGES, [], 128)
    assert out[0]["content"] == "sys"
    assert out[1]["content"] == "看图"


def test_images_attach_to_the_last_user_message(tmp_path):
    """附在**最后一条** user 上（一次请求里通常只有一条 user）。"""
    img = _png(tmp_path)
    messages = [{"role": "user", "content": "第一条"},
                {"role": "assistant", "content": "中间"},
                {"role": "user", "content": "第二条"}]
    out = _messages_with_images(messages, [str(img)], 128)
    assert out[0]["content"] == "第一条", "不该动前面的 user"
    assert isinstance(out[2]["content"], list), "图片要附在最后一条 user 上"


def test_images_without_user_message_are_rejected_not_silently_dropped(tmp_path):
    with pytest.raises(ConfigError, match='user'):
        _messages_with_images([{'role': 'system', 'content': 'review'}],
                              [str(_png(tmp_path))], 128)


def test_the_transport_counts_image_calls_separately():
    """图片调用单独计数：目录复核是按**页**计费的，混在一起就看不见它花了多少。"""
    transport = RealTransport(model="m", api_key="sk-x")
    assert transport.report()["image_calls"] == 0
    assert "image_calls" in transport.report()


def test_transport_preserves_effort_and_leaves_retry_to_gateway(monkeypatch):
    import subprocess
    bodies = []
    class Worker:
        returncode = 0
        stdin = stdout = None
        def __init__(self, *a, **kw): pass
        def communicate(self, input=None, timeout=None):
            bodies.append(json.loads(input)['body'])
            return b'{"error":"TimeoutError","message":"controlled timeout"}', None
        def poll(self): return self.returncode
    monkeypatch.setattr(subprocess, 'Popen', Worker)
    from aiforstudy.providers import transport as transport_module
    monkeypatch.setattr(transport_module, 'communicate_bounded',
        lambda process, payload, **kw: process.communicate(input=payload))
    transport = RealTransport(model='m', api_key='test-key', base_url='http://127.0.0.1')
    with pytest.raises(TimeoutError):
        transport(model='m', effort='high', messages=MESSAGES, images=None,
                  max_tokens=128, timeout=1)
    assert len(bodies) == 1, 'HTTP重试必须只由Gateway按持久尝试计数控制'
    assert bodies[0]['thinking'] == {'type': 'enabled'}
    assert bodies[0]['reasoning_effort'] == 'high'


@pytest.mark.parametrize(('effort', 'wire_effort', 'thinking_type'), [
    ('none', 'none', 'disabled'), ('low', 'low', 'enabled'),
    ('high', 'high', 'enabled'), ('max', 'max', 'enabled'),
    ('minimal', 'low', 'enabled'), ('medium', 'high', 'enabled'),
    ('xhigh', 'high', 'enabled'), ('ultra', 'max', 'enabled'),
    ('off', 'none', 'disabled'), ('no', 'none', 'disabled'), ('0', 'none', 'disabled'),
])
def test_request_body_uses_provider_effort_field_and_aliases(effort, wire_effort, thinking_type):
    transport = RealTransport(model='m', api_key='offline-test-key')
    body = transport.request_body(messages=MESSAGES, effort=effort)
    assert body['reasoning_effort'] == wire_effort
    assert body['thinking'] == {'type': thinking_type}
    assert transport.calls == 0


@pytest.mark.parametrize('effort', ['unknown', 'enabled', '', None, 1])
def test_request_body_rejects_invalid_effort_before_dispatch(effort):
    transport = RealTransport(model='m', api_key='offline-test-key')
    with pytest.raises(ConfigError, match='effort'):
        transport.request_body(messages=MESSAGES, effort=effort)
    assert transport.calls == 0


@pytest.mark.parametrize(('effort', 'expected'), [(None, 'high'), ('low', 'low'), ('off', 'none')])
def test_http_worker_sends_top_level_effort_without_network(monkeypatch, effort, expected):
    import io
    from types import SimpleNamespace
    from aiforstudy.providers import http_worker
    transport = RealTransport(model='m', api_key='offline-test-key')
    body = transport.request_body(messages=MESSAGES, **({} if effort is None else {'effort': effort}))
    captured = []

    class OfflineConnection:
        def __init__(self, *args, **kwargs):
            self.sock = SimpleNamespace(settimeout=lambda value: None)

        def connect(self): pass
        def close(self): pass

        def request(self, method, path, *, body, headers):
            captured.append((method, path, json.loads(body)))

        def getresponse(self):
            return SimpleNamespace(status=200, length=None,
                read=lambda limit: b'{"choices":[{"message":{"content":"{}"}}],"usage":{"total_tokens":1}}')

    monkeypatch.setattr(http_worker.http.client, 'HTTPSConnection', OfflineConnection)
    monkeypatch.setattr(http_worker.http.client, 'HTTPConnection', OfflineConnection)
    monkeypatch.setattr(http_worker.sys, 'stdin', io.StringIO(json.dumps({
        'url': 'https://offline.invalid/chat/completions', 'key': 'offline-test-key',
        'body': body, 'connect_timeout': 1, 'read_timeout': 1})))
    output = io.StringIO()
    monkeypatch.setattr(http_worker.sys, 'stdout', output)
    http_worker.main()
    assert json.loads(output.getvalue())['text'] == '{}'
    assert len(captured) == 1 and captured[0][:2] == ('POST', '/chat/completions')
    wire = captured[0][2]
    assert wire['reasoning_effort'] == expected
    assert wire['thinking'] == {'type': 'disabled' if expected == 'none' else 'enabled'}
