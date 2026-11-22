"""Track an interior point through the textbook's arc/wedge/log-strip maps.

The approved lesson supplies the formulas. Rendering parameters are explicit
context inputs, never inferred from a screenshot. The encoded three-step
timeline is the original narration timeline. No geometric domain morph is
used: every displayed moving point is an analytically computed image.
"""
from manim import *
from aifs_media_runtime import load_context, media_step
import math
import numpy as np

INK = '#20364d'
BLUE = '#267b9d'
ORANGE = '#c9804b'
GREEN = '#3d806a'
PURPLE = '#943e98'
LINE = '#8c9baa'
WASH = '#dcebf4'


class GeneratedScene(Scene):
    def construct(self):
        context = load_context()
        params = context['render_parameters']
        a = float(params['a'])
        alpha = float(params['alpha'])
        beta = float(params['beta'])
        h = float(params['h'])
        if not (a > 0 and h > 0 and 0 <= alpha < beta <= math.pi):
            raise ValueError('the textbook requires a,h>0 and 0<=alpha<beta<=pi')
        theta0, theta1 = math.pi+alpha, math.pi+beta
        span = beta-alpha
        factor = h/span
        phase = ValueTracker(0)
        log_radius_amplitude = .95
        angle_amplitude_fraction = .44

        def input_point(phi):
            log_radius = log_radius_amplitude*math.cos(phi)
            theta = (theta0+theta1)/2+angle_amplitude_fraction*span*math.sin(phi)
            target = np.exp(log_radius+1j*theta)
            return a*(target+1)/(target-1)

        def values():
            z = input_point(phase.get_value())
            t = (z+a)/(z-a)
            theta = math.atan2(t.imag, t.real)
            if theta <= 0:
                theta += 2*math.pi
            u = complex(math.log(abs(t)), theta)
            w = factor*(u-1j*theta0)
            return {'z': z, 't': t, 'u': u, 'w': w}

        def mapping(z):
            return (z+a)/(z-a)

        def source_arc(theta):
            ts = np.exp(np.linspace(-8, 8, 400)+1j*theta)
            middle = [a*(t+1)/(t-1) for t in ts]
            return [-a+0j, *middle, a+0j]

        arcs = [source_arc(theta0), source_arc(theta1)]
        all_z = [z for arc in arcs for z in arc]
        xmin = min(z.real for z in all_z)-.13*a
        xmax = max(z.real for z in all_z)+.13*a
        ymax = max(z.imag for z in all_z)+.2*a
        z_bounds = (xmin, xmax, -.2*a, ymax)
        ray_radius = 3.15
        ray_points = [ray_radius*np.exp(1j*theta) for theta in np.linspace(theta0, theta1, 100)]
        t_bounds = (min(z.real for z in ray_points)-.28,
                    max(.9, max(z.real for z in ray_points)+.28),
                    min(z.imag for z in ray_points)-.25, .65)
        u_bounds = (-1.35, 1.35, theta0-.3, theta1+.3)
        common_x = max(4.1, factor*1.25)
        shared_bounds = (-common_x, common_x, -.35, theta1+.45)

        title = Text('同一点怎样从圆月牙域到水平条带', font_size=35)
        title.to_edge(UP, buff=.22)
        parameter_note = Text(params['display_label'], font_size=21, color=LINE)
        parameter_note.to_edge(DOWN, buff=.14)
        persistent = VGroup(title, parameter_note)
        self.add(persistent)

        def tex(source, position, size=34, color=INK, max_width=12.6):
            item = MathTex(source, font_size=size, color=color).move_to(position)
            if item.width > max_width:
                item.scale_to_fit_width(max_width)
            return item

        def pane(name, center_x, bounds, kind, shared_scale=False):
            x0, x1, y0, y1 = bounds
            scale = min(5.35/(x1-x0), 3.75/(y1-y0))
            cx, cy = (x0+x1)/2, (y0+y1)/2
            center = np.array([center_x, -.05, 0])

            def point(z):
                return center+scale*np.array([z.real-cx, z.imag-cy, 0])

            def path(points, color, width=3):
                result = VMobject(color=color, stroke_width=width)
                result.set_points_as_corners([point(z) for z in points])
                return result

            objects = VGroup()
            # A single scale is used for both real and imaginary coordinates.
            if x0 <= 0 <= x1:
                objects.add(Line(point(complex(0, y0)), point(complex(0, y1)), color=LINE, stroke_width=1.5))
            if y0 <= 0 <= y1:
                objects.add(Line(point(complex(x0, 0)), point(complex(x1, 0)), color=LINE, stroke_width=1.5))
            heading = Text(name+' 平面', font_size=30).move_to([center_x, 2.22, 0])
            objects.add(heading)

            if kind == 'arc':
                fill = Polygon(*[point(z) for z in arcs[0]], *[point(z) for z in reversed(arcs[1])],
                               fill_color=WASH, fill_opacity=.7, stroke_width=0)
                objects.add_to_back(fill)
                for index, (theta, arc, color, label) in enumerate(zip((theta0, theta1), arcs, (BLUE, ORANGE), ('\\alpha', '\\beta'))):
                    objects.add(path(arc, color))
                    top = a*(np.exp(1j*theta)+1)/(np.exp(1j*theta)-1)
                    objects.add(tex(label, point(top)+(DOWN if index == 0 else UP)*.2, 26, color, 1.0))
                for z, label in [(-a+0j, '-a'), (a+0j, 'a')]:
                    endpoint = Circle(radius=.055, fill_color=WHITE, fill_opacity=1, color=INK, stroke_width=2).move_to(point(z))
                    objects.add(endpoint, tex(label, point(z)+DOWN*.25, 28))
                objects.add(Text('两个端点和边界均不取', font_size=23, color=LINE).move_to([center_x, -2.48, 0]))

            elif kind == 'wedge':
                radius = ray_radius
                polygon = [0j]+[radius*np.exp(1j*theta) for theta in np.linspace(theta0, theta1, 100)]
                objects.add_to_back(Polygon(*[point(z) for z in polygon], fill_color=WASH, fill_opacity=.7, stroke_width=0))
                for theta, color, label, offset in [(theta0, BLUE, r'\pi+\alpha', UP*.24), (theta1, ORANGE, r'\pi+\beta', RIGHT*.43)]:
                    ray_end = radius*np.exp(1j*theta)
                    objects.add(Arrow(point(0j), point(ray_end), buff=0, color=color, stroke_width=3,
                                      max_tip_length_to_length_ratio=.04))
                    label_at = 1.55*np.exp(1j*theta)
                    objects.add(tex(label, point(label_at)+offset, 28, color, 1.9))
                cut = DashedLine(point(0j), point(complex(x1-.12, 0)), color=GREEN, stroke_width=3, dash_length=.07)
                objects.add(cut, Text('支割线', font_size=22, color=GREEN).next_to(cut, UP, buff=.15))
                zero = Circle(radius=.055, fill_color=WHITE, fill_opacity=1, color=INK, stroke_width=2).move_to(point(0j))
                objects.add(zero, tex('0', point(0j)+UP*.24, 25))
                objects.add(Text('射线向外无限延伸；原点不取', font_size=23, color=LINE).move_to([center_x, -2.48, 0]))

            elif kind == 'affine':
                for y in (0, 1, 2, 3, 4, 5):
                    if y0 <= y <= y1:
                        anchor = point(complex(0, y))
                        objects.add(Line(anchor+LEFT*.045, anchor+RIGHT*.045, color=LINE, stroke_width=1.5))
                        objects.add(tex(str(y), anchor+LEFT*.14, 18, LINE, .35))
                objects.add(Text('左右无限延伸；上下边界不取', font_size=23, color=LINE).move_to([center_x, -2.48, 0]))
            else:
                lower, upper = (theta0, theta1) if kind == 'u' else (0, h)
                shade = Polygon(point(complex(x0, lower)), point(complex(x1, lower)),
                                point(complex(x1, upper)), point(complex(x0, upper)),
                                fill_color=WASH, fill_opacity=.7, stroke_width=0)
                objects.add_to_back(shade)
                for level, color, label in [(lower, BLUE, r'\pi+\alpha' if kind == 'u' else '0'),
                                             (upper, ORANGE, r'\pi+\beta' if kind == 'u' else 'h')]:
                    objects.add(DoubleArrow(point(complex(x0, level)), point(complex(x1, level)),
                                            buff=0, color=color, stroke_width=3,
                                            max_tip_length_to_length_ratio=.025))
                    objects.add(tex(r'\operatorname{Im}'+name+'='+label,
                                    point(complex(x1, level))+UP*.23+LEFT*.9, 26, color, 2.6))
                if kind == 'w':
                    xarrow = x0+.2
                    measure = DoubleArrow(point(complex(xarrow, 0)), point(complex(xarrow, h)),
                                          buff=0, color=GREEN, stroke_width=3)
                    objects.add(measure, tex('h', measure.get_center()+LEFT*.19, 28, GREEN))
                if shared_scale:
                    for y in (0, 1, 2, 3, 4, 5):
                        if y0 <= y <= y1:
                            anchor = point(complex(0, y))
                            objects.add(Line(anchor+LEFT*.045, anchor+RIGHT*.045, color=LINE, stroke_width=1.5))
                            if y not in (lower, upper):
                                objects.add(tex(str(y), anchor+LEFT*.14, 18, LINE, .35))
                note = '左右无限延伸；上下边界不取'
                objects.add(Text(note, font_size=23, color=LINE).move_to([center_x, -2.48, 0]))
            return objects, point

        def animate_pair(step_id, left, right, formula, note, duration, target):
            left_name, left_kind, left_bounds = left
            right_name, right_kind, right_bounds = right
            left_group, left_point = pane(left_name, -3.62, left_bounds, left_kind, step_id == 'S3')
            right_group, right_point = pane(right_name, 3.62, right_bounds, right_kind, step_id == 'S3')
            equation = tex(formula, [0, 2.91, 0], 35)
            explanation = Text(note, font_size=24, color=INK).move_to([0, -3.05, 0])
            divider = Line([0, -2.3, 0], [0, 2.4, 0], color='#dce4ea', stroke_width=1.3)
            static = VGroup(left_group, right_group, equation, explanation, divider)
            first = always_redraw(lambda: Dot(left_point(values()[left_name]), radius=.075, color=PURPLE))
            second = always_redraw(lambda: Dot(right_point(values()[right_name]), radius=.075, color=PURPLE))
            first_trail = TracedPath(lambda: left_point(values()[left_name]), stroke_color=PURPLE, stroke_width=3)
            second_trail = TracedPath(lambda: right_point(values()[right_name]), stroke_color=PURPLE, stroke_width=3)
            first_label = tex(left_name, first.get_center()+UP*.18, 25, PURPLE)
            second_label = tex(right_name, second.get_center()+UP*.18, 25, PURPLE)
            first_label.add_updater(lambda item: item.next_to(first, UP, buff=.08))
            second_label.add_updater(lambda item: item.next_to(second, UP, buff=.08))
            dynamic = VGroup(first_trail, second_trail, first, second, first_label, second_label)
            self.add(static, dynamic)
            with media_step(self, step_id):
                self.play(phase.animate.set_value(target), run_time=duration, rate_func=linear)
            self.remove(static, dynamic)

        timeline = context['original_timeline']
        animate_pair('S1', ('z', 'arc', z_bounds), ('t', 'wedge', t_bounds),
                     r't=\frac{z+a}{z-a},\qquad -a\mapsto0,\quad a\mapsto\infty',
                     '紫点始终在域内；左右两条轨迹由同一个输入点产生',
                     timeline[0]['end']-timeline[0]['start'], TAU)
        animate_pair('S2', ('t', 'wedge', t_bounds), ('u', 'u', u_bounds),
                     r'u=\log t=\ln|t|+i\theta,\qquad 0<\theta=\arg t<2\pi',
                     '辐角成为高度；角域避开正实轴割线，所选对数分支连续',
                     timeline[1]['end']-timeline[1]['start'], 2*TAU)
        # A genuine family of affine maps, first a translation, then a positive
        # scale. The same scale on both axes and both panels makes the motion
        # of the two boundary heights directly measurable in encoded frames.
        shift = ValueTracker(0)
        scaling = ValueTracker(0)
        left_group, left_point = pane('u', -3.62, shared_bounds, 'u', True)
        right_group, right_point = pane('v / w', 3.62, shared_bounds, 'affine', True)
        x0, x1 = shared_bounds[:2]

        def scale_value():
            return 1+scaling.get_value()*(factor-1)

        def moving_value():
            return scale_value()*(values()['u']-1j*shift.get_value()*theta0)

        def moving_levels():
            lower = scale_value()*(1-shift.get_value())*theta0
            upper = scale_value()*(theta1-shift.get_value()*theta0)
            return lower, upper

        def moving_strip():
            lower, upper = moving_levels()
            strip = VGroup(Polygon(
                right_point(complex(x0, lower)), right_point(complex(x1, lower)),
                right_point(complex(x1, upper)), right_point(complex(x0, upper)),
                fill_color=WASH, fill_opacity=.7, stroke_width=0))
            for level, color in ((lower, BLUE), (upper, ORANGE)):
                strip.add(DoubleArrow(right_point(complex(x0, level)), right_point(complex(x1, level)),
                                      buff=0, color=color, stroke_width=3, max_tip_length_to_length_ratio=.025))
            return strip

        changing_strip = always_redraw(moving_strip)
        first = always_redraw(lambda: Dot(left_point(values()['u']), radius=.075, color=PURPLE))
        second = always_redraw(lambda: Dot(right_point(moving_value()), radius=.075, color=PURPLE))
        first_trail = TracedPath(lambda: left_point(values()['u']), stroke_color=PURPLE, stroke_width=3)
        label_u = tex('u', first.get_center()+UP*.2, 25, PURPLE)
        label_u.add_updater(lambda item: item.next_to(first, UP, buff=.08))
        label_v = tex(r'v_\lambda', second.get_center()+UP*.2, 25, PURPLE)
        label_v.add_updater(lambda item: item.next_to(second, UP, buff=.08))
        equation1 = tex(r'v_\lambda=u-i\lambda(\pi+\alpha),\qquad 0\le\lambda\le1', [0, 2.91, 0], 35)
        equation2 = tex(r'w_\mu=\left(1-\mu+\mu\frac h{\beta-\alpha}\right)v_1,\qquad 0\le\mu\le1', [0, 2.91, 0], 35)
        equation3 = tex(r'w=\frac h{\beta-\alpha}\left[u-i(\pi+\alpha)\right],\qquad 0<\operatorname{Im}w<h', [0, 2.91, 0], 34)
        note = Text('两平面单位刻度相同；右侧边界与紫点按当前仿射式一起移动', font_size=23).move_to([0, -3.05, 0])
        divider = Line([0, -2.3, 0], [0, 2.4, 0], color='#dce4ea', stroke_width=1.3)
        self.add(left_group, right_group, changing_strip, first_trail,
                 first, second, label_u, label_v, equation1, note, divider)
        with media_step(self, 'S3'):
            duration = round(timeline[2]['end']-timeline[2]['start'], 6)
            self.play(shift.animate.set_value(1), phase.animate.set_value(2*TAU+.3*TAU),
                      run_time=3.8, rate_func=linear)
            self.remove(equation1, label_v)
            label_scaled = tex(r'w_\mu', second.get_center()+UP*.2, 25, PURPLE)
            label_scaled.add_updater(lambda item: item.next_to(second, UP, buff=.08))
            self.add(equation2, label_scaled)
            self.play(scaling.animate.set_value(1), phase.animate.set_value(2*TAU+.6*TAU),
                      run_time=3.8, rate_func=linear)
            self.remove(equation2, label_scaled)
            final_trail = TracedPath(lambda: right_point(moving_value()), stroke_color=PURPLE, stroke_width=3)
            label_w = tex('w', second.get_center()+UP*.2, 25, PURPLE)
            label_w.add_updater(lambda item: item.next_to(second, UP, buff=.08))
            final_labels = VGroup(
                tex(r'\operatorname{Im}w=0', right_point(complex(x1, 0))+UP*.23+LEFT*.9, 26, BLUE, 2.6),
                tex(r'\operatorname{Im}w=h', right_point(complex(x1, h))+UP*.23+LEFT*.9, 26, ORANGE, 2.6))
            height = DoubleArrow(right_point(complex(x0+.3, 0)), right_point(complex(x0+.3, h)),
                                 buff=0, color=GREEN, stroke_width=3)
            height_label = tex('h', height.get_center()+LEFT*.19, 28, GREEN)
            self.add(equation3, final_trail, label_w, final_labels, height, height_label)
            self.play(phase.animate.set_value(3*TAU), run_time=round(duration-7.6, 6), rate_func=linear)
