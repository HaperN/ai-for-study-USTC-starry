from manim import *
from aifs_media_runtime import media_step
import math
import numpy as np
INK='#20364d';BLUE='#267b9d';ORANGE='#c9804b';RED='#b74445';LINE='#748a9e'
class GeneratedScene(Scene):
    def construct(self):
        title=Text('参数改变时，先看极点有没有越过路径',font_size=36).to_edge(UP,buff=.25)
        a=ValueTracker(1.06)
        ax=Axes(x_range=[-2.4,4.6,1],y_range=[-2.5,2.5,1],x_length=6.1,y_length=6.1*5/7,tips=False,axis_config={'color':LINE,'stroke_width':2}).move_to(LEFT*3.55+DOWN*.15)
        scale=np.linalg.norm(ax.c2p(1,0)-ax.c2p(0,0))
        contour=Circle(radius=2*scale,color=BLUE,stroke_width=3).move_to(ax.c2p(0,0))
        arrow=Arrow(ax.c2p(.35,1.97),ax.c2p(-.35,1.97),buff=0,color=BLUE,stroke_width=3)
        labs=VGroup(MathTex('0',font_size=26).next_to(ax.c2p(0,0),DOWN,buff=.1),MathTex('2',font_size=26).next_to(ax.c2p(2,0),DOWN,buff=.1),MathTex(r'|z|=2',font_size=30).next_to(contour,UP,buff=.12))
        def alpha():return a.get_value()-math.sqrt(a.get_value()**2-1)
        def beta():return a.get_value()+math.sqrt(a.get_value()**2-1)
        ad=always_redraw(lambda:Dot(ax.c2p(alpha(),0),radius=.075,color=BLUE))
        bd=always_redraw(lambda:Dot(ax.c2p(beta(),0),radius=.075,color=RED if abs(a.get_value()-1.25)<1e-7 else ORANGE))
        al=MathTex(r'\alpha',font_size=31,color=BLUE);bl=MathTex(r'\beta',font_size=31,color=ORANGE)
        al.add_updater(lambda m:m.next_to(ad,UP,buff=.12));bl.add_updater(lambda m:m.next_to(bd,DOWN,buff=.6))
        defs=VGroup(MathTex(r'\alpha=a-\sqrt{a^2-1}',font_size=32,color=BLUE),MathTex(r'\beta=a+\sqrt{a^2-1}',font_size=32,color=ORANGE),MathTex(r'\alpha\beta=1,\quad 0<\alpha<1',font_size=30)).arrange(DOWN,buff=.23).move_to([3.65,1.6,0])
        av=DecimalNumber(a.get_value(),num_decimal_places=3,font_size=31,color=INK).move_to([4,2.7,0]);av.add_updater(lambda m:m.set_value(a.get_value()))
        ap=MathTex(r'a\approx',font_size=31).next_to(av,LEFT,buff=.15)
        cases=[VGroup(Text('两极点都在圆内',font_size=27),MathTex(r'1<a<\frac54,\quad I=0',font_size=34)).arrange(DOWN,buff=.17),VGroup(Text('路径经过极点：题目排除此值',font_size=24,color=RED),MathTex(r'a=\frac54,\quad\beta=2',font_size=34,color=RED),Text('普通围道积分未定义',font_size=27,color=RED)).arrange(DOWN,buff=.16),VGroup(Text('只有左侧极点留在圆内',font_size=27),MathTex(r'a>\frac54,\quad I=-\frac{\pi i}{\sqrt{a^2-1}}',font_size=34)).arrange(DOWN,buff=.17)]
        for c in cases:c.move_to([3.6,-.8,0])
        def update_cases(_):
            v=a.get_value();index=1 if abs(v-1.25)<1e-7 else 0 if v<1.25 else 2
            for i,c in enumerate(cases):c.set_opacity(1 if i==index else 0)
        case_group=VGroup(*cases);case_group.add_updater(update_cases);update_cases(None)
        formula=MathTex(r'I(a)=\oint_{|z|=2}\frac{dz}{(z-\alpha)(z-\beta)}',font_size=35).move_to([0,-3.15,0])
        self.add(title,ax,contour,arrow,labs,ad,bd,al,bl,defs,av,ap,case_group,formula)
        with media_step(self,'S1'):
            self.play(a.animate.set_value(1.12),run_time=5,rate_func=linear)
            self.wait(1)
        with media_step(self,'S2'):
            self.play(a.animate.set_value(1.23),run_time=8,rate_func=linear)
            self.wait(1)
        with media_step(self,'S3'):
            self.play(a.animate.set_value(1.25),run_time=4,rate_func=linear)
            self.wait(3)
        with media_step(self,'S4'):
            self.play(a.animate.set_value(1.8),run_time=9,rate_func=linear)
            self.wait(1)
