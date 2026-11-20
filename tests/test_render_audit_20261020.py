"""修改计划 R4（2026-10-20）：最终产物渲染检验——入口发现、字幕扫描、报告与 CLI。"""
import json
from pathlib import Path

import pytest

from aiforstudy.checks import render_audit
from aiforstudy.checks.offline import webvtt_cue_text
from aiforstudy.cli import build_parser


def _entry(root: Path, name: str):
    folder = root / name
    (folder / 'data').mkdir(parents=True)
    (folder / 'index.html').write_text('<html></html>', encoding='utf-8')
    (folder / 'data' / 'data.js').write_text('window.__DATA__={units:{}}', encoding='utf-8')


def test_find_entries_only_counts_reader_pages(tmp_path):
    _entry(tmp_path, 'CH01')
    _entry(tmp_path, 'book')
    (tmp_path / 'media').mkdir()
    (tmp_path / 'media' / 'index.html').write_text('<html></html>', encoding='utf-8')
    assert [e['name'] for e in render_audit.find_entries(tmp_path)] == ['CH01', 'book']


def test_scan_vtt_flags_unescaped_angle_and_raw_tex_once_per_content(tmp_path):
    bad = 'WEBVTT\n\nS1\n00:00:00.000 --> 00:00:02.000\n当 |z|<1 时取 $\\frac{1}{2}$\n'
    (tmp_path / 'a').mkdir()
    (tmp_path / 'a' / 'x.vtt').write_text(bad, encoding='utf-8')
    (tmp_path / 'b.vtt').write_text(bad, encoding='utf-8')              # 同内容副本只报一次
    good = 'WEBVTT\n\nS1\n00:00:00.000 --> 00:00:02.000\n' + webvtt_cue_text('当 |z|<1 时成立') + '\n'
    (tmp_path / 'good.vtt').write_text(good, encoding='utf-8')
    (tmp_path / 'broken.vtt').write_text('not a vtt', encoding='utf-8')
    kinds = sorted(i['kind'] for i in render_audit.scan_vtt(tmp_path))
    assert kinds == ['vtt_invalid', 'vtt_raw_tex', 'vtt_unescaped_angle']


def test_summary_and_markdown_report_in_chinese():
    report = {'root': '/x', 'checked_at': 'now', 'vtt': [{'kind': 'vtt_raw_tex', 'file': 'a.vtt', 'line': 4, 'snippet': '$a$'}],
              'entries': [{'name': 'CH01', 'ready': 'ok', 'ready_seconds': 1.0, 'declared_units': 2,
                           'units': [{'unit_id': 'U1', 'issues': [{'kind': 'raw_dollar', 'pointer': '/body_markdown', 'snippet': '$x'}]},
                                     {'unit_id': 'U2', 'issues': []}]},
                          {'name': 'book', 'ready': 'timeout', 'error': '页面在限定时间内没有给出就绪信号'}]}
    report['summary'] = render_audit._summary(report)
    assert report['summary']['issue_counts'] == {'raw_dollar': 1, 'vtt_raw_tex': 1}
    assert report['summary']['entries_not_ready'] == ['book']
    assert report['summary']['units_with_issues'] == 1
    text = render_audit._markdown(report)
    assert '可见文字残留 $' in text and 'U1 / /body_markdown' in text and '未就绪的入口：** book' in text


def test_audit_rejects_directory_without_reader_entries(tmp_path):
    from aiforstudy.errors import InputError
    with pytest.raises(InputError):
        render_audit.audit_delivery(tmp_path, out_dir=tmp_path / 'out')


def test_cli_check_render_is_registered_and_defaults_outside_delivery(tmp_path, monkeypatch):
    args = build_parser().parse_args(['check-render', str(tmp_path / 'pkg')])
    assert args.func.__name__ == 'cmd_check_render' and args.out is None
    seen = {}

    def fake_audit(root, *, out_dir, **kw):
        seen['out'] = Path(out_dir)
        return {'summary': {'entries': 1, 'units_checked': 0, 'units_with_issues': 0,
                            'entries_not_ready': [], 'issue_counts': {}}}
    monkeypatch.setattr(render_audit, 'audit_delivery', fake_audit)
    monkeypatch.setattr('aiforstudy.cli.open_workspace', lambda _ws: type('W', (), {'root': tmp_path})())
    assert args.func(args) == 0
    assert seen['out'] == (tmp_path / 'pkg-render-check').resolve()


def _browser_ready():
    try:
        from aiforstudy.blocks.media.validation import display_runtime
        display_runtime()
        return True
    except Exception:                                                     # noqa: BLE001
        return False


@pytest.mark.skipif(not _browser_ready(), reason='需要本机 Chromium + Playwright')
def test_browser_audit_finds_raw_tex_in_rendered_unit(tmp_path):
    folder = tmp_path / 'pkg' / 'CH01'
    (folder / 'data').mkdir(parents=True)
    data = {'units': {'U1': {'body_markdown': '正文'}}}
    (folder / 'data' / 'data.js').write_text('window.__DATA__=' + json.dumps(data), encoding='utf-8')
    (folder / 'index.html').write_text(
        '<html><body><main></main><script src="data/data.js"></script><script>'
        'function show(){var id=location.hash.slice(2)||"U1";'
        'document.querySelector("main").innerHTML=\'<div class="unit"><div data-reader-pointer="/body_markdown">'
        '这里有一段没有渲染的公式 $\\\\frac{1}{2}$，以及足够长的正文文字用来通过空正文检查。</div></div>\';'
        'document.documentElement.setAttribute("data-reader-unit",id);}'
        'show();window.addEventListener("hashchange",show);'
        'document.documentElement.setAttribute("data-reader-ready","ok");</script></body></html>', encoding='utf-8')
    report = render_audit.audit_delivery(tmp_path / 'pkg', out_dir=tmp_path / 'out', ready_timeout=30, unit_timeout=10)
    assert report['summary']['entries_not_ready'] == []
    assert report['summary']['issue_counts'].get('raw_dollar') == 1
    assert (tmp_path / 'out' / 'render-check.md').is_file()
