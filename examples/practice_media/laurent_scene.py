"""A moving radius selects the Laurent expansion about zero.

Run only through the project's isolated media worker. The five media steps
match the independently reviewed narration in the companion scene spec.
"""
from manim import *
from aifs_media_runtime import media_step
import math
import numpy as np

INK = '#20364d'
BLUE = '#267b9d'
ORANGE = '#c9804b'
RED = '#b74445'
LINE = '#748a9e'
WASH = '#e8f3f6'


class GeneratedScene(Scene):
    def construct(self):
        title = Text('洛朗展开：半径跨过奇点后，换一种幂次', font_size=34)
        title.to_edge(UP, buff=.23)
        definition = MathTex(
            r'f(z)=\frac1{(z-1)(z-3)}=',
            r'-\frac1{2(z-1)}', '+', r'\frac1{2(z-3)}', font_size=35)
        definition[1].set_color(BLUE)
        definition[3].set_color(ORANGE)
        definition.move_to([0, 2.86, 0])

        r = ValueTracker(.3)
        axes = Axes(x_range=[-4.4, 4.4, 1], y_range=[-4.4, 4.4, 1],
                    x_length=5.2, y_length=5.2, tips=False,
                    axis_config={'color': LINE, 'stroke_width': 1.4,
                                 'include_ticks': False})
        axes.move_to([-3.8, -.15, 0])
        origin = axes.c2p(0, 0)
        scale = np.linalg.norm(axes.c2p(1, 0) - origin)
        regions = VGroup(
            Circle(radius=scale, fill_color=BLUE, fill_opacity=.14, stroke_width=0),
            Annulus(inner_radius=scale, outer_radius=3*scale,
                    fill_color=ORANGE, fill_opacity=.13, stroke_width=0),
            Annulus(inner_radius=3*scale, outer_radius=4.2*scale,
                    fill_color=BLUE, fill_opacity=.12, stroke_width=0))
        for shape in regions:
            shape.move_to(origin)
        limits = VGroup(*[
            DashedVMobject(Circle(radius=a*scale, color=LINE, stroke_width=2)
                          .move_to(origin), num_dashes=48 if a == 3 else 30)
            for a in (1, 3)])
        ray = DashedLine(origin, axes.c2p(4.2/math.sqrt(2), 4.2/math.sqrt(2)),
                         color=LINE, stroke_width=1.3)
        poles = VGroup()
        for a in (1, 3):
            cross = VGroup(
                Line(LEFT*.065+DOWN*.065, RIGHT*.065+UP*.065,
                     color=RED, stroke_width=4),
                Line(LEFT*.065+UP*.065, RIGHT*.065+DOWN*.065,
                     color=RED, stroke_width=4)).move_to(axes.c2p(a, 0))
            poles.add(cross, MathTex(str(a), color=RED, font_size=27)
                      .next_to(cross, DOWN, buff=.14))
        origin_label = MathTex('0', font_size=25).next_to(origin, DOWN+LEFT, buff=.1)
        pole_note = Text('红叉：函数的两个极点', font_size=22, color=RED)
        pole_note.move_to([-3.8, -2.78, 0])
        path_label = MathTex(r'z=r e^{i\pi/4},\quad r>0', font_size=29)
        path_label.move_to([-3.8, -3.15, 0])
        radius_circle = always_redraw(lambda: Circle(
            radius=r.get_value()*scale, color=BLUE, stroke_width=2.8).move_to(origin))

        def point():
            return axes.c2p(r.get_value()/math.sqrt(2), r.get_value()/math.sqrt(2))

        radial_line = always_redraw(lambda: Line(origin, point(), color=BLUE, stroke_width=3))
        moving_point = always_redraw(lambda: Dot(point(), radius=.075, color=BLUE))
        point_label = MathTex('z', font_size=29, color=BLUE)
        point_label.add_updater(lambda mob: mob.next_to(moving_point, UP+LEFT, buff=.1))
        radius_label = MathTex(r'r\approx', font_size=28).move_to([-6.1, 2.2, 0])
        radius_value = DecimalNumber(.3, num_decimal_places=2, font_size=28, color=BLUE)
        radius_value.next_to(radius_label, RIGHT, buff=.1)
        radius_value.add_updater(lambda mob: mob.set_value(r.get_value()))

        divider = Line([-.64, -2.8, 0], [-.64, 2.4, 0], color='#d6e0e8', stroke_width=1.5)

        def formula(text, y, color=INK, size=33):
            result = MathTex(text, font_size=size, color=color).move_to([3.35, y, 0])
            if result.width > 6.35:
                result.scale_to_fit_width(6.35)
            return result

        def card(heading, first, first_note, second, second_note, result):
            return VGroup(
                Text(heading, font_size=28, color=INK).move_to([3.35, 2.14, 0]),
                formula(first, 1.50, BLUE),
                Text(first_note, font_size=22, color=BLUE).move_to([3.35, .85, 0]),
                formula(second, .18, ORANGE),
                Text(second_note, font_size=22, color=ORANGE).move_to([3.35, -.48, 0]),
                formula(result, -2.58, size=31))

        inner = card('内域：两个分式都选非负幂',
                     r'\frac12\sum_{n=0}^{\infty}z^n', '公比 z，要求 |z| < 1',
                     r'-\frac16\sum_{n=0}^{\infty}\left(\frac z3\right)^n',
                     '公比 z/3，要求 |z| < 3',
                     r'f(z)=\frac13+\frac49z+\frac{13}{27}z^2+\cdots')
        middle = card('中间环域：一部分负幂，一部分非负幂',
                      r'-\frac12\sum_{n=0}^{\infty}z^{-n-1}', '公比 1/z，要求 |z| > 1',
                      r'-\frac16\sum_{n=0}^{\infty}\left(\frac z3\right)^n',
                      '公比 z/3，要求 |z| < 3',
                      r'1<|z|<3')
        outer = card('外域：两个分式都选负幂',
                     r'-\frac12\sum_{n=0}^{\infty}z^{-n-1}', '公比 1/z，要求 |z| > 1',
                     r'\frac12\sum_{n=0}^{\infty}3^n z^{-n-1}',
                     '公比 3/z，要求 |z| > 3',
                     r'f(z)=\frac1{z^2}+\frac4{z^3}+\frac{13}{z^4}+\cdots')

        def boundary(radius, first, second, result):
            return VGroup(
                Text('边界：蓝点仍然不是极点', font_size=28, color=RED)
                    .move_to([3.35, 2.14, 0]),
                formula(first, 1.50, size=32),
                Text('函数在这个点有定义', font_size=24).move_to([3.35, .85, 0]),
                formula(second, .18, RED, size=33),
                Text('通项不趋于零，相关级数发散', font_size=23, color=RED)
                    .move_to([3.35, -.48, 0]),
                formula(result, -2.58, RED, size=31))

        boundary1 = boundary(1, r'z=e^{i\pi/4}\ne 1,3',
                             r'\left|\frac12z^n\right|=\frac12\not\to0',
                             r'|z|=1:\quad |z|=|1/z|=1')
        boundary3 = boundary(3, r'z=3e^{i\pi/4}\ne 1,3',
                             r'\left|\frac16(z/3)^n\right|=\frac16\not\to0',
                             r'|z|=3:\quad |z/3|=|3/z|=1')
        cases = VGroup(inner, boundary1, middle, boundary3, outer)

        def region_index():
            value = r.get_value()
            if abs(value-1) < 1e-7:
                return 1
            if abs(value-3) < 1e-7:
                return 3
            return 0 if value < 1 else 2 if value < 3 else 4

        def update_cases(_):
            index = region_index()
            for j, item in enumerate(cases):
                item.set_opacity(1 if j == index else 0)

        def update_regions(_):
            index = region_index()
            for j, shape in enumerate(regions):
                active = (index == 0 and j == 0) or (index == 2 and j == 1) or (index == 4 and j == 2)
                shape.set_fill(opacity=.14 if active else 0)

        cases.add_updater(update_cases)
        regions.add_updater(update_regions)
        update_cases(None)
        update_regions(None)

        def ratio1():
            return r.get_value() if r.get_value() <= 1 else 1/r.get_value()

        def ratio3():
            return r.get_value()/3 if r.get_value() <= 3 else 3/r.get_value()

        meters = VGroup()
        for label, y, ratio, color in [('1', -.98, ratio1, BLUE), ('3', -1.54, ratio3, ORANGE)]:
            prefix = MathTex(r'|q_{'+label+r'}|', font_size=25, color=color).move_to([.68, y, 0])
            track = Line([1.33, y, 0], [5.5, y, 0], color='#dae4eb', stroke_width=9)
            bar = always_redraw(lambda y=y, ratio=ratio, color=color: Line(
                [1.33, y, 0], [1.33+4.17*ratio(), y, 0], color=color, stroke_width=9))
            limit = Line([5.5, y-.16, 0], [5.5, y+.16, 0], color=RED, stroke_width=2)
            value = DecimalNumber(ratio(), num_decimal_places=2, font_size=25, color=color)
            value.move_to([6.04, y, 0])
            value.add_updater(lambda mob, ratio=ratio: mob.set_value(ratio()))
            meters.add(prefix, track, bar, limit, value)
        meter_note = Text('公比模长读数为近似值；红线为 1', font_size=20, color=LINE)
        meter_note.move_to([3.35, -2.0, 0])
        meter_group = VGroup(meters, meter_note)

        footers = VGroup(*[
            Text(text, font_size=24, color=color).move_to([0, -3.56, 0])
            for text, color in [
                ('|z| < 1：两个收敛条件的交集', INK),
                ('边界圆经过极点 1；蓝点不在极点上，级数仍然发散', RED),
                ('1 < |z| < 3：左侧分式换成负幂，右侧分式保留非负幂', INK),
                ('边界圆经过极点 3；函数有定义不保证这一展开收敛', RED),
                ('|z| > 3：外域向外无限延伸，图中只画出有限一段', INK)]])
        footers.add_updater(lambda _: [item.set_opacity(1 if j == region_index() else 0)
                                      for j, item in enumerate(footers)])

        self.add(title, definition, regions, axes, limits, ray, poles, origin_label,
                 radius_circle, radial_line, moving_point, point_label, pole_note,
                 path_label, radius_label, radius_value, divider, cases,
                 meter_group, footers)
        with media_step(self, 'S1'):
            self.play(r.animate.set_value(.82), run_time=12, rate_func=linear)
            self.wait(1)
        with media_step(self, 'S2'):
            self.play(r.animate.set_value(1), run_time=7, rate_func=linear)
            self.play(Indicate(moving_point, color=BLUE), Indicate(poles[0], color=RED), run_time=2)
            self.wait(3)
        with media_step(self, 'S3'):
            self.play(r.animate.set_value(2.62), run_time=13, rate_func=linear)
            self.wait(1)
        with media_step(self, 'S4'):
            self.play(r.animate.set_value(3), run_time=7, rate_func=linear)
            self.play(Indicate(moving_point, color=BLUE), Indicate(poles[2], color=RED), run_time=2)
            self.wait(3)
        with media_step(self, 'S5'):
            self.play(r.animate.set_value(3.9), run_time=8, rate_func=linear)
            cancellation = formula(r'-\frac1{2z}+\frac1{2z}=0', -1.13, size=38)
            cancellation_note = Text('两个 z 的负一次幂项相消', font_size=23, color=INK)
            cancellation_note.move_to([3.35, -1.77, 0])
            self.play(FadeOut(meter_group), FadeIn(cancellation),
                      FadeIn(cancellation_note), r.animate.set_value(4.04), run_time=3)
            self.play(Circumscribe(cancellation, color=ORANGE),
                      Circumscribe(outer[-1], color=BLUE), run_time=2)
            check = formula(r'\lim_{z\to\infty}z^2f(z)=1', -1.13, size=36)
            self.play(ReplacementTransform(cancellation, check),
                      r.animate.set_value(4.14), run_time=2)
            self.wait(1)
