"""An original, self-contained teaching example for the existing code prompt.

The source is transported as data and only runs inside the media worker.
Its synthetic context is deliberately separate from the current lesson.
"""


SOURCE = r'''from manim import *
from aifs_media_runtime import media_step, load_context


class GeneratedScene(Scene):
    def construct(self):
        context = load_context()
        model = next(m for m in context["approved_models"] if m["model_id"] == "EXAMPLE-M")
        obj = next(o for o in model["objects"] if o["math_object_id"] == "w")
        a, b = [float(entry["value"]) for entry in obj["entries"]]
        teal, orange, green = "#007F86", "#B45309", "#187542"
        ink = "#172033"
        template = TexTemplate(
            documentclass=r"\documentclass[preview]{standalone}",
            preamble=r"\usepackage{amsmath}\usepackage{amssymb}\usepackage{xcolor}",
        )

        def formula(*parts, size=58):
            return MathTex(*parts, font_size=size, color=ink, tex_template=template)

        title = Text("交叉项，为什么合成两倍实部？", font="Noto Sans CJK SC",
                     font_size=36, color="#172033").move_to(UP * 3.12)
        factors = formula(r"|z_1+z_2|^2=", "(", "z_1", "+", "z_2", ")",
                          "(", r"\overline{z_1}", "+", r"\overline{z_2}", ")", size=58)
        factors.move_to(UP * 1.65)
        expanded = formula(r"|z_1|^2", "+", r"z_1\overline{z_2}", "+",
                           r"z_2\overline{z_1}", "+", r"|z_2|^2", size=68)
        expanded.move_to(UP * 0.25)
        expanded[2].set_color(teal)
        expanded[4].set_color(orange)
        expanded_equal = formula("=", size=68).next_to(expanded, LEFT, buff=0.18)
        w_label = formula(r"w=z_1\overline{z_2}", size=66).set_color(teal).move_to(DOWN * 1.2)
        conjugate_identity = formula(r"\overline w=\overline{z_1}z_2=z_2\overline{z_1}", size=60)
        conjugate_identity.set_color(orange).move_to(DOWN * 2.35)

        # Static initial state is visible in the actual first frame and poster.
        self.add(title, factors)
        with media_step(self, "S1"):
            self.wait(1.6)
            self.wait(1.5)
            # The two factor selections visibly account for the two cross terms.
            self.play(Write(expanded[0]), Write(expanded[6]), run_time=1)
            for left, right, term, color in [(2, 9, 2, teal), (4, 7, 4, orange)]:
                selected = VGroup(SurroundingRectangle(factors[left], color=color, buff=0.1),
                                  SurroundingRectangle(factors[right], color=color, buff=0.1))
                self.play(Create(selected), run_time=0.5)
                self.play(TransformFromCopy(VGroup(factors[left], factors[right]), expanded[term]), run_time=1)
                self.wait(0.6)
                self.play(FadeOut(selected), run_time=0.3)
            self.play(Write(VGroup(expanded_equal, expanded[1], expanded[3], expanded[5])), run_time=0.7)
            self.play(expanded[0].animate.set_opacity(0.4), expanded[6].animate.set_opacity(0.4),
                      TransformFromCopy(expanded[2], w_label), run_time=1)
            self.play(TransformFromCopy(expanded[4], conjugate_identity), run_time=1)
            self.wait(2)

        with media_step(self, "S2"):
            self.play(*[FadeOut(mob) for mob in list(self.mobjects)], run_time=0.7)
            title2 = Text("共轭相加：虚部抵消，实部相加", font="Noto Sans CJK SC",
                          font_size=36, color="#172033").move_to(UP * 3.12)
            numeric_note = Text("数值示意", font="Noto Sans CJK SC", font_size=28,
                                color="#172033").move_to(LEFT * 4.5 + UP * 2.25)
            example_label = formula(r"z_1=1+i,\qquad z_2=2+i", size=58).move_to(RIGHT + UP * 2.25)
            example_result = formula(r"w=3+i,\qquad\overline w=3-i", size=58).move_to(RIGHT + UP * 2.25)
            # Equal physical units on the real and imaginary axes: 8.4/7 = 3.6/3.
            axes = Axes(x_range=[-0.5, 6.5, 1], y_range=[-1.5, 1.5, 1], x_length=8.4, y_length=3.6,
                        tips=True, axis_config={"color": "#64748B", "include_ticks": False,
                                                "stroke_width": 2}).shift(DOWN * 0.3)
            origin = axes.c2p(0, 0)
            w_vector = Arrow(origin, axes.c2p(a, b), buff=0, color=teal, stroke_width=8)
            conjugate_vector = Arrow(origin, axes.c2p(a, -b), buff=0, color=orange, stroke_width=8)
            w_name = formula("w", size=60).set_color(teal).next_to(w_vector.get_end(), UP, buff=0.18)
            bar_name = formula(r"\overline w", size=60).set_color(orange).next_to(conjugate_vector.get_end(), DOWN, buff=0.16)
            real_label = formula(r"\mathrm{Re}", size=46).next_to(axes.x_axis.get_end(), RIGHT, buff=0.1)
            imaginary_label = formula(r"\mathrm{Im}", size=46).next_to(axes.y_axis.get_end(), LEFT, buff=0.15)
            zero_label = formula("0", size=48).next_to(origin, DL, buff=0.12)
            guide = DashedLine(axes.c2p(a, b), axes.c2p(a, -b), color="#94A3B8", stroke_width=2)
            general = formula(r"(a+bi)+(a-bi)=2a", size=66).move_to(DOWN * 2.95)
            self.play(FadeIn(title2), FadeIn(numeric_note), FadeIn(example_label), Create(axes),
                      FadeIn(VGroup(real_label, imaginary_label, zero_label)), run_time=1)
            self.wait(1)
            self.play(Transform(example_label, example_result), run_time=1)
            self.play(GrowArrow(w_vector), Write(w_name), run_time=1.4)
            self.play(GrowArrow(conjugate_vector), Write(bar_name), Create(guide), run_time=1.4)
            self.wait(1.8)
            moving = conjugate_vector.copy()
            self.add(moving)
            self.play(conjugate_vector.animate.set_opacity(0.25), bar_name.animate.set_opacity(0.4),
                      guide.animate.set_opacity(0.3), run_time=0.5)
            self.play(moving.animate.shift(axes.c2p(a, b) - origin), run_time=2)
            moving_name = formula(r"\overline w", size=60).set_color(orange)
            moving_name.next_to(moving, UP, buff=0.16)
            # The original position has served its comparison purpose. Clear it
            # before the sum label enters the same region.
            self.play(FadeOut(conjugate_vector), FadeOut(bar_name), FadeOut(guide),
                      FadeIn(moving_name), run_time=0.5)
            result = Arrow(origin, axes.c2p(2*a, 0), buff=0, color=green, stroke_width=8)
            result_name = formula(r"w+\overline w=6", size=60).set_color(green)
            result_name.next_to(result, DOWN, buff=0.36)
            self.play(GrowArrow(result), Write(result_name), run_time=1.3)
            self.wait(1.5)
            self.play(Write(general), run_time=1.5)
            self.wait(2.5)

        with media_step(self, "S3"):
            self.play(*[FadeOut(mob) for mob in list(self.mobjects)], run_time=0.7)
            title3 = Text("把看见的关系，放回原来的推导", font="Noto Sans CJK SC",
                          font_size=36, color="#172033").move_to(UP * 3.12)
            cross = formula(r"w+\overline w=2\operatorname{Re}w", size=68).move_to(UP * 1.95)
            cross.set_color(green)
            # A complete aligned identity enters together: never leave an
            # intermediate equality on screen with its cross term missing.
            identity = formula(
                r"\begin{aligned}|z_1+z_2|^2&=|z_1|^2+|z_2|^2\\"
                r"&\quad+\color[HTML]{187542}2\operatorname{Re}(z_1\overline{z_2})\end{aligned}",
                size=66,
            ).move_to(ORIGIN)
            check_label = Text("数值核对，不代替一般证明", font="Noto Sans CJK SC",
                               font_size=28, color="#172033").move_to(DOWN * 1.85)
            check = formula(r"13=2+5+6", size=66).move_to(DOWN * 2.85)
            self.play(FadeIn(title3), Write(cross), run_time=1.2)
            self.wait(1.3)
            self.play(FadeIn(identity), run_time=1.8)
            self.wait(2.2)
            self.play(FadeIn(check_label), Write(check), run_time=1.3)
            self.wait(2.5)
'''


def cross_terms_example():
    """Return fresh, explicitly synthetic inputs alongside a complete program."""
    steps = [
        {"step_id": "S1", "author_step_index": 0,
         "caption": "展开后，两个交叉项互为共轭。",
         "observation": "用相同颜色追踪因子、交叉项与 w 的定义。",
         "expected_change": "从乘积展开为四项，再辨认出 w 与它的共轭。"},
        {"step_id": "S2", "author_step_index": 1,
         "caption": "橙色向量首尾相接：虚部抵消，实部相加。数值只作示意。",
         "observation": "比较淡色原向量与平移后的向量；一般代数式解释抵消。",
         "expected_change": "共轭向量保持方向和长度平移，和落在实轴上。"},
        {"step_id": "S3", "author_step_index": 2,
         "caption": "回到原题：交叉项合成两倍实部；数值核对不代替一般证明。",
         "observation": "把局部关系代回完整恒等式。",
         "expected_change": "由 w 的共轭相加关系得到原题的一般结论。"},
    ]
    objects = [{"model_id": "EXAMPLE-M", "object_id": "w"}]
    request = {
        "request_id": "EXAMPLE-CROSS-TERMS-ONLY", "kind": "animation",
        "learning_goal": "理解例题展开后的两个交叉项为什么等于两倍实部。",
        "observe": "代数项的来源、互为共轭、向量相加时虚部抵消。",
        "misconception": "不能只因两个点关于实轴对称，就直接把反射动画称为相加。",
        "object_refs": objects, "math_model_refs": [{"model_id": "EXAMPLE-M"}],
        "conditions": ["z1、z2 为任意复数；数值示意取 z1=1+i、z2=2+i。"],
        "animation": {
            "initial_state": "显示模平方与共轭乘积关系，尚未合并交叉项。",
            "steps": ["展开四项，辨认互为共轭的两个交叉项。",
                      "以首尾相接演示共轭相加，并用 a±bi 解释虚部抵消。",
                      "代回原题恒等式，数值示意只用于核对。"],
            "key_pauses": ["w 与其共轭身份明确后", "相加结果落在实轴后"],
            "final_state": "完整的一般恒等式，并标明数值只是核对。",
        },
        "acceptance_criteria": ["颜色与标签身份一致；不把反射等同于相加。",
                                "完整保留一般推导；不以一个数值证明恒等式。"],
    }
    model = {"model_id": "EXAMPLE-M", "objects": [
        {"math_object_id": "w", "type": "vector", "entries": [
            {"type": "number", "value": "3"}, {"type": "number", "value": "1"}]}]}
    content = {
        "body_markdown": "例题：展开 |z1+z2|² 后，令 w=z1·共轭z2。另一交叉项等于共轭w。"
                         "若 w=a+bi，则 w+共轭w=(a+bi)+(a−bi)=2a=2Re(w)，代回得恒等式。",
        "examples": [{"id": "EXAMPLE-CROSS-TERMS", "label": "原创教学样例",
                      "statement": "证明 |z1+z2|²=|z1|²+|z2|²+2Re(z1·共轭z2)。",
                      "illustration": "z1=1+i，z2=2+i 时 w=3+i；13=2+5+6 仅为核对。"}],
        "math_models": [model],
    }
    return {
        "purpose": "原创完整教学样例：同一条推导连接公式与几何。只借鉴对象追踪、参照、停顿和解释方法；"
                   "本样例的题目、合成数值、ID、三个步骤和布局均不是当前任务要求，不能原样复制到其他题。",
        "context": {"request": request, "approved_content": content, "approved_models": [model]},
        "program": {"schema_version": "GeneratedMediaCodeV1", "request_id": request["request_id"],
                    "backend": "manim", "code": SOURCE, "steps": steps,
                    "object_bindings": [{"display_id": "w_vector", "model_id": "EXAMPLE-M", "object_id": "w"}]},
    }
