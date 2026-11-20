"""Static Manim produces a real PNG and keeps the drawing pipeline image-based."""
from pathlib import Path

from aiforstudy.blocks.media.code_contracts import BACKENDS, validate_media_code
from aiforstudy.blocks.media.code_execution import _render_row
from aiforstudy.blocks.media import code_runtime
from aiforstudy.core.digest import digest_file
from aiforstudy.core.review_mode import review_mode

ROOT = Path(__file__).resolve().parents[1]

STATIC_SCENE = r'''from manim import Scene, Axes, Text, MathTex, VGroup, DOWN, BLUE
class GeneratedScene(Scene):
    def construct(self):
        axes = Axes(x_range=[-2, 2, 1], y_range=[0, 4, 1],
                    x_length=6, y_length=3, axis_config={"color": "#61738A"})
        graph = axes.plot(lambda x: x*x, x_range=[-2, 2], color=BLUE)
        label = VGroup(Text("平方函数", font_size=24), MathTex("y=x^2", font_size=30)).arrange(DOWN)
        label.next_to(axes, direction=DOWN, buff=.2)
        self.add(axes, graph, label)
'''


def drawing():
    request = {"request_id": "manim-still", "kind": "drawing", "object_refs": []}
    program = {"schema_version": "GeneratedMediaCodeV1", "request_id": request["request_id"],
               "backend": "manim", "code": STATIC_SCENE, "steps": [], "object_bindings": []}
    return request, program


def test_new_drawing_uses_manim_without_animation_steps():
    request, program = drawing()
    assert BACKENDS["drawing"] == "manim"
    assert validate_media_code(program, request) == program


def test_png_only_static_result_mounts_without_fabricated_svg(tmp_path):
    request, program = drawing()
    source = tmp_path / "figure.png"
    source.write_bytes(b"image bytes are verified by the renderer")
    resource = {"kind": "image", "relative_path": source.name, "sha256": digest_file(source)}
    row = _render_row({"request_id": request["request_id"], "request": request,
                       "kind": "drawing", "math_context_digest": "context"}, program,
                      {"source_dir": str(tmp_path), "resources": [resource]}, "final")
    assert row["image"] == "figure.png" and row["svg"] is None
    assert row["entry"]["file"] == "figures/figure.png"
    assert row["entry"]["svg"] is None
    assert "animation" not in row
    from aiforstudy.pipeline.media_stages import _copy_static_resources
    with review_mode("balanced"):
        entry, paths = _copy_static_resources(row, "U", tmp_path / "published")
    assert entry["file"] == "figures/U/manim-still/figure.png"
    assert Path(paths["figure.png"]).read_bytes() == source.read_bytes()


def test_static_preflight_selects_still_scene_and_not_video(monkeypatch):
    dispatched = []
    def render(program, **kwargs):
        dispatched.append((program, kwargs["context"]))
        return {"status": "needs_review", "reason": "", "technical_report": {
            "environment": {"tools": {"ffmpeg": {"available": True}}}}}
    monkeypatch.setattr(code_runtime, "render_generated_code", render)
    report = code_runtime.preflight_generated_code("manim", kind="drawing")
    assert report["kind"] == "drawing"
    program, context = dispatched[0]
    assert program["steps"] == [] and "media_step" not in program["code"]
    assert context["request"]["kind"] == "drawing"


def test_real_manim_static_has_png_math_font_and_no_video(tmp_path):
    request, program = drawing()
    result = code_runtime.render_generated_code(program,
        context={"request": request, "approved_content": {}, "approved_models": {}},
        out_dir=tmp_path / "out", workspace_root=ROOT, profile="final")
    assert result["status"] == "needs_review", result
    assert result["steps"] == []
    assert [(r["kind"], r["relative_path"]) for r in result["resources"]] == [("image", "figure.png")]
    assert result["technical_report"]["image_decode_ok"] is True
    assert result["technical_report"]["isolation"]["safe"] is True
    assert result["technical_report"]["mathematical_status"] == "requires_visual_review"
    assert not (tmp_path / "out" / "video.mp4").exists()
    assert not (tmp_path / "out" / "figure.svg").exists()
    from PIL import Image
    with Image.open(tmp_path / "out" / "figure.png") as image:
        assert image.size == (1280, 720)


def test_drawing_capability_requires_the_selected_manim_runtime(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from aiforstudy.pipeline import media_stages
    from aiforstudy.checks import offline
    from aiforstudy.blocks.interaction import spec
    from aiforstudy.pipeline import interaction_runtime
    available = []
    caps = SimpleNamespace(as_dict=lambda: {}, render_backends=lambda: available)
    ctx = SimpleNamespace(services=SimpleNamespace(capabilities=caps),
                          workspace=SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(offline, '_browser_executable', lambda _: '/installed/browser')
    monkeypatch.setattr(media_stages, '_load_content', lambda _: ({}, {}))
    monkeypatch.setattr(media_stages, '_source_assets', lambda _: {})
    monkeypatch.setattr(media_stages, '_source_context', lambda _: {})
    monkeypatch.setattr(interaction_runtime, 'interaction_runtime_inputs', lambda _: {})
    monkeypatch.setattr(spec, 'interaction_capabilities', lambda *a, **k: {
        'available':False, 'targets':[], 'reader_protocol':'test'})
    assert 'drawing' not in media_stages._capabilities(ctx)['kinds']
    available.append('manim')
    result = media_stages._capabilities(ctx)
    assert {'drawing','animation'}.issubset(result['kinds'])
    assert result['static_renderer'] == 'api-manim-still-python-v1'
