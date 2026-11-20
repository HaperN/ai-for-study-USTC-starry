"""Original still-image example, executed only by the isolated Manim worker."""

SOURCE = r'''from manim import *
from aifs_media_runtime import load_context


class GeneratedScene(Scene):
    def construct(self):
        context = load_context()
        # The illustrative position belongs only to this sample, not the lesson.
        values = context["approved_content"]["illustration"]
        a, b = float(values["a"]), float(values["b"])
        ink, quiet = "#172033", "#64748B"
        teal, orange = "#007F86", "#B45309"
        template = TexTemplate(
            documentclass=r"\documentclass[preview]{standalone}",
            preamble=r"\usepackage{amsmath}\usepackage{amssymb}\usepackage{xcolor}",
        )

        def math(text, size=44, color=ink):
            return MathTex(text, font_size=size, color=color, tex_template=template)

        # Equal units on both axes; the mathematical points are never moved
        # for layout. Only the labels get an offset.
        axes = Axes(
            x_range=[-0.8, 4.2, 1], y_range=[-2, 2, 1],
            x_length=7.5, y_length=6,
            tips=True, axis_config={"color": quiet, "stroke_width": 1.5,
                                    "include_ticks": False, "tip_width": 0.16,
                                    "tip_height": 0.16},
        ).move_to(UP * 0.45 + LEFT * 0.3)
        origin = axes.c2p(0, 0)
        upper, lower = axes.c2p(a, b), axes.c2p(a, -b)
        vertical = DashedLine(upper, lower, dash_length=0.08,
                              color="#CBD5E1", stroke_width=2)
        guides = VGroup(*[
            DashedLine(axes.c2p(0, y), axes.c2p(a, y), dash_length=0.08,
                       color="#CBD5E1", stroke_width=1.5)
            for y in (b, -b)
        ])
        z_vector = Arrow(origin, upper, buff=0, color=teal, stroke_width=4,
                         max_tip_length_to_length_ratio=0.09)
        conjugate_vector = Arrow(origin, lower, buff=0, color=orange, stroke_width=4,
                                 max_tip_length_to_length_ratio=0.09)
        points = VGroup(Dot(upper, radius=0.055, color=teal),
                        Dot(lower, radius=0.055, color=orange))
        labels = VGroup(
            math(r"z=a+bi", color=teal).next_to(upper, RIGHT, buff=0.24),
            math(r"\overline z=a-bi", color=orange).next_to(lower, RIGHT, buff=0.24),
            math("a", size=34).next_to(axes.c2p(a, 0), DOWN, buff=0.15),
            math("b", size=34).next_to(axes.c2p(0, b), LEFT, buff=0.16),
            math("-b", size=34).next_to(axes.c2p(0, -b), LEFT, buff=0.16),
            math("0", size=30, color=quiet).next_to(origin, DL, buff=0.12),
            math(r"\mathrm{Re}", size=32, color=quiet).next_to(axes.x_axis.get_end(), RIGHT, buff=0.12),
            math(r"\mathrm{Im}", size=32, color=quiet).next_to(axes.y_axis.get_end(), UP, buff=0.12),
        )
        # One subordinate sentence. The surrounding reader supplies the proof.
        note = Text("实部不变 · 虚部变号", font="Noto Sans CJK SC",
                    font_size=27, color=quiet).move_to(DOWN * 3.08)
        sample_note = Text("示意：b > 0", font="Noto Sans CJK SC",
                           font_size=20, color=quiet).move_to(DOWN * 3.6)
        self.add(axes, guides, vertical, z_vector, conjugate_vector, points, labels, note, sample_note)
'''


def conjugate_static_example():
    request = {
        'request_id': 'EXAMPLE-CONJUGATE-STILL', 'kind': 'drawing',
        'learning_goal': '看见共轭复数实部不变、虚部变号，关于实轴对称。',
        'object_refs': [],
    }
    content = {
        'body_markdown': '若 z=a+bi，则共轭为 a-bi。两个点关于实轴对称；b=0 时两点重合。'
                         '本图只画 b>0 的示意位置，不以图上单个位置代替一般说明。',
        'illustration': {'a': 2, 'b': 1.3},
    }
    return {
        'purpose': '原创静态示意：同一坐标系显示共轭对称，以简短小字辅助图形。'
                   '此样例自带独立上下文；只借鉴几何、配色和文字层级，不能把其数值、题目或布局当成本题要求。',
        'context': {'request': request, 'approved_content': content, 'approved_models': []},
        'program': {'schema_version': 'GeneratedMediaCodeV1', 'request_id': request['request_id'],
                    'backend': 'manim', 'code': SOURCE, 'steps': [], 'object_bindings': []},
    }
