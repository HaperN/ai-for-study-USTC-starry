"""Draw the reviewed figure-to-text exercises as genuine, inspectable SVG.

This builder never changes questions or a media manifest.  Its binding is only
valid when the catalog has an accepted review for the exact current content and
the stem matches the frozen figure definition.  Candidates remain unbound.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aiforstudy.practice.catalog import content_digest, validate_catalog  # noqa: E402

BLUE = "#126d91"
INK = "#172d3c"
GRAY = "#788994"
PALE = "#e4f3f7"
ORANGE = "#97531a"
FONT = "'STIX Two Text','Cambria Math','Times New Roman','PingFang SC','Microsoft YaHei',serif"
NS = {"s": "http://www.w3.org/2000/svg"}


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def number(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def attributes(**attrs) -> str:
    return " ".join(f'{k.replace("_", "-")}="{html.escape(str(v), quote=True)}"' for k, v in attrs.items() if v is not None)


class SVG:
    def __init__(self, width, height, title, description):
        self.width, self.height = width, height
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">',
                      f'<title id="title">{html.escape(title)}</title><desc id="description">{html.escape(description)}</desc>',
                      '<defs><marker id="axis-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="#788994"/></marker>',
                      f'<marker id="domain-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="{BLUE}"/></marker>',
                      '<marker id="dimension-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 1L9 5L0 9" fill="none" stroke="#788994" stroke-width="1.3"/></marker></defs>',
                      f'<rect width="{width}" height="{height}" fill="white"/><g font-family="{html.escape(FONT, quote=True)}" fill="{INK}">']
        self.geometry = []

    def add(self, element, **attrs):
        self.parts.append(f'<{element} {attributes(**attrs)}/>')

    def text(self, x, y, text, size=27, anchor="start", color=INK, weight=None):
        self.parts.append(f'<text {attributes(x=number(x), y=number(y), font_size=size, text_anchor=anchor, fill=color, font_weight=weight)}>{html.escape(text)}</text>')

    def lines(self, x, y, lines, size=26, gap=37, color=INK):
        for i, line in enumerate(lines):
            self.text(x, y + gap * i, line, size=size, color=color)

    def path(self, points, color=BLUE, width=4, dash=None, end=None, start=None, **attrs):
        d = " ".join(("M" if i == 0 else "L") + number(x) + " " + number(y) for i, (x, y) in enumerate(points))
        self.add("path", d=d, fill="none", stroke=color, stroke_width=width, stroke_dasharray=dash, marker_end=end, marker_start=start, stroke_linecap="round", stroke_linejoin="round", **attrs)

    def finish(self, metadata):
        self.parts.append('</g><metadata id="figure-data">' + html.escape(json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))) + '</metadata></svg>')
        return "\n".join(self.parts) + "\n"


class Plot:
    def __init__(self, svg, panel, x, y, width, height, xrange, yrange):
        self.s, self.panel = svg, panel
        self.x, self.y, self.w, self.h = x, y, width, height
        self.xr, self.yr = xrange, yrange
        self.record = {"panel": panel, "box": [x, y, width, height], "xrange": list(xrange), "yrange": list(yrange), "segments": [], "endpoints": []}
        svg.geometry.append(self.record)

    def p(self, x, y):
        return (self.x + (x - self.xr[0]) / (self.xr[1] - self.xr[0]) * self.w,
                self.y + (self.yr[1] - y) / (self.yr[1] - self.yr[0]) * self.h)

    def line(self, points, role="signal", color=BLUE, width=4, dash=None, **attrs):
        self.s.path([self.p(*p) for p in points], color=color, width=width, dash=dash, data_panel=self.panel, data_role=role, **attrs)
        if role in ("signal", "boundary"):
            self.record["segments"].append({"role": role, "points": points})

    def point(self, x, y, closed=True):
        px, py = self.p(x, y)
        self.s.add("circle", cx=number(px), cy=number(py), r=5.7, fill=BLUE if closed else "white", stroke=BLUE, stroke_width=2.5,
                   data_panel=self.panel, data_role="endpoint", data_x=x, data_y=y, data_closed=str(closed).lower())
        self.record["endpoints"].append({"x": x, "y": y, "closed": closed})

    def axes(self, xticks, yticks, xlabel="t/T", ylabel="f/E"):
        self.line([(self.xr[0], 0), (self.xr[1], 0)], role="axis", color=GRAY, width=1.7, end="url(#axis-arrow)")
        self.line([(0, self.yr[0]), (0, self.yr[1])], role="axis", color=GRAY, width=1.7, end="url(#axis-arrow)")
        self.s.text(self.x + self.w + 8, self.p(0, 0)[1] + 8, xlabel, size=25)
        self.s.text(self.p(0, 0)[0] - 12, self.y - 14, ylabel, size=26, anchor="end")
        for value, label in xticks:
            x, y = self.p(value, 0)
            self.s.path([(x, y - 4), (x, y + 5)], color=GRAY, width=1.5)
            self.s.text(x, self.y + self.h + 31, label, size=25, anchor="middle")
        for value, label in yticks:
            x, y = self.p(0, value)
            self.s.path([(x - 5, y), (x + 4, y)], color=GRAY, width=1.5)
            self.s.text(x - 13, y + 8, label, size=25, anchor="end")

    def guide(self, x1, y1, x2, y2):
        self.line([(x1, y1), (x2, y2)], role="guide", color="#bac8ce", width=1.4, dash="5 6")

    def zero_before(self):
        self.line([(self.xr[0], 0), (0, 0)])


def separator(svg, y):
    svg.path([(35, y), (1085, y)], color="#dbe3e7", width=1)


def build_nonperiodic():
    svg = SVG(1120, 1900, "图9.8对应的五种非周期信号", "按本题文字定义绘制，采用右连续端点。第四问的3E/4和E/4是公开补充条件。")
    svg.text(35, 45, "图 9.8 · 五种非周期信号", 36, weight=600)
    svg.text(35, 85, "T,E>0；t<0 时均为 0。实心点取到，空心点不取；跳点取右侧值。", 27)
    starts = [120, 465, 810, 1155, 1500]
    titles = ["（1）延迟斜坡", "（2）延迟负阶跃", "（3）两个不相交矩形脉冲", "（4）等高下降阶梯", "（5）截断斜坡"]
    plots = []
    for i, start in enumerate(starts):
        svg.text(35, start + 27, titles[i], 30, weight=600)
        xr = [(-0.2, 3.1), (-0.2, 2.35), (-0.25, 4.35), (-0.25, 4.55), (-0.18, 1.8)][i]
        yr = [(-0.2, 2.35), (-1.3, 0.45), (-0.22, 1.27), (-0.2, 1.25), (-0.2, 1.25)][i]
        p = Plot(svg, f"nonperiodic-{i+1}", 135, start + 75, 550, 181, xr, yr)
        plots.append(p)
    p = plots[0]
    p.record["y_unit"] = "T/E; drawn y-coordinate v means f1/E=(T/E)*v"
    p.axes([(0,"0"),(1,"1"),(2,"2"),(3,"3")], [(1,"T/E"),(2,"2T/E")], ylabel="f₁/E")
    p.line([(-.2,0),(1,0)])
    p.line([(1,0),(3.05,2.05)], end="url(#domain-arrow)")
    p.point(1,0)
    svg.lines(790, starts[0]+105, ["令 u=t/T。", "u<1 时为 0；", "u≥1 时", "f₁/E=(T/E)(u−1)。"], size=26)
    svg.text(135, starts[0]+325, "纵刻度保留 T/E；未令 E=T。", 25, color=GRAY)
    p = plots[1]
    p.axes([(0,"0"),(1,"1"),(2,"2")], [(-1,"−1")], ylabel="f₂/E")
    p.guide(1,0,1,-1)
    p.line([(-.2,0),(1,0)])
    p.line([(1,-1),(2.3,-1)], end="url(#domain-arrow)")
    p.point(1,0,False); p.point(1,-1)
    svg.lines(790, starts[1]+105, ["u=t/T", "u<1：f₂/E=0", "u≥1：f₂/E=−1"])
    p = plots[2]
    a,b,c,d=.65,1.55,2.5,3.65
    p.record["illustrative_positions"] = {"a":a,"b":b,"c":c,"d":d,"numeric_spacing_is_not_a_question_condition":True}
    p.axes([(0,"0"),(a,"a"),(b,"b"),(c,"c"),(d,"d")], [(1,"1")], ylabel="f₃/E")
    for l,r,y in [(-.25,a,0),(a,b,1),(b,c,0),(c,d,1),(d,4.3,0)]: p.line([(l,y),(r,y)])
    for x,rval,lval in [(a,1,0),(b,0,1),(c,1,0),(d,0,1)]:
        p.guide(x,0,x,1); p.point(x,lval,False); p.point(x,rval)
    svg.lines(790, starts[2]+72, ["a=t₁/T", "b=(t₁+τ₁)/T", "c=t₂/T", "d=(t₂+τ₂)/T", "0≤a<b<c<d"], size=26, gap=38)
    svg.text(135, starts[2]+325, "[a,b)、[c,d) 上为 1，其余为 0；横向间距仅示意。", 25, color=GRAY)
    p=plots[3]
    p.axes([(0,"0"),(1,"1"),(2,"2"),(3,"3"),(4,"4")], [(1,"1"),(.75,"3/4"),(.5,"1/2"),(.25,"1/4")], ylabel="f₄/E")
    p.zero_before()
    p.point(0,0,False)
    for k in range(4):
        y=1-k/4
        p.line([(k,y),(k+1,y)]); p.point(k,y); p.point(k+1,y,False)
    p.line([(4,0),(4.5,0)]);p.point(4,0)
    svg.lines(790, starts[3]+92, ["u=t/T，k=0,1,2,3", "k≤u<k+1：", "f₄/E=1−k/4", "u≥4：f₄/E=0"], size=26)
    svg.text(135, starts[3]+325, "按本题补充条件绘制；3E/4、E/4 为公开补充值。", 25, color=ORANGE)
    p=plots[4]
    p.axes([(0,"0"),(1,"1")], [(1,"1")], ylabel="f₅/E")
    p.zero_before(); p.line([(0,0),(1,1)]); p.line([(1,0),(1.75,0)], end="url(#domain-arrow)")
    p.point(0,0); p.point(1,1,False); p.point(1,0)
    p.guide(0,1,1,1)
    svg.lines(790, starts[4]+105, ["u=t/T", "0≤u<1：f₅/E=u", "u≥1：f₅/E=0"])
    for y in [458,803,1148,1493]: separator(svg,y)
    svg.text(35, 1870, "端点约定来自本题文字定义；原图未使用空心／实心端点符号。", 25, color=GRAY)
    return svg


def build_periodic():
    svg=SVG(1120,1600,"图9.9对应的四种周期信号","所有信号在负时间为零，正时间按给定周期延拓。显示至少两个完整周期。")
    svg.text(35,45,"图 9.9 · 四种周期信号",36,weight=600)
    svg.text(35,85,"E,A,T,ω>0；0<τ<T；t<0 时为 0。实心点取到，空心点不取。",27)
    starts=[120,470,820,1170]
    titles=["（1）锯齿波", "（2）半波整流", "（3）分段直线", "（4）正弦波串"]
    plots=[]
    for i,start in enumerate(starts):
        svg.text(35,start+26,titles[i],30,weight=600)
        yr=[(-1.25,1.25),(-.18,1.25),(-.4,1.23),(-1.25,1.25)][i]
        p=Plot(svg,f"periodic-{i+1}",135,start+75,550,185,(-.12,2.35),yr)
        plots.append(p)
    p=plots[0]
    p.axes([(0,"0"),(1,"1"),(2,"2")],[(-1,"−1"),(1,"1")],ylabel="f₁/E")
    p.zero_before(); p.point(0,0,False)
    for k in range(3):
        stop=min(k+1,2.28)
        p.line([(k,1),(stop,1-2*(stop-k))])
        p.point(k,1)
        if k<2:p.point(k+1,-1,False)
    svg.lines(790,starts[0]+95,["u=t/T，q=u−⌊u⌋", "0≤q<1", "f₁/E=1−2q", "周期为 T。"],size=26)
    svg.text(135,starts[0]+330,"正整数 u 处由 −1 跳回 1，取右侧值 1。",25,color=GRAY)
    p=plots[1]
    p.axes([(0,"0"),(.5,"1/2"),(1,"1"),(1.5,"3/2"),(2,"2")],[(1,"1")],xlabel="t/P",ylabel="f₂/A")
    p.zero_before()
    for k in range(3):
        stop=min(k+.5,2.28)
        pts=[(k+(stop-k)*j/90,math.sin(2*math.pi*(stop-k)*j/90)) for j in range(91)]
        p.line(pts)
        if k<2:p.line([(k+.5,0),(k+1,0)])
    for x in [0,.5,1,1.5,2]:p.point(x,0)
    svg.lines(790,starts[1]+78,["P=2π/ω，u=t/P", "q=u−⌊u⌋", "0≤q<1/2：", "f₂/A=sin(2πq)", "1/2≤q<1：f₂/A=0"],size=25,gap=37)
    svg.text(135,starts[1]+330,"本问幅度是 A，周期是 P=2π/ω；未将 A 或 P 与 E、T 等同。",24,color=GRAY)
    p=plots[2]
    p.axes([(0,"0"),(.5,"1/2"),(1,"1"),(1.5,"3/2"),(2,"2")],[(1,"1"),(.8,"0.8"),(-.2,"−0.2")],ylabel="f₃/E")
    p.zero_before();p.point(0,0,False)
    for k in range(3):
        stop=min(k+.5,2.28)
        p.line([(k,1),(stop,1-.4*(stop-k))]);p.point(k,1)
        if k<2:
            p.line([(k+.5,-.2),(k+1,0)])
            p.point(k+.5,.8,False);p.point(k+.5,-.2);p.point(k+1,0,False)
    svg.lines(790,starts[2]+78,["u=t/T，q=u−⌊u⌋", "0≤q<1/2：", "f₃/E=1−2q/5", "1/2≤q<1：", "f₃/E=2(q−1)/5"],size=26,gap=37)
    svg.text(135,starts[2]+330,"前半周期 1→0.8；中点跳到 −0.2；后半周期趋向 0。",25,color=GRAY)
    p=plots[3];rho=2/3
    p.record["rho"]=rho
    p.axes([(0,"0"),(rho,"ρ"),(1,"1"),(1+rho,"1+ρ"),(2,"2")],[(-1,"−1"),(1,"1")],ylabel="f₄/E")
    p.zero_before()
    for k in range(3):
        stop=min(k+rho,2.28)
        pts=[(k+(stop-k)*j/120,math.sin(2*math.pi*(stop-k)*j/(rho*120))) for j in range(121)]
        p.line(pts)
        if k<2:p.line([(k+rho,0),(k+1,0)])
    for x in [0,rho,1,1+rho,2]:p.point(x,0)
    svg.lines(790,starts[3]+78,["u=t/T，q=u−⌊u⌋", "ρ=τ/T，0<ρ<1", "0≤q<ρ：", "f₄/E=sin(2πq/ρ)", "ρ≤q<1：f₄/E=0"],size=26,gap=37)
    svg.text(135,starts[3]+330,"每 T 重复一段完整正弦波；图示 ρ=2/3，仅用于展示。",25,color=GRAY)
    for y in [463,813,1163]:separator(svg,y)
    svg.text(35,1568,"各面板显示两个完整周期及下一周期的开头；跳跃点均取右侧值。",25,color=GRAY)
    return svg


def domain_dimension(p, a, b, label, dx=0,dy=0):
    p.line([a,b],role="dimension",color=GRAY,width=1.4,start="url(#dimension-arrow)",end="url(#dimension-arrow)")
    x,y=p.p((a[0]+b[0])/2,(a[1]+b[1])/2)
    p.s.text(x+dx,y+dy,label,size=27,anchor="middle")


def domain_fill(p, polygon):
    pixels=" ".join(number(x)+","+number(y) for x,y in (p.p(*q) for q in polygon))
    p.s.add("polygon",points=pixels,fill=PALE,stroke="none",data_panel=p.panel,data_role="domain-fill")
    p.record["fill_polygon"]=polygon


def boundary(p,points,start=False,end=False):
    p.line(points,role="boundary",width=3.8,dash="10 7",start="url(#domain-arrow)" if start else None,end="url(#domain-arrow)" if end else None)


def domain_label(p,x,y,text,dx=0,dy=0,anchor="start",size=26):
    xp,yp=p.p(x,y);p.s.text(xp+dx,yp+dy,text,size=size,anchor=anchor)


def build_domains():
    svg=SVG(1120,1390,"图8.26对应的两个开多边形域","蓝色阴影表示域内；虚线边界全部排除。边界箭头仅表示向无穷延伸，不表示积分方向。")
    svg.text(35,45,"图 8.26 · 两个开多边形域",36,weight=600)
    svg.text(35,85,"蓝色为域内；虚线边界均不含。箭头仅表示无限延伸，图边缘不是边界。",26)
    svg.text(35,140,"（1）直角转弯域 G₁",31,weight=600)
    p=Plot(svg,"domain-1",70,190,550,350,(-2.2,2.75),(-1.8,2.25))
    h,H=1.4,1.05
    p.record["illustrative_parameters"]={"h":h,"H":H}
    poly=[(-h,2.25),(0,2.25),(0,0),(2.75,0),(2.75,-H),(-h,-H)]
    domain_fill(p,poly)
    p.line([(-2.1,0),(2.68,0)],role="axis",color=GRAY,width=1.3,end="url(#axis-arrow)")
    p.line([(0,-1.7),(0,2.19)],role="axis",color=GRAY,width=1.3,end="url(#axis-arrow)")
    boundary(p,[(-h,-H),(-h,2.1)],end=True)
    boundary(p,[(-h,-H),(2.62,-H)],end=True)
    boundary(p,[(0,0),(2.62,0)],end=True)
    boundary(p,[(0,0),(0,2.1)],end=True)
    domain_dimension(p,(-h,1.2),(0,1.2),"h",dy=-14)
    domain_dimension(p,(1.2,-H),(1.2,0),"H",dx=26)
    domain_label(p,0,0,"O",dx=10,dy=-12)
    domain_label(p,2.7,0,"x",dy=30,anchor="end")
    domain_label(p,0,2.2,"y",dx=16,dy=5)
    domain_label(p,-h,-H,"(−h,−H)",dx=-5,dy=36)
    domain_label(p,-h/2,1.92,"+∞",dy=-15,anchor="middle")
    domain_label(p,2.47,-H/2,"+∞",dx=1,dy=8,anchor="middle")
    domain_label(p,-.67,-.62,"G₁",size=30,anchor="middle")
    svg.lines(715,195,["h,H>0；w=x+iy", "x>−h，y>−H", "并且 x<0 或 y<0。"],size=28,gap=42)
    svg.text(715,345,"排除的四条边界：",27,weight=600)
    svg.lines(715,390,["x=−h，y≥−H", "y=−H，x≥−h", "y=0，x≥0", "x=0，y≥0"],size=27,gap=42)
    svg.text(70,590,"向上、向右两个通道开放；长度只作示意，未固定 h/H。",25,color=GRAY)
    separator(svg,632)
    svg.text(35,690,"（2）三通道域 G₂",31,weight=600)
    p=Plot(svg,"domain-2",70,765,550,390,(-3.0,2.3),(-2.9,1.8))
    h1,h2,h3=.85,1.8,1.3
    low=h1-h2
    p.record["illustrative_parameters"]={"h1":h1,"h2":h2,"h3":h3}
    poly=[(-3,h1),(2.3,h1),(2.3,0),(0,0),(0,-2.9),(-h3,-2.9),(-h3,low),(-3,low)]
    domain_fill(p,poly)
    p.line([(-2.92,0),(2.23,0)],role="axis",color=GRAY,width=1.3,end="url(#axis-arrow)")
    p.line([(0,-2.8),(0,1.7)],role="axis",color=GRAY,width=1.3,end="url(#axis-arrow)")
    boundary(p,[(-2.86,h1),(2.15,h1)],start=True,end=True)
    boundary(p,[(0,0),(2.15,0)],end=True)
    boundary(p,[(0,0),(0,-2.78)],end=True)
    boundary(p,[(-h3,low),(-2.86,low)],end=True)
    boundary(p,[(-h3,low),(-h3,-2.78)],end=True)
    domain_dimension(p,(1.1,0),(1.1,h1),"h₁",dx=26)
    domain_dimension(p,(-2.3,low),(-2.3,h1),"h₂",dx=-29)
    domain_dimension(p,(-h3,-1.95),(0,-1.95),"h₃",dy=-15)
    domain_label(p,0,0,"O",dx=12,dy=30)
    domain_label(p,2.25,0,"x",dy=30,anchor="end")
    domain_label(p,0,1.72,"y",dx=16,dy=8)
    domain_label(p,-h3,low,"(−h₃,h₁−h₂)",dx=-12,dy=39,anchor="end",size=25)
    domain_label(p,-2.65,.55,"−∞",dx=-5,dy=8,anchor="middle")
    domain_label(p,2.06,.42,"+∞",dy=8,anchor="middle")
    domain_label(p,-h3/2,-2.61,"−∞",dy=13,anchor="middle")
    domain_label(p,-.65,-.5,"G₂",size=30,anchor="middle")
    svg.lines(715,765,["h₂>h₁>0，h₃>0", "y<h₁；", "x<0 或 y>0；", "x>−h₃ 或 y>h₁−h₂。"],size=27,gap=42)
    svg.text(715,967,"排除的边界：",27,weight=600)
    svg.lines(715,1012,["y=h₁（整条直线）", "y=0，x≥0", "x=0，y≤0", "y=h₁−h₂，x≤−h₃", "x=−h₃，y≤h₁−h₂"],size=26,gap=40)
    svg.text(70,1252,"向左、向右、向下三个通道开放；h₁−h₂<0。",26,color=GRAY)
    svg.text(35,1348,"域为严格不等式定义的开集；边界上的点（含拐角）均不属于域。",26,color=GRAY)
    return svg


def inside_polygon(x,y,polygon):
    inside=False
    for (x1,y1),(x2,y2) in zip(polygon,polygon[1:]+polygon[:1]):
        if (y1>y)!=(y2>y) and x<(x2-x1)*(y-y1)/(y2-y1)+x1:inside=not inside
    return inside


def check_math(svg_path):
    """Check emitted vector coordinates against formula samples and exclusions."""
    root=ET.parse(svg_path).getroot()
    meta=json.loads(root.find("s:metadata",NS).text)
    counts={"vector_points":0,"formula_points":0,"endpoints":0,"domain_samples":0,"excluded_boundary_samples":0}
    errors=[]
    def close(a,b,where):
        tolerance = 5.1e-5 if " pixel " in where else 2e-6
        if abs(a-b)>tolerance:errors.append(f"{where}: {a} != {b}")
    for p in meta["geometry"]:
        panel=p["panel"];bx,by,bw,bh=p["box"];xlo,xhi=p["xrange"];ylo,yhi=p["yrange"]
        actual=root.findall(f'.//s:path[@data-panel="{panel}"]',NS)
        actual=[q for q in actual if q.get("data-role") in ("signal","boundary")]
        if len(actual)!=len(p["segments"]):errors.append(panel+": missing plotted segments")
        for segment,node in zip(p["segments"],actual):
            pairs=node.get("d").replace("M","").replace("L","").split()
            pixels=list(zip(map(float,pairs[::2]),map(float,pairs[1::2])))
            for (x,y),(px,py) in zip(segment["points"],pixels):
                close(px,bx+(x-xlo)/(xhi-xlo)*bw,panel+" pixel x")
                close(py,by+(yhi-y)/(yhi-ylo)*bh,panel+" pixel y")
                counts["vector_points"]+=1
        circles=root.findall(f'.//s:circle[@data-panel="{panel}"]',NS)
        if len(circles)!=len(p["endpoints"]):errors.append(panel+": missing endpoint symbols")
        for endpoint,node in zip(p["endpoints"],circles):
            x,y=endpoint["x"],endpoint["y"]
            close(float(node.get("cx")),bx+(x-xlo)/(xhi-xlo)*bw,panel+" pixel endpoint x")
            close(float(node.get("cy")),by+(yhi-y)/(yhi-ylo)*bh,panel+" pixel endpoint y")
            if (node.get("fill")==BLUE)!=endpoint["closed"]:errors.append(panel+": wrong endpoint fill")
        if panel.startswith("domain"):
            polygon=root.find(f'.//s:polygon[@data-panel="{panel}"]',NS)
            actual_polygon=[tuple(map(float,point.split(','))) for point in polygon.get("points").split()]
            for (x,y),(px,py) in zip(p["fill_polygon"],actual_polygon):
                close(px,bx+(x-xlo)/(xhi-xlo)*bw,panel+" pixel fill x")
                close(py,by+(yhi-y)/(yhi-ylo)*bh,panel+" pixel fill y")
            par=p["illustrative_parameters"]
            if panel=="domain-1":
                h,H=par["h"],par["H"]
                predicate=lambda x,y:x>-h and y>-H and (x<0 or y<0)
                boundary_points=[(-h,-H),(-h,.3),(.4,-H),(0,0),(0,.4),(.4,0)]
            else:
                h1,h2,h3=par["h1"],par["h2"],par["h3"]
                predicate=lambda x,y:y<h1 and (x<0 or y>0) and (x>-h3 or y>h1-h2)
                boundary_points=[(-2,h1),(2,h1),(0,0),(.4,0),(0,-1),(-h3,h1-h2),(-2,h1-h2),(-h3,-2)]
            for i in range(47):
                for j in range(43):
                    x=xlo+(xhi-xlo)*(i+.319)/47;y=ylo+(yhi-ylo)*(j+.417)/43
                    if inside_polygon(x,y,p["fill_polygon"])!=predicate(x,y):errors.append(panel+": domain fill mismatch")
                    counts["domain_samples"]+=1
            for x,y in boundary_points:
                if predicate(x,y):errors.append(panel+": boundary included")
                counts["excluded_boundary_samples"]+=1
            continue
        def value(x):
            if x<0:return 0
            if panel=="nonperiodic-1":return max(x-1,0)
            if panel=="nonperiodic-2":return 0 if x<1 else -1
            if panel=="nonperiodic-3":
                v=p["illustrative_positions"]
                return int(v["a"]<=x<v["b"] or v["c"]<=x<v["d"])
            if panel=="nonperiodic-4":return 1-math.floor(x)/4 if x<4 else 0
            if panel=="nonperiodic-5":return x if x<1 else 0
            q=x-math.floor(x)
            if panel=="periodic-1":return 1-2*q
            if panel=="periodic-2":return math.sin(2*math.pi*q) if q<.5 else 0
            if panel=="periodic-3":return 1-.4*q if q<.5 else .4*(q-1)
            rho=p["rho"]
            return math.sin(2*math.pi*q/rho) if q<rho else 0
        for segment in p["segments"]:
            pts=segment["points"]
            if len(pts)==2:
                (x1,y1),(x2,y2)=pts
                for t in [.123,.5,.877]:
                    x=x1+(x2-x1)*t;y=y1+(y2-y1)*t
                    close(y,value(x),panel+" interior sample");counts["formula_points"]+=1
            else:
                for x,y in pts[1:-1]:
                    close(y,value(x),panel+" sampled curve");counts["formula_points"]+=1
        for endpoint in p["endpoints"]:
            x,y=endpoint["x"],endpoint["y"]
            if endpoint["closed"]:close(y,value(x),panel+" closed endpoint")
            elif abs(y-value(x))<1e-8:errors.append(panel+": an open endpoint equals the actual value")
            counts["endpoints"]+=1
    if root.findall('.//s:image',NS):errors.append("raster image embedded in SVG")
    if errors:raise ValueError("; ".join(errors[:20]))
    return {"file":svg_path.name,"status":"passed","checks":counts}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--catalog",type=Path)
    parser.add_argument("--sources",type=Path)
    parser.add_argument("--definitions",type=Path,default=ROOT/"examples/practice_source_svgs/source-definitions.json")
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    definitions=json.loads(args.definitions.read_text())
    sources={q["id"]:q for q in json.loads(args.sources.read_text())} if args.sources else {}
    catalog={q["id"]:q for q in json.loads(args.catalog.read_text())} if args.catalog else {}
    builders=[("fig-8-26.svg",build_domains),("fig-9-8.svg",build_nonperiodic),("fig-9-9.svg",build_periodic)]
    bindings=[];checks=[]
    for spec,(filename,builder) in zip(definitions,builders):
        if digest(spec["stem"])!=spec["stem_sha256"]:raise ValueError("definition hash mismatch: "+spec["question_id"])
        if spec["question_id"] in sources and sources[spec["question_id"]]["stem"]!=spec["stem"]:
            raise ValueError("source stem changed; inspect definitions before redrawing: "+spec["question_id"])
        q=catalog.get(spec["question_id"])
        binding={"question_id":spec["question_id"],"base_question_id":spec["base_question_id"],"figure":filename,"source_stem_sha256":spec["stem_sha256"],"catalog_content_sha256":None,"binding_status":"candidate_not_in_accepted_catalog","independent_figure_review":"pending"}
        if q:
            review=q.get("review",{})
            if q.get("stem")!=spec["stem"]:binding["binding_status"]="catalog_stem_changed_requires_figure_review"
            elif review.get("status")=="accepted" and review.get("issues")==[] and review.get("content_sha256")==content_digest(q):
                validate_catalog([q])
                binding["catalog_content_sha256"]=content_digest(q)
                binding["binding_status"]="bound_to_current_accepted_catalog_content"
            else:binding["binding_status"]="catalog_question_not_accepted"
        svg=builder()
        metadata={"schema":"source-practice-svg-v1","binding":binding,"source_definition":spec,"geometry":svg.geometry}
        path=args.out/filename;path.write_text(svg.finish(metadata),encoding="utf-8")
        binding["svg_sha256"]=digest(path.read_bytes())
        bindings.append(binding);checks.append(check_math(path))
    (args.out/"bindings.json").write_text(json.dumps(bindings,ensure_ascii=False,indent=2)+"\n")
    (args.out/"math-checks.json").write_text(json.dumps(checks,ensure_ascii=False,indent=2)+"\n")
    preview='<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><title>原图转写题静态图审查</title><style>body{margin:0;background:#eef1f3}main{max-width:1120px;margin:auto}figure{margin:24px 0;background:white}img{width:100%;display:block}@media print{@page{size:A4;margin:12mm}body{background:white}figure{margin:0;break-after:page}img{max-height:272mm;object-fit:contain;object-position:top}}</style><main>'
    preview+=''.join(f'<figure><img src="{name}" alt="{html.escape(spec["question_id"])}"></figure>' for spec,(name,_) in zip(definitions,builders))
    (args.out/"preview.html").write_text(preview+'</main></html>',encoding="utf-8")
    print(json.dumps({"output":str(args.out),"bindings":bindings,"math_checks":checks},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
