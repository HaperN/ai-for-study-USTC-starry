from manim import *
from aifs_media_runtime import media_step
import math
import numpy as np
INK='#20364d';BLUE='#267b9d';ORANGE='#c9804b';RED='#b74445';LINE='#748a9e'
class GeneratedScene(Scene):
    def construct(self):
        title=Text('小半圆缩小，为什么贡献不趋于零',font_size=37).to_edge(UP,buff=.25)
        eps=ValueTracker(.7);R=4
        ax=Axes(x_range=[-4.5,4.5,1],y_range=[-.6,4.5,1],x_length=6.4,y_length=6.4*5.1/9,tips=False,axis_config={'color':LINE,'stroke_width':2}).move_to(LEFT*3.55+DOWN*.12)
        big=ParametricFunction(lambda t:ax.c2p(R*math.cos(t),R*math.sin(t)),t_range=[0,PI],color=BLUE,stroke_width=3)
        outside_arrow=Arrow(ax.c2p(.45,3.97),ax.c2p(-.45,3.97),buff=0,color=BLUE,stroke_width=3)
        left=always_redraw(lambda:Line(ax.c2p(-R,0),ax.c2p(1-eps.get_value(),0),color=BLUE,stroke_width=3))
        right=always_redraw(lambda:Line(ax.c2p(1+eps.get_value(),0),ax.c2p(R,0),color=BLUE,stroke_width=3))
        cut=always_redraw(lambda:ParametricFunction(lambda t:ax.c2p(1+eps.get_value()*math.cos(t),eps.get_value()*math.sin(t)),t_range=[0,PI],color=ORANGE,stroke_width=4))
        cutarrow=always_redraw(lambda:Arrow(ax.c2p(1-.28*eps.get_value(),.97*eps.get_value()),ax.c2p(1+.28*eps.get_value(),.97*eps.get_value()),buff=0,color=ORANGE,stroke_width=3,max_tip_length_to_length_ratio=.3))
        arrows=VGroup(Arrow(ax.c2p(-2.8,0),ax.c2p(-2,0),buff=0,color=BLUE),Arrow(ax.c2p(2.4,0),ax.c2p(3.2,0),buff=0,color=BLUE))
        pole=Dot(ax.c2p(1,0),color=RED,radius=.065);up=Dot(ax.c2p(0,2),color=BLUE,radius=.065)
        labels=VGroup(MathTex('1',font_size=28,color=RED).next_to(pole,DOWN,buff=.12),MathTex('2i',font_size=29,color=BLUE).next_to(up,LEFT,buff=.13),MathTex('-R',font_size=26).next_to(ax.c2p(-R,0),DOWN,buff=.1),MathTex('R',font_size=26).next_to(ax.c2p(R,0),DOWN,buff=.1))
        expr=MathTex(r'g(z)=\frac{e^{2iz}}{(z^2+4)(z-1)}',font_size=34).move_to([3.5,2.1,0])
        ev=DecimalNumber(eps.get_value(),num_decimal_places=2,font_size=30,color=INK).move_to([4.1,2.85,0]);ev.add_updater(lambda m:m.set_value(eps.get_value()))
        el=MathTex(r'\varepsilon=',font_size=31).next_to(ev,LEFT,buff=.12)
        notes=[VGroup(Text('先写完整闭路积分',font_size=28),MathTex(r'I_{R,\varepsilon}+\int_{C_\varepsilon}g+\int_{C_R}g',font_size=28),MathTex(r'=2\pi i\,\mathrm{Res}_{2i}g',font_size=32)).arrange(DOWN,buff=.2),VGroup(Text('小凹口沿顺时针走',font_size=28,color=ORANGE),MathTex(r'z=1+\varepsilon e^{i\theta}',font_size=32),MathTex(r'\theta:\ \pi\longrightarrow0',font_size=32)).arrange(DOWN,buff=.2),VGroup(Text('长度变小，函数的模变大',font_size=26),MathTex(r'g(z)\,dz\sim\frac{c}{\varepsilon e^{i\theta}}i\varepsilon e^{i\theta}d\theta',font_size=29),MathTex(r'c=\frac{e^{2i}}5,\quad\int_\pi^0 ic\,d\theta=-i\pi c',font_size=29,color=ORANGE),MathTex(r'\lim_{\varepsilon\to0}\left|\int_{C_\varepsilon}g\right|=\frac\pi5',font_size=29)).arrange(DOWN,buff=.2),VGroup(Text('取极限、移项，再取虚部',font_size=26),MathTex(r'\mathrm{PV}\!\int g=2\pi i\,\mathrm{Res}_{2i}g',font_size=29),MathTex(r'+\ i\pi\,\mathrm{Res}_{1}g',font_size=31),MathTex(r'I=\frac\pi5(\cos2-e^{-4})',font_size=34,color=BLUE)).arrange(DOWN,buff=.18)]
        for n in notes:n.move_to([3.5,-.45,0])
        footer=Text('图示 R=4；结论取 R 趋于无穷、ε 趋于零',font_size=26,color=LINE).move_to([0,-3.15,0])
        if footer.width>12.8:footer.scale_to_fit_width(12.8)
        for n in notes:
            if n.width>5.8:n.scale_to_fit_width(5.8)
        self.add(title,ax,big,outside_arrow,left,right,cut,cutarrow,arrows,pole,up,labels,expr,ev,el,notes[0],footer)
        tracer=Dot(radius=.055,color=ORANGE)
        with media_step(self,'S1'):
            self.play(Create(big),run_time=2)
            self.wait(3)
        with media_step(self,'S2'):
            self.play(ReplacementTransform(notes[0],notes[1]),run_time=.6)
            self.add(tracer)
            self.play(UpdateFromAlphaFunc(tracer,lambda m,t:m.move_to(ax.c2p(1+.7*math.cos(PI*(1-t)),.7*math.sin(PI*(1-t))))),run_time=5,rate_func=linear)
            self.remove(tracer)
            self.wait(.8)
        with media_step(self,'S3'):
            self.play(ReplacementTransform(notes[1],notes[2]),run_time=.6)
            self.play(eps.animate.set_value(.08),run_time=7,rate_func=linear)
            self.wait(1)
        with media_step(self,'S4'):
            self.play(ReplacementTransform(notes[2],notes[3]),run_time=.8)
            self.wait(2)
            self.wait(3)
