"""Build exact vector diagrams for parameter-pole and principal-value examples."""
from pathlib import Path
import math
import argparse

HEAD='<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="470" viewBox="0 0 1200 470"><defs><marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10" fill="#497992"/></marker></defs><rect width="1200" height="470" fill="#f7fafc"/><g font-family="PingFang SC,Microsoft YaHei,sans-serif" fill="#20364d">'
def text(x,y,s,size=24,color='#20364d',anchor='middle'):return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" text-anchor="{anchor}">{s}</text>'
def build(out):
 out=Path(out);out.mkdir(parents=True,exist_ok=True);parts=[HEAD]
 for i,a in enumerate([1.1,1.25,1.6]):
  cx=150+400*i;cy=220;scale=60;alpha=a-math.sqrt(a*a-1);beta=a+math.sqrt(a*a-1)
  parts.extend([text(cx+20,45,['两极点在圆内','极点落在路径上','只剩一个内极点'][i],27),text(cx+20,85,['a = 1.10','a = 1.25 = 5/4','a = 1.60'][i]),f'<circle cx="{cx}" cy="{cy}" r="120" fill="none" stroke="#267b9d" stroke-width="3"/>',f'<path d="M{cx-140} {cy}H{cx+200}" stroke="#8096a8" stroke-width="2" marker-end="url(#arr)"/>',f'<path d="M{cx} 360V95" stroke="#8096a8" stroke-width="2"/>',f'<path d="M{cx+22} 102H{cx-22}" stroke="#267b9d" stroke-width="3" marker-end="url(#arr)"/>',text(cx,247,'0',20),text(cx+120,247,'2',20),text(cx-90,125,'|z| = 2',21)])
  for x,c,label,dy in [(alpha,'#267b9d','α',-17),(beta,'#b74445' if i==1 else '#c9804b','β',65)]:
   xp=cx+x*scale;parts.extend([f'<circle cx="{xp:.5f}" cy="{cy}" r="6" fill="{c}"/>',text(xp,cy+dy,label,27,c)])
  parts.append(text(cx+20,402,['两个留数相消，积分为 0','普通围道积分未定义','仅计算 α 的留数'][i],23,'#b74445' if i==1 else '#20364d'))
 parts.append('</g></svg>');(out/'poles.svg').write_text(''.join(parts),encoding='utf-8')
 cx=330;cy=330;sc=60;eps=.55;left=cx+sc*(1-eps);right=cx+sc*(1+eps);rr=eps*sc
 p=[HEAD.replace('width="1200" height="470" viewBox="0 0 1200 470"','width="1000" height="470" viewBox="0 0 1000 470"').replace('width="1200" height="470" fill','width="1000" height="470" fill'),text(500,42,'主值围道：实轴两段与上侧小凹口',28),f'<path d="M65 {cy}H600M{cx} 365V65" stroke="#8b9bab" stroke-width="2"/>',f'<path d="M570 {cy}A240 240 0 0 0 90 {cy}" fill="none" stroke="#267b9d" stroke-width="3"/>',f'<path d="M90 {cy}H{left}M{right} {cy}H570" stroke="#267b9d" stroke-width="3"/>',f'<path d="M{left} {cy}A{rr} {rr} 0 0 1 {right} {cy}" fill="none" stroke="#c9804b" stroke-width="4"/>',*[f'<path d="{d}" fill="none" stroke="#497992" stroke-width="3" marker-end="url(#arr)"/>' for d in ("M350 90H310",f"M165 {cy}H210",f"M485 {cy}H530",f"M377 {cy-rr+1}H403")],f'<circle cx="390" cy="{cy}" r="6" fill="white" stroke="#b74445" stroke-width="3"/><circle cx="330" cy="210" r="6" fill="#267b9d"/>',text(390,365,'1',24,'#b74445'),text(300,215,'2i',24,'#267b9d'),text(90,365,'−R',22),text(570,365,'R',22),text(705,125,'实轴两段：向右',23,anchor='start'),text(705,175,'大半圆：逆时针',23,anchor='start'),text(705,225,'小凹口：顺时针',23,'#c9804b',anchor='start'),text(705,285,'实轴极点不在围道内',22,'#b74445',anchor='start'),text(500,425,'有限图示 R=4、ε=0.55；最终分别取 R→∞ 与 ε→0',23),'</g></svg>']
 (out/'pv.svg').write_text(''.join(p),encoding='utf-8')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args();build(a.out)
