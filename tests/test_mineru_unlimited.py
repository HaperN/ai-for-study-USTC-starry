"""Unlimited MinerU waits still preserve cancellation and parent deadlines."""
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blocks.parse.mineru import fetch as F
from aiforstudy.errors import Cancelled
from aiforstudy.providers import http_exchange


class Clock:
    value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds

    strftime = staticmethod(time.strftime)


def test_unlimited_fetch_survives_long_responses_and_polling(tmp_path, monkeypatch):
    """A null operation limit must not reintroduce HTTP 60/600s or poll 1800s caps."""
    clock = Clock()
    monkeypatch.setattr(F, 'time', clock)
    pdf = tmp_path / 'source.pdf'
    pdf.write_bytes(b'%PDF-offline')
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('layout.json', json.dumps({'pdf_info': [{'page_idx': 0}]}))
        output.writestr('full.md', 'UNLIMITED SOURCE')
    responses = [
        ('POST', '/file-urls/batch', {'code': 0, 'data': {
            'batch_id': 'offline-batch', 'file_urls': ['https://offline.invalid/upload']}}),
        ('PUT', '/upload', b''),
        ('GET', '/extract-results/batch/offline-batch', {'code': 0, 'data': {
            'extract_result': [{'state': 'running', 'extract_progress': {
                'extracted_pages': 0, 'total_pages': 1}}]}}),
        ('GET', '/extract-results/batch/offline-batch', {'code': 0, 'data': {
            'extract_result': [{'state': 'done', 'full_zip_url': 'https://offline.invalid/result'}]}}),
        ('GET', '/result', archive.getvalue()),
    ]
    sent = []

    def exchange(url, *, method='GET', body=None, headers=None, timeout=60, checkpoint=None):
        expected_method, suffix, response = responses[len(sent)]
        assert method == expected_method and url.endswith(suffix)
        assert timeout is None, 'unlimited operation acquired a hidden HTTP response cap'
        if method == 'PUT':
            assert body == b'%PDF-offline' and headers == {}
        clock.value += 1200
        checkpoint()
        sent.append((method, suffix))
        return 200, response if isinstance(response, bytes) else json.dumps(response).encode()

    monkeypatch.setattr(http_exchange, 'exchange', exchange)
    checkpoints = []
    result = F.fetch(pdf, tmp_path / 'out', token='offline-token', page_ranges='1',
                     timeout_s=None, checkpoint=lambda: checkpoints.append(clock.value),
                     log=lambda *_: None)
    assert result['merged']['pages'] == 1
    assert (tmp_path / 'out/mineru/full.md').read_text() == 'UNLIMITED SOURCE\n'
    assert len(sent) == 5 and clock.value >= 6000 and checkpoints


def test_unlimited_child_cannot_override_finite_parent(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(F, 'time', clock)
    with F._control(10):
        with F._control(None):
            clock.value = 11
            with pytest.raises(F.MinerUFetchError, match='deadline'):
                F._remaining()


def test_finite_child_of_unlimited_parent_keeps_its_deadline(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(F, 'time', clock)
    with F._control(None):
        with F._control(10):
            clock.value = 11
            with pytest.raises(F.MinerUFetchError, match='deadline'):
                F._remaining()
        clock.value = 10000
        assert F._remaining() is None


def test_unlimited_poll_obeys_stop_before_next_dispatch(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(F, 'time', clock)
    calls = []

    def checkpoint():
        if calls:
            raise Cancelled('operator stop')

    def response(*args, **kwargs):
        clock.value += 3600
        calls.append('running')
        return {'code': 0, 'data': {'extract_result': [{'state': 'running'}]}}

    monkeypatch.setattr(F, '_api_json', response)
    with F._control(None, checkpoint):
        with pytest.raises(Cancelled, match='operator stop'):
            F.poll('offline-batch', 'offline-token', timeout_s=None, log=lambda *_: None)
    assert calls == ['running']


def test_unlimited_poll_does_not_erase_explicit_finite_parent(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(F, 'time', clock)

    def response(*args, **kwargs):
        clock.value = 11
        return {'code': 0, 'data': {'extract_result': [{'state': 'done'}]}}

    monkeypatch.setattr(F, '_api_json', response)
    with F._control(10):
        with pytest.raises(F.MinerUFetchError, match='deadline'):
            F.poll('offline-batch', 'offline-token', timeout_s=None, log=lambda *_: None)
