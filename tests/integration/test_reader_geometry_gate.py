"""The production free browser gate catches CSS overrides of reviewed geometry."""
import shutil
import importlib.util
from pathlib import Path
import pytest
from aiforstudy.checks.offline import check_browser_execution

ROOT=Path(__file__).resolve().parents[2]


def production_reader(out, *, font_size=40):
    """Use the published shell, CSS, Markdown layout and figure renderer."""
    from test_content_v4_reader_contract import candidate, catalog
    from aiforstudy.blocks.content.reader_document import project_reader
    from aiforstudy.blocks.render.layout import layout_from_reader_media
    from aiforstudy.core.digest import digest_of
    content=candidate()
    content['body_markdown']='图形观察。'+('\n\n长页正文用于保留真实浏览器滚动条。'*40)
    document=project_reader(content,catalog()).as_dict()
    field=next(b for b in document['blocks'] if b['json_pointer']=='/body_markdown')
    media=[{'request_id':'R','figure_id':'F','kind':'geometry','placement':{
        'target_pointer':'/body_markdown','owner_id':field['owner_id'],
        'span':[0,5],'anchor_text':'图形观察。','field_hash':digest_of(field['raw_text']),
        'position':'after','anchor_id':'media-R'}}]
    view={**content,'reader_document':document,'reader_media':media,
          'reader_layout':layout_from_reader_media(document,media)}
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 500"><text x="50" y="100" font-size="{font_size}">x = 0</text></svg>'
    data={'units':{'U':view},'figures':{'F':{'id':'F','source':'generated','svg':svg}},
          'tree':[{'id':'U','title':'长页','unit':True,'children':[]}]}
    spec=importlib.util.spec_from_file_location('geometry_render_build',ROOT/'blocks/render/build.py')
    build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)
    build.write_reader_site(data,out,book='滚动条契约')
    return svg


def test_long_production_reader_uses_actual_client_width(tmp_path):
    production_reader(tmp_path)
    result=check_browser_execution(tmp_path,node=shutil.which('node'),workspace_root=ROOT)
    assert result.ok,result.as_dict()
    rows={row['profile']:row for row in result.evidence['media_geometry']}
    assert set(rows)=={'wide'}
    desktop=rows['wide']
    assert desktop['viewport']['inner_width']==1280
    assert desktop['viewport']['client_width']==1265
    assert desktop['viewport']['scrollbar_width']==15
    assert desktop['viewport']['scroll_height']>900
    assert desktop['reviewed_minimum_width']==450
    # Static figures use the whole 451.94px comparison column; videos lose
    # two pixels to their border. Both are audited at the 450px image size.
    # 修改计划 S（2026-10-20）：固定版式按固定逻辑宽度等比缩放，以便墨迹在任何窗口宽度下对齐；
    # 主栏比参考宽度宽 1–2px 时缩放略大于 1，媒体宽差不到 1px，不影响约 450px 的可读性判断。
    assert desktop['surfaces'][0]['width']==pytest.approx(451.94,abs=1.5)


@pytest.mark.parametrize('override,reason',[
    ('.reader-media .media-viewport{height:80px !important}', 'media cropped'),
    ('.reader-media svg{clip-path:inset(0 0 30% 0)}', 'media cropped'),
    ('.reader-media{height:80px;overflow:hidden}', 'media cropped'),
    ('.reader-media{clip-path:inset(0 0 30% 0)}', 'media cropped'),
])
def test_long_production_reader_rejects_harmful_decoration(tmp_path,override,reason):
    production_reader(tmp_path)
    with (tmp_path/'assets/styles.css').open('a') as stream:stream.write('\n'+override)
    result=check_browser_execution(tmp_path,node=shutil.which('node'),workspace_root=ROOT)
    assert result.status=='failed',result.as_dict()
    assert any(reason in error for error in result.evidence['media_layout_errors'])


@pytest.mark.parametrize('height,client_width',[(500,1280),(2600,1265)])
def test_desktop_audit_uses_comparison_width_with_and_without_page_scrollbar(tmp_path,height,client_width):
    from aiforstudy.blocks.media.validation import capture_display
    from aiforstudy.blocks.media.review_policy import DEFAULT_DISPLAY_PROFILES
    svg=tmp_path/'marginal.svg'
    svg.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 {height}"><text x="50" y="100" font-size="24">x = 0</text></svg>')
    rows=capture_display(svg,profiles=DEFAULT_DISPLAY_PROFILES,out_dir=tmp_path/'audit',workspace_root=ROOT)
    assert len(rows)==1
    desktop=rows[0]
    assert desktop['profile_id']=='wide'
    assert desktop['media_width']==450
    assert desktop['client_width']==client_width
    assert desktop['scrollbar_width']==1280-client_width
    assert desktop['labels'][0]['font_px']==pytest.approx(13.5)


def test_long_production_reader_reports_small_label_as_advisory(tmp_path):
    production_reader(tmp_path,font_size=24)
    result=check_browser_execution(tmp_path,node=shutil.which('node'),workspace_root=ROOT)
    assert result.ok,result.as_dict()
    assert any('SVG label below reviewed display minimum' in error for error in result.evidence['appearance_warnings'])

def test_production_browser_refuses_picture_width_override(tmp_path):
    html='''<!doctype html><html><head><style>
    body{margin:0}.reader-media{max-width:780px;width:calc(100vw - 32px);margin:0 auto}
    .media-viewport{width:100%;overflow:hidden;border-radius:12px}
    svg{display:block;width:100%;height:auto}
    </style></head><body><div class="reader-media"><div class="media-viewport">
    <svg viewBox="0 0 800 500"><text x="50" y="100" font-size="40">x = 0</text></svg>
    </div></div><script>window.__DATA__={units:{U:{}}}</script></body></html>'''
    page=tmp_path/'index.html';page.write_text(html)
    good=check_browser_execution(tmp_path,node=shutil.which('node'),workspace_root=ROOT)
    assert good.ok,good.as_dict()
    desktop=next(row for row in good.evidence['media_geometry'] if row['profile']=='wide')
    assert desktop['viewport']['scrollbar_width']==0
    assert desktop['reviewed_minimum_width']==450
    page.write_text(html.replace('</style>','svg {width:90% !important}</style>'))
    bad=check_browser_execution(tmp_path,node=shutil.which('node'),workspace_root=ROOT)
    assert bad.ok,bad.as_dict()
    assert bad.evidence['appearance_warnings']


@pytest.mark.parametrize('override,key',[
    ('.reader-document{display:none!important}', 'reader_content_errors'),
    ('.reader-media text{font-size:0!important}', 'media_layout_errors'),
])
def test_invisible_content_is_functional_failure_not_font_advice(tmp_path,override,key):
    production_reader(tmp_path)
    with (tmp_path/'assets/styles.css').open('a') as stream: stream.write('\n'+override)
    report=check_browser_execution(tmp_path,node=shutil.which('node'),workspace_root=ROOT)
    assert report.status=='failed',report.as_dict()
    assert report.evidence[key]
