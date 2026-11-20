from manim import *
from aifs_media_runtime import media_step
import numpy as np
import math

INK='#20364d';BLUE='#267b9d';ORANGE='#c9804b';WASH='#cde8ef';LINE='#748a9e'
class GeneratedScene(Scene):
    def construct(self):
        title=Text('一个点怎样经过两次复合映射',font_size=38).to_edge(UP,buff=.28)
        note=Text('先确定边界，再跟踪同一点的像',font_size=26,color=LINE).next_to(title,DOWN,buff=.12)
        zax=Axes(x_range=[0,4.5,1],y_range=[-2.2,2.2,1],x_length=3.8,y_length=3.8*4.4/4.5,tips=False,axis_config={'color':LINE,'stroke_width':2}).move_to(LEFT*4.65+DOWN*.1)
        uax=Axes(x_range=[0,.75,.25],y_range=[-.36,.36,.18],x_length=3.8,y_length=3.648,tips=False,axis_config={'color':LINE,'stroke_width':2}).move_to(DOWN*.1)
        wax=Axes(x_range=[0,2.6,1],y_range=[0,2.6,1],x_length=3.6,y_length=3.6,tips=False,axis_config={'color':LINE,'stroke_width':2}).move_to(RIGHT*4.65+DOWN*.1)
        zscale=np.linalg.norm(zax.c2p(1,0)-zax.c2p(0,0))
        outer=Circle(radius=2*zscale,color=BLUE,stroke_width=3).move_to(zax.c2p(2,0))
        inner=Circle(radius=zscale,color=ORANGE,stroke_width=3).move_to(zax.c2p(1,0))
        region=Difference(outer,inner,color=WASH,fill_opacity=.85,stroke_width=0)
        zl=VGroup(Text('原域 z',font_size=30).move_to([-4.65,2.25,0]),MathTex('0',font_size=26).next_to(zax.c2p(0,0),DOWN+LEFT,buff=.08),MathTex('2',font_size=26).next_to(zax.c2p(2,0),DOWN,buff=.08),MathTex('4',font_size=26).next_to(zax.c2p(4,0),DOWN,buff=.08))
        strip=Polygon(uax.c2p(.25,-.36),uax.c2p(.5,-.36),uax.c2p(.5,.36),uax.c2p(.25,.36),fill_color=WASH,fill_opacity=.85,stroke_width=0)
        limits=VGroup(DashedLine(uax.c2p(.25,-.36),uax.c2p(.25,.36),color=BLUE),DashedLine(uax.c2p(.5,-.36),uax.c2p(.5,.36),color=ORANGE))
        ul=VGroup(Text('倒数平面 u',font_size=30).move_to([0,2.25,0]),MathTex(r'\frac14',font_size=28).next_to(uax.c2p(.25,0),DOWN,buff=.12),MathTex(r'\frac12',font_size=28).next_to(uax.c2p(.5,0),DOWN,buff=.12))
        wedge=Polygon(wax.c2p(0,0),wax.c2p(2.6,0),wax.c2p(2.6,2.6),wax.c2p(2.6/math.sqrt(3),2.6),fill_color=WASH,fill_opacity=.85,stroke_width=0)
        rays=VGroup(Arrow(wax.c2p(0,0),wax.c2p(2.52,0),buff=0,color=BLUE,stroke_width=3),Arrow(wax.c2p(0,0),wax.c2p(1.44,1.44*math.sqrt(3)),buff=0,color=ORANGE,stroke_width=3))
        wl=VGroup(Text('像平面 w',font_size=30).move_to([4.65,2.25,0]),MathTex(r'\pi/3',font_size=28).move_to(wax.c2p(.72,.75)),Text('向外无限延伸',font_size=24,color=LINE).move_to([4.65,-2.4,0]))
        zgroup=VGroup(region,zax,outer,inner,zl);ugroup=VGroup(strip,uax,limits,ul,Text('上下无限延伸',font_size=24,color=LINE).move_to([0,-2.4,0]));wgroup=VGroup(wedge,wax,rays,wl)
        phi=ValueTracker(0);mix=ValueTracker(0)
        def uv():
            v=3/8+.1*math.cos(phi.get_value())+.18j*math.sin(phi.get_value())
            return (1-mix.get_value())*v+mix.get_value()/3
        def zv():return 1/uv()
        def wv():return np.exp((4j*math.pi/3)*(uv()-.25))
        zd=always_redraw(lambda:Dot(zax.c2p(zv().real,zv().imag),radius=.065,color='#126b8b'))
        ud=always_redraw(lambda:Dot(uax.c2p(uv().real,uv().imag),radius=.065,color='#126b8b'))
        wd=always_redraw(lambda:Dot(wax.c2p(wv().real,wv().imag),radius=.065,color='#126b8b'))
        formulas=[MathTex(x,font_size=34).move_to([0,-3.18,0]) for x in [r'|z-2|<2,\quad |z-1|>1',r'u=\frac1z,\qquad \frac14<\operatorname{Re}u<\frac12',r'w=\exp\!\left[\frac{4\pi i}{3}\left(u-\frac14\right)\right],\quad 0<\arg w<\frac\pi3',r'z=3\ \longmapsto\ u=\frac13\ \longmapsto\ w=e^{i\pi/9}']]
        for f in formulas:
            if f.width>12.6:f.scale_to_fit_width(12.6)
        self.add(title,note,zgroup,zd,formulas[0])
        with media_step(self,'S1'):
            self.play(phi.animate.set_value(TAU),run_time=7,rate_func=linear)
            self.wait(.6)
        with media_step(self,'S2'):
            self.play(FadeIn(ugroup),FadeIn(ud),ReplacementTransform(formulas[0],formulas[1]),run_time=1)
            self.play(phi.animate.set_value(2*TAU),run_time=7,rate_func=linear)
        with media_step(self,'S3'):
            self.play(FadeIn(wgroup),FadeIn(wd),ReplacementTransform(formulas[1],formulas[2]),run_time=1)
            self.play(phi.animate.set_value(3*TAU),run_time=7,rate_func=linear)
        with media_step(self,'S4'):
            self.play(mix.animate.set_value(1),ReplacementTransform(formulas[2],formulas[3]),run_time=4)
            self.wait(2)
