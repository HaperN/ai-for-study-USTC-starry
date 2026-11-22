"""Draw requested mathematical figures; outputs remain drafts until reviewed."""
from pathlib import Path
import argparse,hashlib,json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
plt.rcParams.update({'font.family':'Arial Unicode MS','font.size':12,'axes.unicode_minus':False,'svg.fonttype':'path'})
BLUE='#177697';FILL='#d7edf3';GRAY='#78909c';RED='#ba4a5e'

def plane(ax,limits=(-3,3,-3,3)):
    ax.set(xlim=limits[:2],ylim=limits[2:]);ax.set_aspect('equal');ax.axhline(0,color=GRAY,lw=.7);ax.axvline(0,color=GRAY,lw=.7)
    ax.set_xlabel('Re z');ax.set_ylabel('Im z');ax.grid(alpha=.12)

def shade(ax,predicate,limits):
    x=np.linspace(*limits[:2],440);y=np.linspace(*limits[2:],440);X,Y=np.meshgrid(x,y)
    mask=predicate(X+1j*Y);ax.contourf(X,Y,mask.astype(float),levels=[.5,1.5],colors=[FILL])

def circle(ax,c,r,style='--',color=BLUE):
    ax.add_patch(Circle((complex(c).real,complex(c).imag),r,fill=False,ls=style,color=color,lw=1.6))

def point(ax,z,label,open=False):
    z=complex(z);ax.scatter([z.real],[z.imag],s=42,facecolors='white' if open else RED,edgecolors=RED,zorder=5)
    ax.annotate(label,(z.real,z.imag),xytext=(7,7),textcoords='offset points',fontsize=11)

def finish(fig,out,ident,caption):
    fig.text(.5,.015,caption,ha='center',va='bottom',fontsize=11,color='#40596a');fig.tight_layout(rect=(0,.045,1,1))
    svg=out/(ident+'.svg');png=out/(ident+'.png');fig.savefig(svg);fig.savefig(png,dpi=130);plt.close(fig)
    return {'source_file':str(svg),'preview_file':str(png),'relative_path':'assets/practice-'+ident+'.svg','sha256':hashlib.sha256(svg.read_bytes()).hexdigest(),'purpose':caption}

def mapping(out,ident,spec):
    pred,limits,curves,p,pt,title=spec
    fig,axes=plt.subplots(1,2,figsize=(12.5,5.7));a,b=axes;plane(a,limits);shade(a,pred,limits)
    for c,r in curves:circle(a,c,r)
    if '半圆' in title:
        if '上半圆' in title:a.plot([-curves[0][1],curves[0][1]],[0,0],'--',color=BLUE)
        else:a.plot([0,0],[-curves[0][1],curves[0][1]],'--',color=BLUE)
    a.set_title('原域 D：'+title)
    if p is not None:
        assert bool(pred(complex(p))),'normalization point outside domain'
        point(a,p,'指定内点')
    if ident=='FIT-A2024-Q3-2-V1':
        a.plot([1,1],[-np.sqrt(8),np.sqrt(8)],'--',color=BLUE)
        plane(b,(-2,2,-3,3));shade(b,lambda z:abs(z.real)<1,(-2,2,-3,3));b.axvline(-1,color=BLUE,ls='--');b.axvline(1,color=BLUE,ls='--');b.set_title(r'像域：$-1<\mathrm{Re}\,w<1$')
    else:
        plane(b,(-1.4,1.4,-1.4,1.4));shade(b,lambda z:abs(z)<1,(-1.4,1.4,-1.4,1.4));circle(b,0,1);b.set_title(r'像域：$|w|<1$')
        if p is not None:point(b,pt,'指定像点 '+{0:'0',1j/3:r'$i/3$',1j/4:r'$i/4$',1j/2:r'$i/2$'}[pt])
    b.set_xlabel('Re w');b.set_ylabel('Im w')
    return finish(fig,out,ident,'浅蓝为域内；虚线画出所用边界圆或直线，边界均不含。对应关系与单叶性见完整解析。')

def loci(out):
    fig,axs=plt.subplots(2,3,figsize=(15,10));axes=axs.flat
    a=next(axes);plane(a);a.axvline(0,color=BLUE,lw=2);point(a,-1,'a');point(a,1,'b');a.set_title('(1) a=-1, b=1：垂直平分线')
    a=next(axes);plane(a);t=np.linspace(0,2*np.pi,500);a.plot(2*np.cos(t),np.sqrt(3)*np.sin(t),color=BLUE);point(a,-1,'a');point(a,1,'b');a.set_title('(2) a=-1, b=1, R=4：椭圆')
    a=next(axes);plane(a);circle(a,1,1,'-');point(a,0,'z=0 不定义',True);a.set_title(r'(3) $\alpha=1/2$：圆去掉原点')
    a=next(axes);plane(a,(-2,2,-.5,2.5));r=np.logspace(-5,5,1200);w=r*np.exp(1j*np.pi/3);z=(1+w)/(1-w);a.plot(z.real,z.imag,color=BLUE);point(a,1,'1 不取',True);point(a,-1,'-1 不取',True);a.set_title(r'(4) $\alpha=\pi/3$：指定辐角的圆弧')
    a=next(axes);plane(a,(-4,2,-3,3));circle(a,-5/3,4/3,'-');a.set_title(r'(5) $\alpha=2$：阿波罗尼斯圆')
    a=next(axes);a.axis('off');a.text(0,.95,'参数图是示例，不能代替分类：\n\n(1) a=b 时为全平面。\n(2) a=b 时为圆，半径 R/2。\n(3) α=0 时为虚轴去掉原点；\n    α≠0 时为过原点的圆去掉原点。\n(4) z=±1 均不定义，辐角按题目约定；\n    α 为 π 的整数倍时须单列实轴部分。\n(5) α<0：空集；α=0：仅 z=1；\n    α=1：虚轴；其余 α>0：圆。',va='top',linespacing=1.8)
    return finish(fig,out,'TB-CH01-Q16','曲线的实线部分取到，空心点不取。给定的数值仅用于显示代表图形，完整参数分类见解析。')

def sets(out):
    fig,axs=plt.subplots(5,2,figsize=(12,24));L=(-3.5,3.5,-3.5,4.5)
    predicates=[lambda z:z.real<2,lambda z:z.imag>=3,lambda z:(abs(np.angle(z))<np.pi/4)&(abs(z)>0),
        lambda z:(np.angle(z)>np.pi/4)&(np.angle(z)<np.pi/3)&(abs(z)>1)&(abs(z)<2),
        lambda z:(np.angle(z-1j)>0)&(np.angle(z-1j)<np.pi/6)&(abs(z-1j)>0),
        lambda z:(abs(z+1)>2)&(abs(z+1)<3)&(z.real>-2)&(z.real<=1.5),
        lambda z:(abs(z)>2)&(abs(z-2)<2),lambda z:(z.imag>1)&(abs(z)<2),
        lambda z:(z.imag>-1)&(z.imag<=1),lambda z:(z.real<0)&(abs(z+1)>0)]
    titles=['Re z < 2','Im z ≥ 3',r'$|\arg z|<\pi/4$',r'$\pi/4<\arg z<\pi/3,\ 1<|z|<2$',r'$0<\arg(z-i)<\pi/6$',r'$2<|z+1|<3,\ -2<\mathrm{Re}\,z\leq3/2$',r'$|z|>2,\ |z-2|<2$',r'$\mathrm{Im}\,z>1,\ |z|<2$',r'$y_1<\mathrm{Im}\,z\leq y_2$；示例 $y_1=-1,y_2=1$',r'$|(z-1)/(z+1)|>1$']
    for n,(a,p,title) in enumerate(zip(axs.flat,predicates,titles),1):
        limits=(-.2,2.2,-.1,2.2) if n==4 else ((-.5,4.5,-2.5,2.5) if n==7 else L)
        plane(a,limits);shade(a,p,limits);a.set_title(f'({n}) '+title)
        if n==1:a.axvline(2,color=BLUE,ls='--')
        if n==2:a.axhline(3,color=BLUE)
        if n==3:
            for s in [-1,1]:a.plot([0,3.5],[0,s*3.5],'--',color=BLUE)
            point(a,0,'0 不取',True)
        if n==4:
            t=np.linspace(np.pi/4,np.pi/3,200)
            for r in [1,2]:a.plot(r*np.cos(t),r*np.sin(t),'--',color=BLUE)
            for t in [np.pi/4,np.pi/3]:a.plot(np.array([1,2])*np.cos(t),np.array([1,2])*np.sin(t),'--',color=BLUE)
        if n==5:
            a.plot([0,3.5],[1,1],'--',color=BLUE);a.plot([0,3.5],[1,1+3.5/np.sqrt(3)],'--',color=BLUE);point(a,1j,'i 不取',True)
        if n==6:
            circle(a,-1,2);circle(a,-1,3);a.plot([-2,-2],[-np.sqrt(8),np.sqrt(8)],'--',color=GRAY)
            a.plot([1.5,1.5],[-np.sqrt(2.75),np.sqrt(2.75)],color=BLUE);point(a,1.5+1j*np.sqrt(2.75),'',True);point(a,1.5-1j*np.sqrt(2.75),'',True)
        if n==7:circle(a,0,2);circle(a,2,2)
        if n==8:circle(a,0,2);a.plot([-np.sqrt(3),np.sqrt(3)],[1,1],'--',color=BLUE)
        if n==9:a.axhline(-1,color=BLUE,ls='--');a.axhline(1,color=BLUE)
        if n==10:a.axvline(0,color=BLUE,ls='--');point(a,-1,'-1 不定义',True)
    return finish(fig,out,'TB-CH01-Q17','浅蓝为点集；实线边界取到，虚线边界不取，空心点排除。圆或直线未邻接阴影的部分仅为辅助线。')

def shifted(out):
    fig,axs=plt.subplots(2,2,figsize=(12,8));omega=2;phi=1
    titles=[r'$h(t)\sin\omega(t-\varphi)$',r'$h(t)\sin(\omega t-\varphi)$',r'$h(t-\varphi)\sin\omega t$',r'$h(t-\varphi)\sin\omega(t-\varphi)$']
    fs=[lambda t:np.sin(omega*(t-phi)),lambda t:np.sin(omega*t-phi),lambda t:np.sin(omega*t),lambda t:np.sin(omega*(t-phi))]
    for n,(a,f,title) in enumerate(zip(axs.flat,fs,titles)):
        start=0 if n<2 else phi;t=np.linspace(start,5,800);a.axhline(0,color=GRAY,lw=.7);a.axvline(0,color=GRAY,lw=.7);a.plot([-.5,start],[0,0],color=BLUE);a.plot(t,f(t),color=BLUE);a.scatter([start],[f(start)],color=BLUE,zorder=3)
        if abs(f(start))>1e-10:a.scatter([start],[0],facecolors='white',edgecolors=BLUE,zorder=4)
        a.set(xlim=(-.5,5),ylim=(-1.3,1.3),xlabel='t',ylabel='f(t)',title=f'({n+1}) '+title);a.grid(alpha=.15)
    return finish(fig,out,'TB-CH09-Q2','示例参数 ω=2、φ=1，采用右连续阶跃 h(0)=1。相位移动与启动延迟不同；图示参数不替代题目的参数讨论。')

def saw(out):
    fig,axs=plt.subplots(1,2,figsize=(12,4.8))
    for n,a in enumerate(axs):
        for k in range(4):
            a.plot([k,k+1],[0,1] if n==0 else [1,0],color=BLUE)
            a.scatter([k],[0 if n==0 else 1],color=BLUE,zorder=3)
            a.scatter([k+1],[1 if n==0 else 0],facecolors='white',edgecolors=BLUE,zorder=3)
        a.set(xlim=(-.15,3.2),ylim=(-.15,1.2),xlabel='t',ylabel='f(t)',title=r'前向：$t-K(t-1)$' if n==0 else r'后向：$K(t)-t$');a.axhline(0,color=GRAY,lw=.7);a.grid(alpha=.15)
    return finish(fig,out,'TB-CH09-Q4','画 t≥0 部分，h(0)=1。周期为1；实心点为整数时刻的值，空心点表示左极限而非该时刻取值。')

def build(catalog,output):
    out=Path(output).resolve();out.mkdir(parents=True,exist_ok=True);qs={q['id']:q for q in json.loads(Path(catalog).read_text())};entries=[]
    specs={
      'A2019-Q3-4':(lambda z:(abs(z)>1)&(abs(z-np.sqrt(3)*1j)<2),(-2.5,2.5,-.6,4.2),[(0,1),(np.sqrt(3)*1j,2)],np.sqrt(3)*1j,0,r'$|z|>1,\ |z-\sqrt{3}i|<2$'),
      'FIT-A2019-Q3-4-V1':(lambda z:(abs(z)>1)&(abs(z-1j)<np.sqrt(2)),(-2,2,-1.3,2.8),[(0,1),(1j,np.sqrt(2))],1.5j,0,r'$|z|>1,\ |z-i|<\sqrt{2}$'),
      'A2021-Q3-3':(lambda z:(abs(z-1)>1)&(abs(z)<2),(-2.5,2.5,-2.5,2.5),[(1,1),(0,2)],-1,0,r'$|z-1|>1,\ |z|<2$'),
      'FIT-A2021-Q3-3-V1':(lambda z:(abs(z-2)>2)&(abs(z)<4),(-5,5,-5,5),[(2,2),(0,4)],-2,0,r'$|z-2|>2,\ |z|<4$'),
      'A2022-Q3-2':(lambda z:(z.real>0)&(abs(z)<2),(-.5,2.5,-2.5,2.5),[(0,2)],1,1j/3,'右半圆，半径2'),
      'FIT-A2022-Q3-2-V1':(lambda z:(z.real>0)&(abs(z)<3),(-.5,3.5,-3.5,3.5),[(0,3)],2,1j/4,'右半圆，半径3'),
      'FIT-A2022-Q3-2-V2':(lambda z:(z.imag>0)&(abs(z)<2),(-2.5,2.5,-.5,2.5),[(0,2)],1j,1j/2,'上半圆，半径2'),
      'FIT-A2025-Q3-1-V1':(lambda z:(abs(z)>2)&(abs(z-2)<2),(-.5,4.5,-2.5,2.5),[(0,2),(2,2)],2+np.sqrt(3)*1j,0,r'$|z|>2,\ |z-2|<2$'),
      'FIT-A2024-Q3-2-V1':(lambda z:(abs(z-2)<3)&(z.real<1),(-1.7,1.7,-3.5,3.5),[(2,3)],None,None,r'$|z-2|<3,\ \mathrm{Re}\,z<1$')}
    for ident,spec in specs.items():entries.append({'question_id':ident,'question_content_sha256':qs[ident]['review']['content_sha256'],'asset':mapping(out,ident,spec)})
    for ident,fn in [('TB-CH01-Q16',loci),('TB-CH01-Q17',sets),('TB-CH09-Q2',shifted),('TB-CH09-Q4',saw)]:entries.append({'question_id':ident,'question_content_sha256':qs[ident]['review']['content_sha256'],'asset':fn(out)})
    (out/'draft-assets.json').write_text(json.dumps({'engine':'python-matplotlib','author':'/root','status':'draft_pending_independent_visual_math_review','items':entries},ensure_ascii=False,indent=2));return entries

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--catalog',required=True);p.add_argument('--out',required=True);a=p.parse_args();print('Draft figures:',len(build(a.catalog,a.out)))
