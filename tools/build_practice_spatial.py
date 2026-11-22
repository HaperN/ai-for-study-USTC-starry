"""Build an offline, actually interactive pair of complex-analysis spatial figures.

Only writes the new page and its named assets. Existing textbook data is never read
or changed. ``bindings`` maps ``sphere`` / ``log`` to question IDs.
"""
from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
import shutil
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
RESOURCE = ROOT / 'src/aiforstudy/resources/practice'
VENDOR = ROOT / 'blocks/render/vendor'


def _tex(value: str, block: bool = True) -> str:
    cls = 'spatial-equation' if block else 'spatial-inline-math'
    return f'<div class="{cls}" data-tex="{html.escape(value, quote=True)}">{html.escape(value)}</div>'


def _svg(kind: str) -> str:
    """Orthographic projection of actual model coordinates, entirely vector paths."""
    scale = 100 if kind == 'sphere' else 48
    cy = 285 if kind == 'sphere' else 266
    azimuth, elevation = .78, .50

    def project(p):
        x, y, z = p
        return (350+scale*(math.cos(azimuth)*x-math.sin(azimuth)*y),
                cy-scale*(math.cos(elevation)*z-math.sin(elevation)*(math.sin(azimuth)*x+math.cos(azimuth)*y)))

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 700 490" role="img" aria-labelledby="title desc"><title id="title">{("黎曼球面立体投影" if kind == "sphere" else "连续对数的螺旋曲面")}：静态矢量对照</title><desc id="desc">这是静态示意图；动态网页另提供参数控制与旋转。图中曲线来自明确的三维参数坐标。</desc><rect width="700" height="490" fill="#f2f6f6"/>']

    def path(points, stroke='#4c8e92', opacity=.6, width=1.1, fill='none', dash=False):
        coords = [project(p) for p in points]
        d = 'M'+' L'.join(f'{x:.2f},{y:.2f}' for x,y in coords)
        parts.append(f'<path d="{d}" stroke="{stroke}" stroke-width="{width}" opacity="{opacity}" fill="{fill}"'+(' stroke-dasharray="6 4"' if dash else '')+'/>')

    def text(label, p, color='#254c50', dx=10, dy=-10):
        x,y = project(p)
        parts.append(f'<text x="{x+dx:.2f}" y="{y+dy:.2f}" fill="{color}" font-size="17" font-family="system-ui,sans-serif">{html.escape(label)}</text>')

    def dot(p, color, size=5):
        x,y=project(p)
        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{size}" fill="{color}"/>')

    base = 0 if kind == 'sphere' else -2*math.pi*.36-.45
    plane = [(-2.6,-2.6,base),(2.6,-2.6,base),(2.6,2.6,base),(-2.6,2.6,base),(-2.6,-2.6,base)]
    path(plane, '#a6c9c7',.42,1,'#cfe3df')
    for i in range(-2,3):
        path([(-2.6,i,base),(2.6,i,base)],'#bad1d0',.6)
        path([(i,-2.6,base),(i,2.6,base)],'#bad1d0',.6)
    path([(-2.6,0,base),(2.6,0,base)],'#607d80',.8)
    path([(0,-2.6,base),(0,2.6,base)],'#607d80',.8)
    text('Re z',(2.6,0,base),dx=0,dy=22)
    text('Im z',(0,2.6,base),dx=-30,dy=22)
    if kind == 'sphere':
        center_x, center_y = project((0,0,1))
        parts.append(f'<circle cx="{center_x:.2f}" cy="{center_y:.2f}" r="{scale}" fill="#a1cecd" fill-opacity=".18" stroke="#5c9699"/>')
        for latitude in [-math.pi/3,-math.pi/6,0,math.pi/6,math.pi/3]:
            path([(math.cos(latitude)*math.cos(i*math.tau/160), math.cos(latitude)*math.sin(i*math.tau/160), 1+math.sin(latitude)) for i in range(161)])
        for j in range(8):
            path([(math.cos(i*math.tau/160)*math.cos(j*math.pi/8), math.cos(i*math.tau/160)*math.sin(j*math.pi/8), 1+math.sin(i*math.tau/160)) for i in range(161)],opacity=.4)
        r,t=1.7,.3
        x,y=r*math.cos(t),r*math.sin(t)
        q=(4*x/(r*r+4),4*y/(r*r+4),2*r*r/(r*r+4))
        path([(0,0,2),(x,y,0)],'#d17a41',1,2)
        dot((0,0,2),'#205961');text('N：∞',(0,0,2),dx=-55,dy=-17)
        dot((0,0,0),'#487d80');text('S：z = 0',(0,0,0),dx=-22,dy=26)
        dot((x,y,0),'#204ebc');text('z（有限点）',(x,y,0),'#204ebc',dx=7,dy=25)
        dot(q,'#df733a',6);text('Q(z)',q,'#a24e22')
        text('赤道：|z| = 2',(-1.1,0,1),dx=-120,dy=-4)
        parts.append('<text x="24" y="34" fill="#25565b" font-size="19" font-family="system-ui,sans-serif">r = 1.7：投影点与北极仍有正距离</text>')
    else:
        for j in range(120):
            t0=-math.tau+3*math.tau*j/120;t1=-math.tau+3*math.tau*(j+1)/120
            path([(.3*math.cos(t0),.3*math.sin(t0),t0*.36),(2.4*math.cos(t0),2.4*math.sin(t0),t0*.36),(2.4*math.cos(t1),2.4*math.sin(t1),t1*.36),(.3*math.cos(t1),.3*math.sin(t1),t1*.36)],'none',.17,0,'#4a999e')
        for r in [.3,.65,1,1.35,1.7,2.05,2.4]:
            path([(r*math.cos(t),r*math.sin(t),t*.36) for t in [-math.tau+3*math.tau*i/240 for i in range(241)]],opacity=.65)
        path([(0,0,base),(0,0,4*math.pi*.36+.3)],'#486c70',.8)
        for n in range(-2,5):text('0' if n==0 else f'{n}π',(0,0,n*math.pi*.36),dx=-30,dy=5)
        t=2*math.pi+math.pi/4;r=1.25;x,y=r*math.cos(t),r*math.sin(t)
        path([(x,y,base),(x,y,t*.36)],'#d17a41',1,1.8,dash=True)
        dot((x,y,base),'#204ebc');text('同一 z',(x,y,base),'#204ebc',dy=23)
        dot((x,y,t*.36),'#df733a',6);text('θ = 9π/4',(x,y,t*.36),'#a24e22')
        dot((x,y,(t-math.tau)*.36),'#df733a',5);text('θ = π/4',(x,y,(t-math.tau)*.36),'#a24e22')
        path([(-2.6,0,base),(0,0,base)],'#be4a44',1,2,dash=True)
        parts.append('<text x="24" y="30" fill="#25565b" font-size="18" font-family="system-ui,sans-serif">相同输入 z，连续辐角相差 2π</text><text x="24" y="55" fill="#516c70" font-size="14" font-family="system-ui,sans-serif">高度 = Im log z；Re log z = ln r 单列于读数</text>')
    parts.append('</svg>')
    return '\n'.join(parts)


def _controls(kind):
    sphere = kind == 'sphere'
    turn = '' if sphere else '<button type="button" data-action="turn">绕一圈 +2π</button>'
    return f'''<div class="spatial-controls" hidden>
<label class="spatial-slider"><span class="spatial-slider-title"><span>模 r</span><output data-label="radius">{'1.400' if sphere else '1.250'}</output></span><input aria-label="{'球面' if sphere else '对数'}：模 r" type="range" min="{'0' if sphere else '0.3'}" max="{'12' if sphere else '2.4'}" step="0.01" value="{'1.4' if sphere else '1.25'}" data-parameter="radius"></label>
<label class="spatial-slider"><span class="spatial-slider-title"><span>{'方向 θ' if sphere else '连续辐角 θ'}</span><output data-label="theta">0.250π</output></span><input aria-label="{'球面' if sphere else '对数'}：辐角 θ" type="range" min="{-1 if sphere else -2}" max="{1 if sphere else 4}" step="0.005" value="0.25" data-parameter="theta"></label>
<div class="spatial-buttons"><button type="button" data-action="play">播放</button><button type="button" data-action="pause" disabled>暂停</button><button type="button" data-action="reset">重置</button>{turn}</div></div>
<p class="spatial-status" role="status" aria-live="polite" data-status>三维组件尚未启动；下方保留静态矢量图。</p>'''


def _links(kind, bindings):
    links = ''.join(f'<a href="完整版-习题集.html#practice-{quote(ident, safe="")}">{html.escape(ident)}</a>' for ident in bindings[kind])
    if not links:
        links = '<span>适用：' + ('扩充复平面、模与辐角、无穷远点' if kind=='sphere' else '多值对数、主支、连续辐角与绕数') + '</span>'
    return f'<footer class="spatial-footer"><strong>关联练习</strong>{links}<a href="assets/practice-spatial-{kind}.svg" target="_blank" rel="noopener">打开静态矢量图</a></footer>'


def _figure(kind, description, legend):
    return f'''<figure class="spatial-figure"><div class="spatial-viewport" data-canvas-host hidden></div><figure class="spatial-fallback" data-fallback><img src="assets/practice-spatial-{kind}.svg" alt="{description}"><figcaption>静态矢量对照图；不提供旋转或播放。启用 JavaScript 且支持 WebGL 时，显示交互三维画面。</figcaption></figure><figcaption class="spatial-caption"><p data-window-note>{description}</p><div class="spatial-legend">{legend}</div><p class="spatial-interaction-hint" hidden>拖拽旋转；滚轮缩放。键盘：画面获焦后用方向键旋转，＋／−缩放，Home 恢复视角。</p></figcaption></figure>'''


def build_spatial(out: Path | str, bindings: dict | None = None) -> dict:
    """Return build metadata after writing a standalone offline page and assets."""
    bindings = bindings or {}
    normalized = {}
    for kind in ('sphere','log'):
        values = bindings.get(kind, [])
        if not isinstance(values, (list, tuple)) or any(not isinstance(x,str) or not x for x in values):
            raise ValueError('spatial bindings must contain lists of nonempty question IDs')
        normalized[kind] = list(dict.fromkeys(values))
    out = Path(out)
    assets = out / 'assets'
    assets.mkdir(parents=True,exist_ok=True)
    for name in ('practice-spatial.js','practice-spatial.css'):
        shutil.copy2(RESOURCE/name,assets/name)
    shutil.copy2(VENDOR/'mathbox/three.min.js',assets/'practice-three.min.js')
    for name in ('katex.min.js','katex.min.css'):
        if not (assets/name).exists():shutil.copy2(VENDOR/name,assets/name)
    shutil.copytree(VENDOR/'fonts',assets/'fonts',dirs_exist_ok=True)
    for kind in ('sphere','log'):
        (assets/f'practice-spatial-{kind}.svg').write_text(_svg(kind),encoding='utf-8')
    sphere_formula=_tex(r'Q(z)=\left(\frac{4x}{r^2+4},\frac{4y}{r^2+4},\frac{2r^2}{r^2+4}\right)')
    sphere_inverse=_tex(r'z=\frac{2\xi}{2-\zeta}+i\frac{2\eta}{2-\zeta}')
    sphere_limit=_tex(r'|Q-N|=\frac{4}{\sqrt{r^2+4}}\longrightarrow 0')
    log_formula=_tex(r'(r\cos\theta,\ r\sin\theta,\ \theta)')
    log_value=_tex(r'w=\ln r+i\theta,\qquad r>0')
    page=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>复变函数 A · 三维图解</title><link rel="stylesheet" href="assets/katex.min.css"><link rel="stylesheet" href="assets/practice-spatial.css"></head>
<body><main class="spatial-shell"><nav class="spatial-topnav"><a href="index.html">返回学习首页</a><a href="#sphere">黎曼球面</a><a href="#log">复对数曲面</a></nav><header class="spatial-head"><span class="spatial-kicker">复变函数 A · 空间与变化</span><h1>把抽象关系转过来看</h1><p>先拖拽看清空间位置，再改变参数。支持 WebGL 时，两幅图均可离线旋转与播放，公式、运行时和静态后备图都保存在本地。</p></header>
<section class="spatial-card" id="sphere" data-spatial="sphere"><header class="spatial-card-header"><span class="spatial-number">01 / 扩充复平面</span><h2>有限点怎样趋近无穷远点</h2><p>采用本教材的球面：球心 (0, 0, 1)，半径 1，北极 N = (0, 0, 2)。复平面与球面相切于南极；北极到平面点的直线给出立体投影。</p></header><div class="spatial-body">
{_figure('sphere','蓝点是复平面中的 z；橙点是球面上的 Q(z)。','<span class="blue">复平面点 z</span><span class="orange">球面点 Q(z)</span><span>北极 N：∞</span>')}
<div class="spatial-panel">{_controls('sphere')}<div class="spatial-readings" hidden><span class="spatial-reading-title">当前参数与空间坐标</span><p data-value="input"></p><p data-value="output"></p><p data-value="distance"></p></div>{sphere_formula}<p class="spatial-note">这里 z = x + iy，r² = x² + y²。球面坐标依次是 (ξ, η, ζ)，满足 ξ² + η² + (ζ − 1)² = 1；复平面位于 ζ = 0。<strong>|z| = 2 对应赤道 ζ = 1。</strong></p>{sphere_inverse}<p class="spatial-note">上式为逆投影，适用于 Q ≠ N。</p>{sphere_limit}<p class="spatial-note"><strong>有限 r 永远不等于 ∞。</strong>动画只演示 r ≤ 12 的有限点。北极 N = (0, 0, 2) 单独对应无穷远点；南极 (0, 0, 0) 对应 z = 0。方形平面只是显示窗口。</p></div></div>{_links('sphere',normalized)}</section>
<section class="spatial-card" id="log" data-spatial="log"><header class="spatial-card-header"><span class="spatial-number">02 / 辐角与分支</span><h2>绕一圈，为什么没有回到同一个对数值</h2><p>保持模不变，让输入绕原点一圈。平面中的 z 回到原处，连续跟踪的辐角却增加 2π；螺旋曲面把这次变化显出来。</p></header><div class="spatial-body">
{_figure('log','蓝点显示输入 z；橙点高度跟踪连续辐角 θ。','<span class="blue">输入 z</span><span class="orange">连续对数</span><span class="red">主支割线（负实轴）</span>')}
<div class="spatial-panel">{_controls('log')}<div class="spatial-readings" hidden><span class="spatial-reading-title">输入、连续值与解析主支</span><p data-value="input"></p><p data-value="output"></p><p data-value="principal"></p></div>{log_formula}{log_value}<p class="spatial-note"><strong>竖轴只表示 Im w = θ。</strong>Re w = ln r 在读数中单列。这不是把完整的四维复值函数图像塞进三维。竖向显示比例为 0.36，刻度仍按 θ 标注；底面只用于显示输入 z。</p><p class="spatial-note"><strong>图中仅绘制：</strong>0.3 ≤ r ≤ 2.4、−2π ≤ θ ≤ 4π。数学上 r &gt; 0、连续 θ ∈ ℝ，可继续延伸；显示三层不表示只有三个对数值。</p><p class="spatial-note"><strong>本页的解析主支约定：</strong>−π &lt; Arg z &lt; π，定义域为 ℂ ∖ (−∞, 0]。负实轴不属于该解析主支。若教材给负实轴规定主值 π，那是边界取值约定，不等同于把割线纳入解析域。连续分支仍可沿绕行路径跨过割线。</p></div></div>{_links('log',normalized)}</section>
<aside class="spatial-endnote"><p>两幅图的用途分别是观察立体投影的极限位置，以及区分同一输入与不同连续对数值。它们帮助建立图像，不代替证明与定义域检查。</p><p>播放默认关闭；暂停、隐藏页面、离开页面或将图解移出视野都会停止动画。打印时自动使用静态矢量图。无 WebGL 时会明确提示并显示静态图。</p></aside><noscript><p>JavaScript 未启用：当前只有静态矢量图，不能播放或旋转。</p></noscript></main><script src="assets/katex.min.js"></script><script src="assets/practice-three.min.js"></script><script src="assets/practice-spatial.js"></script></body></html>'''
    (out/'三维图解.html').write_text(page,encoding='utf-8')
    manifest = {
        'schema':1,'runtime':'local Three.js r137','page':'三维图解.html',
        'scenes': {
            'sphere':{'question_ids':normalized['sphere'],'necessity':'把无穷远点作为球面北极，并展示有限点趋近它的动态过程；单幅平面图难以同时说明投影线、球面点与极限。','model':'(4x/(r²+4),4y/(r²+4),2r²/(r²+4))','textbook_unit':'CH01-S01-SS05','sphere_center':[0,0,1],'sphere_radius':1,'north_pole':[0,0,2],'inverse':'z=2ξ/(2−ζ)+i2η/(2−ζ)','equator_input_radius':2,'finite_display_radius':[0,12],'infinity':'Only the separate north-pole label denotes infinity.'},
            'log':{'question_ids':normalized['log'],'necessity':'同一 z 的闭合绕行与连续对数值的非闭合变化需要动画和可旋转曲面区分。','model':'(r cos θ,r sin θ,θ), with Re w=ln r in the readout','domain':'r>0; analytic principal branch C \\ (-infinity,0], -pi<Arg<pi','display_vertical_scale':.36,'display_radius':[.3,2.4],'display_theta_pi':[-2,4],'continuous_theta_domain':'all real numbers','limitation':'Shows the argument covering surface, not the complete four-dimensional graph.'},
        },
        'fallback':'Real SVG vector paths; never represented as playable animation.',
    }
    (assets/'practice-spatial-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return {'page':'三维图解.html','scenes':2,'assets':[str(p.relative_to(out)) for p in sorted(assets.glob('practice-*'))], 'bindings':normalized}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out',type=Path)
    parser.add_argument('--bindings',type=Path,help='JSON mapping sphere/log to question ID lists')
    args=parser.parse_args()
    print(json.dumps(build_spatial(args.out,json.loads(args.bindings.read_text()) if args.bindings else None),ensure_ascii=False,indent=2))
