"""A body adapter repair may replay proven directory responses, never invent approval."""
import json

import pytest

from test_dual_toc import fixture, Client


def test_body_only_change_replays_verified_evidence_without_provider(tmp_path, monkeypatch):
    from blocks.parse.toc.replay import capture, replay
    d, pdf = fixture(tmp_path, monkeypatch)
    assert d.generate_directory(tmp_path, pdf, Client(change=True))['status'] == 'accepted'
    evidence = capture(tmp_path, pdf)
    (tmp_path / 'document.md').write_text('第一章 向量\n已修复的习题正文\n')
    result = replay(tmp_path, pdf, evidence)
    assert result['status'] == 'accepted'
    assert d.is_current(tmp_path, pdf)
    assert all(c['cached'] and c['usage']['total_tokens'] == 0 for c in result['calls'])
    assert result['local_replay']['reason'] == 'body-adaptation-only'
    assert json.loads((tmp_path / 'toc.v2.json').read_text())['nodes'][0]['title'] == '空间'


@pytest.mark.parametrize('changed', ['pdf', 'toc.json', 'toc.comparison.json', 'image'])
def test_changed_directory_evidence_cannot_be_replayed(tmp_path, monkeypatch, changed):
    from blocks.parse.toc.replay import capture, replay
    d, pdf = fixture(tmp_path, monkeypatch)
    d.generate_directory(tmp_path, pdf, Client())
    evidence = capture(tmp_path, pdf)
    target = pdf if changed == 'pdf' else tmp_path / ('page-3.png' if changed == 'image' else changed)
    target.write_text('{}')
    before = (tmp_path / 'toc.dual-source.json').read_bytes()
    assert replay(tmp_path, pdf, evidence) is None
    assert (tmp_path / 'toc.dual-source.json').read_bytes() == before


def test_stale_or_unapproved_evidence_cannot_be_captured(tmp_path, monkeypatch):
    from blocks.parse.toc.replay import capture
    d, pdf = fixture(tmp_path, monkeypatch)
    d.generate_directory(tmp_path, pdf, Client(omit=True))
    assert capture(tmp_path, pdf) is None
