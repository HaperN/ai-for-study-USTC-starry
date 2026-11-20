"""The balanced page check measures runtime usability, not old model metadata."""
from pathlib import Path
import shutil

import pytest

from aiforstudy.checks.offline import check_browser_execution, _browser_executable
from aiforstudy.core.review_mode import review_mode


@pytest.mark.parametrize('defect', [None, 'image', 'exception', 'empty', 'hidden', 'body_missing'])
def test_balanced_browser_checks_actual_page_and_ignores_unused_model_binding(tmp_path, defect):
    root=Path(__file__).resolve().parents[2]
    if not shutil.which('node') or not _browser_executable(root):
        pytest.skip('real Chromium/Node unavailable')
    body='<main><h1>复数</h1><article class="reader-document">这段正文应当正常显示。</article></main>'
    # The old checker treats this unused model declaration as missing content.
    script='window.__DATA__={units:{U:{models:{M:{}}}},models:{M:{template:"harmonic-contours"}}};'
    if defect=='image': body+='<img src="missing-image.png">'
    if defect=='exception': script+='throw new Error("page failed to render");'
    if defect=='empty': body='<main></main>'
    if defect=='hidden': body=body.replace('class="reader-document"','class="reader-document" style="display:none"')
    if defect=='body_missing':
        body='<main><h1>复数</h1></main>'
        script+='window.__DATA__.units.U.reader_document={blocks:[{json_pointer:"/body_markdown",raw_text:"应当显示的正文"}]};'
    (tmp_path/'index.html').write_text('<!doctype html><meta charset="utf-8">'+body+'<script>'+script+'</script>')
    with review_mode('balanced'):
        report=check_browser_execution(tmp_path,node=shutil.which('node'),workspace_root=root)
    # 用户 2026-10-20 定：只有正文显示问题判不通过；坏图、脚本报错在正文正常时只记警告。
    body_defect = defect in ('empty','hidden','body_missing')
    assert report.status==('failed' if body_defect else 'accepted'), report.as_dict()
    if defect=='image': assert report.evidence['missing_images'] and report.evidence['technical_warnings']['missing_images']
    if defect=='exception': assert report.evidence['runtime_errors'] and report.evidence['technical_warnings']['runtime_errors']
    if defect in ('empty','hidden','body_missing'): assert report.evidence['reader_content_errors']


@pytest.mark.parametrize(('header_text', 'body_state', 'status'), [
    ('', 'visible', 'accepted'),
    ('', 'hidden', 'failed'),
    ('', 'missing', 'failed'),
    ('复数', 'missing', 'failed'),
])
def test_balanced_browser_checks_body_separately_from_optional_header(
    tmp_path, header_text, body_state, status,
):
    """An empty title wrapper is harmless; a title cannot replace the body."""
    root = Path(__file__).resolve().parents[2]
    if not shutil.which('node') or not _browser_executable(root):
        pytest.skip('real Chromium/Node unavailable')
    # The production reader uses .reader-document for both regions, and leaves
    # an empty header wrapper when the authored title lives inside body_markdown.
    header = '<header class="unit-head"><div class="reader-document">' + header_text + '</div></header>'
    body = '' if body_state == 'missing' else (
        '<div class="reader-document"' + (' style="display:none"' if body_state == 'hidden' else '')
        + '><h1>复数</h1><p>正文中的定义、例题和配图都应当可见。</p></div>'
    )
    data = 'window.__DATA__={units:{U:{reader_document:{blocks:[{json_pointer:"/body_markdown",raw_text:"复数的正文"}]}}}};'
    (tmp_path / 'index.html').write_text('<!doctype html><meta charset="utf-8">'
        '<main><article class="unit">' + header + body + '</article><footer>学习页面</footer></main>'
        '<script>' + data + '</script>')
    with review_mode('balanced'):
        report = check_browser_execution(tmp_path, node=shutil.which('node'), workspace_root=root)
    assert report.status == status, report.as_dict()
    assert bool(report.evidence['reader_content_errors']) == (status == 'failed')
