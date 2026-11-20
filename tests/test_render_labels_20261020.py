"""修改计划 R1/R3（2026-10-20）：标签与链接文字里的裸 TeX 渲染为公式；VTT 转义。"""
import json
from pathlib import Path
import subprocess

from aiforstudy.checks.offline import parse_webvtt_cues, webvtt_cue_text

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''const fs=require('fs'), {JSDOM}=require('jsdom');
const fields=JSON.parse(fs.readFileSync(0,'utf8'));
const marked=require('./blocks/render/vendor/marked.min.js'),katex=require('./blocks/render/vendor/katex.min.js');
const core=require('./blocks/render/assets/render-core.js').createRenderer({marked,katex});
const html=core.readerDocumentHtml({schema_version:'ReaderDocumentV1',blocks:fields},'body');
const d=new JSDOM(html).window.document;
process.stdout.write(JSON.stringify({errors:d.querySelectorAll('.katex-error,.math-error').length,
  formulas:[...d.querySelectorAll('annotation[encoding="application/x-tex"]')].map(n=>n.textContent),
  text:d.body.textContent}));'''


def _field(pointer, markdown, group='body_markdown'):
    return dict(block_id='b' + str(abs(hash(pointer)) % 10**6), json_pointer=pointer, owner_id='U', group=group,
                field_label='正文', anchor='reader-1123456789abcdef0123', raw_text=markdown,
                visible_text=markdown, source_map=list(range(len(markdown) + 1)), marker_spans=[],
                markdown=markdown)


def _render(fields):
    result = subprocess.run(['node', '-e', SCRIPT], cwd=ROOT, input=json.dumps(fields),
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_bare_tex_in_example_label_renders_as_math():
    out = _render([_field('/examples/0/label', '例10（e^{1+i} 的两个身份）', group='examples')])
    assert out['errors'] == 0
    assert out['formulas'] == ['e^{1+i}']
    assert '例10' in out['text'] and '的两个身份' in out['text']


def test_bare_tex_in_body_link_text_renders_but_prose_stays_text():
    body = ("见 [f'(z_0)=0 时失效](https://example.org/a) 与 [根式函数 w=\\sqrt[n]{z}](https://example.org/b)；"
            "正文里的 report_v2 与 HTTP 不受影响，a_k 不在链接里也不改。")
    out = _render([_field('/body_markdown', body)])
    assert out['errors'] == 0
    assert out['formulas'] == ["f'(z_0)=0", 'w=\\sqrt[n]{z}']
    assert 'report_v2' in out['text'] and 'a_k' in out['text']


def test_vtt_cue_text_escapes_angle_brackets_and_round_trips():
    caption = '把满足 |z_n−z_0|<δ 的点放入圆盘 & 取 $\\frac{1}{2}$'
    vtt = 'WEBVTT\n\nS1\n00:00:00.000 --> 00:00:02.000\n' + webvtt_cue_text(caption) + '\n'
    assert '<' not in vtt.split('\n', 4)[4]
    assert parse_webvtt_cues(vtt)[0]['text'] == caption


def test_implicit_products_and_backslashless_functions_render_in_labels():
    """检验程序发现：nz^{n−1}、cos4φ 曾因“两个以上连续字母”被当成文字。"""
    out = _render([_field('/examples/1/label', '例3（(z^n)′=nz^{n−1} 与 z^4=r^4(cos4φ+i sin4φ)）', group='examples'),
                   _field('/examples/2/label', '例4（w = \\sqrt{z}, n \\geqslant 2）', group='examples')])
    assert out['errors'] == 0
    assert "(z^n)′=nz^{n−1}" in out['formulas']
    assert any('\\cos' in f and '\\sin' in f for f in out['formulas'])
    assert any('\\geqslant' in f for f in out['formulas'])


def test_long_words_with_underscores_stay_text_in_labels():
    out = _render([_field('/examples/3/label', '例5（字段 report_v2 与 media_key）', group='examples')])
    assert out['formulas'] == [] and 'report_v2' in out['text']
