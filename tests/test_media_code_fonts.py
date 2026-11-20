"""Chinese glyphs must survive the generated builder -> host savefig boundary."""
from pathlib import Path
import re

from aiforstudy.blocks.media.code_runtime import render_generated_code

ROOT = Path(__file__).resolve().parents[1]


def test_chinese_labels_survive_local_rc_context_and_host_save(tmp_path):
    # Real generated programs often scope their style and return the Figure.
    # The worker saves it only after rc_context has restored the outer defaults.
    code = '''import matplotlib.pyplot as plt
from matplotlib import font_manager
def build_figure(context):
    family = plt.rcParams["font.family"][0]
    with plt.rc_context({"font.family": "sans-serif",
                         "font.sans-serif": [family]}):
        fig, ax = plt.subplots(figsize=(4, 2))
        ax.set_axis_off()
        ax.text(.1, .5, "共轭实轴对称", fontsize=24)
        return fig
'''
    program = {"schema_version": "GeneratedMediaCodeV1", "request_id": "R1",
               "backend": "matplotlib", "code": code, "steps": [], "object_bindings": []}
    context = {"request": {"request_id": "R1", "kind": "drawing", "object_refs": []},
               "approved_content": {}, "approved_models": {}}
    result = render_generated_code(program, context=context, out_dir=tmp_path / "out",
                                   workspace_root=ROOT)
    assert result["status"] == "needs_review", result
    svg = (tmp_path / "out/figure.svg").read_text()
    label = svg.split("<!-- 共轭实轴对称 -->", 1)[1].split("</g>", 1)[0]
    glyphs = re.findall(r'xlink:href="#([^"]+)"', label)
    # All six different characters need actual distinct glyph outlines, not one
    # repeated LastResort tofu symbol accepted merely because pixels are nonempty.
    assert len(set(glyphs)) == 6, glyphs
    assert all(glyph.startswith("NotoSansCJK") for glyph in glyphs), glyphs


def test_explicit_latin_font_is_not_replaced_by_the_worker(tmp_path):
    code = '''import matplotlib.pyplot as plt
def build_figure(context):
    fig, ax = plt.subplots(figsize=(4, 2))
    ax.set_axis_off()
    ax.text(.1, .5, "Explicit Latin", fontfamily="DejaVu Serif", fontsize=24)
    return fig
'''
    program = {"schema_version": "GeneratedMediaCodeV1", "request_id": "R1",
               "backend": "matplotlib", "code": code, "steps": [], "object_bindings": []}
    context = {"request": {"request_id": "R1", "kind": "drawing", "object_refs": []},
               "approved_content": {}, "approved_models": {}}
    result = render_generated_code(program, context=context, out_dir=tmp_path / "out",
                                   workspace_root=ROOT)
    assert result["status"] == "needs_review", result
    svg = (tmp_path / "out/figure.svg").read_text()
    label = svg.split("<!-- Explicit Latin -->", 1)[1].split("</g>", 1)[0]
    glyphs = re.findall(r'xlink:href="#([^"]+)"', label)
    assert glyphs and all(glyph.startswith("DejaVuSerif") for glyph in glyphs), glyphs


def test_static_generation_example_uses_real_manim_chinese_and_formula_output(tmp_path):
    from aiforstudy.blocks.media.code_generation import _example
    from PIL import Image
    example = _example('drawing')
    result = render_generated_code(example['program'], context=example['context'],
                                   out_dir=tmp_path / 'out', workspace_root=ROOT, profile='final')
    assert result['status'] == 'needs_review', result
    assert [r['kind'] for r in result['resources']] == ['image']
    assert result['steps'] == []
    assert not (tmp_path / 'out/figure.svg').exists()
    with Image.open(tmp_path / 'out/figure.png') as image:
        assert image.size == (1280, 720)
    assert 'Noto' in result['technical_report']['worker']['result']['font']['family']
