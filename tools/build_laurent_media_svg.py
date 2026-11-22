"""Build the exact vector companion to the moving Laurent radius scene."""
from pathlib import Path
import argparse
import math
from xml.sax.saxutils import escape

INK = '#20364d'
BLUE = '#267b9d'
ORANGE = '#c9804b'
RED = '#b74445'
LINE = '#748a9e'


def text(x, y, value, size=24, color=INK, anchor='middle'):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
            f'text-anchor="{anchor}">{escape(value)}</text>')


def circle(x, y, radius, **attrs):
    fields = ' '.join(f'{key.replace("_", "-")}="{value}"' for key, value in attrs.items())
    return f'<circle cx="{x}" cy="{y}" r="{radius}" {fields}/>'


def build(output):
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" viewBox="0 0 1440 900" role="img" aria-labelledby="title desc">',
        '<title id="title">同一有理函数在三个圆域中的洛朗展开</title>',
        '<desc id="desc">f(z)=1/((z-1)(z-3))。原点到两个极点的距离一和三划出三个展开域。各图蓝点均在辐角四十五度的射线上，不是极点。</desc>',
        '<rect width="1440" height="900" fill="#f7fafc"/>',
        '<g font-family="Noto Sans CJK SC,PingFang SC,Microsoft YaHei,sans-serif">',
        text(720, 47, '半径跨过奇点，洛朗展开的幂次随之改变', 34),
        text(720, 91, 'f(z) = 1 / [(z−1)(z−3)] = −1 / [2(z−1)] + 1 / [2(z−3)]', 26),
    ]
    for index, radius in enumerate((.7, 2, 3.8)):
        left = 24+472*index
        center = left+224
        cy = 342
        scale = 42
        parts.append(f'<rect x="{left}" y="120" width="448" height="597" rx="18" fill="white" stroke="#d6e0e8"/>')
        parts.append(text(center, 165, ('内域：|z| < 1', '中间环域：1 < |z| < 3', '外域：|z| > 3')[index], 28))
        if index == 0:
            parts.append(circle(center, cy, scale, fill=BLUE, fill_opacity='.13'))
        else:
            outer = (3 if index == 1 else 4.15)*scale
            inner = (1 if index == 1 else 3)*scale
            parts.append(circle(center, cy, (outer+inner)/2, fill='none', stroke=ORANGE if index == 1 else BLUE,
                                stroke_width=outer-inner, stroke_opacity='.13'))
        parts.append(f'<path d="M{center-184} {cy}H{center+188}M{center} {cy+177}V{cy-177}" stroke="{LINE}" stroke-width="1.5"/>')
        for a in (1, 3):
            parts.append(circle(center, cy, a*scale, fill='none', stroke=LINE, stroke_width='2', stroke_dasharray='6 5'))
            px = center+a*scale
            parts.append(f'<path d="M{px-5} {cy-5}L{px+5} {cy+5}M{px-5} {cy+5}L{px+5} {cy-5}" stroke="{RED}" stroke-width="4"/>')
            parts.append(text(px, cy+29, str(a), 23, RED))
        parts.append(text(center-14, cy+26, '0', 23))
        angle_end = 4.15*scale/math.sqrt(2)
        parts.append(f'<path d="M{center} {cy}L{center+angle_end} {cy-angle_end}" stroke="{LINE}" stroke-dasharray="5 5" stroke-width="1.5"/>')
        delta = radius*scale/math.sqrt(2)
        parts.append(circle(center, cy, radius*scale, fill='none', stroke=BLUE, stroke_width='3'))
        parts.append(f'<path d="M{center} {cy}L{center+delta} {cy-delta}" stroke="{BLUE}" stroke-width="3"/>')
        parts.append(circle(center+delta, cy-delta, 7, fill=BLUE))
        parts.append(text(center+delta-15, cy-delta-14, 'z', 25, BLUE))
        parts.append(text(center, 538, 'z = r exp(iπ/4)，图示 r = '+str(radius), 21, LINE))
        rows = [
            ('½ (1 + z + z² + ⋯)', '−⅙ (1 + z/3 + z²/9 + ⋯)', 'f = ⅓ + 4z/9 + 13z²/27 + ⋯'),
            ('−½ (z⁻¹ + z⁻² + z⁻³ + ⋯)', '−⅙ (1 + z/3 + z²/9 + ⋯)', '负幂与非负幂各自收敛'),
            ('−½ (z⁻¹ + z⁻² + z⁻³ + ⋯)', '½ (z⁻¹ + 3z⁻² + 9z⁻³ + ⋯)', 'f = z⁻² + 4z⁻³ + 13z⁻⁴ + ⋯'),
        ][index]
        parts.extend([text(center, 585, rows[0], 24, BLUE), text(center, 626, rows[1], 24, ORANGE),
                      text(center, 683, rows[2], 24)])
    parts.extend([
        text(720, 763, '红叉是极点；两条虚线圆是围绕 0 展开时的边界。外域向外无限延伸。', 25),
        text(720, 811, '在 z = exp(iπ/4) 和 z = 3exp(iπ/4)，函数有定义，但相邻展开的通项不趋于零。', 25, RED),
        text(720, 859, '外域的 z⁻¹ 项：−1/(2z) + 1/(2z) = 0；最低非零项为 z⁻²，系数为 1。', 25),
        '</g></svg>',
    ])
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(''.join(parts)+'\n', encoding='utf-8')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    print(build(parser.parse_args().out))
