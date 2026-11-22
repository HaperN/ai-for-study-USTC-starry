"""Two explanatory vectors for the corrected streamline and keyhole exercises."""
from pathlib import Path
import argparse,json,hashlib
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.family':'Arial Unicode MS','font.size':12,'axes.unicode_minus':False,'svg.fonttype':'path'})

def build(out):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True);assets={}
    def save(fig,qid,purpose):
        fig.text(.5,.02,purpose,ha='center',fontsize=11);fig.tight_layout(rect=(0,.07,1,1));svg=out/(qid+'.svg');png=out/(qid+'.png');fig.savefig(svg);fig.savefig(png,dpi=140);plt.close(fig)
        assets[qid]={'relative_path':'assets/practice-'+qid+'.svg','source_file':str(svg),'preview_file':str(png),'sha256':hashlib.sha256(svg.read_bytes()).hexdigest(),'purpose':purpose}
    fig,ax=plt.subplots(figsize=(10,7));ax.set(xlim=(-3.5,4.5),ylim=(-3,5),xlabel='x',ylabel='y',title='经过原点的流线只取蓝色连通支');ax.set_aspect('equal');ax.axhline(0,color='#aaa',lw=.7);ax.axvline(0,color='#aaa',lw=.7)
    X,Y=np.meshgrid(np.arange(-3.5,4.6,.5),np.arange(-3,5.1,.5));U=2*X+3;V=4-2*Y;M=np.hypot(U,V);U=np.divide(U,M,out=np.zeros_like(U),where=M>1e-8);V=np.divide(V,M,out=np.zeros_like(V),where=M>1e-8)
    ax.quiver(X,Y,U,V,color='#a8bdc8',angles='xy',scale_units='xy',scale=4,width=.0025)
    for lo,hi,color,style in [(-1.49,4.5,'#197896','-'),(-3.5,-1.51,'#888','--')]:
        x=np.linspace(lo,hi,1500);ax.plot(x,4*x/(2*x+3),color=color,ls=style,lw=2.4)
    ax.axvline(-1.5,color='#bbb',ls=':',lw=1);ax.axhline(2,color='#bbb',ls=':',lw=1)
    ax.scatter([0], [0],color='#c04c5d',s=50,zorder=5);ax.annotate('原点；速度方向 (3,4)',(0,0),xytext=(.25,-.55))
    ax.scatter([-1.5],[2],color='#333',s=35,zorder=5);ax.annotate('临界点 (-3/2,2)',(-1.5,2),xytext=(-1.35,2.3))
    ax.text(-3.4,4.55,'灰虚线：同一水平集的另一支');ax.text(.5,3.7,r'$\psi=0:\ y=\frac{4x}{2x+3}$'+'\n过原点支：x > -3/2')
    save(fig,'NAT-CH08-045','箭头只表示速度方向，已归一化，不表示速率；图框截断不表示流线终止。')
    fig,(ax,txt)=plt.subplots(1,2,figsize=(12,6),gridspec_kw={'width_ratios':[1.25,1]});R=2.;eps=.3;delta=.10
    ax.set(xlim=(-2.4,2.6),ylim=(-2.4,2.4),xlabel='Re z',ylabel='Im z',title='正向钥匙孔围道');ax.set_aspect('equal');ax.axhline(0,color='#aaa',lw=.7);ax.axvline(0,color='#aaa',lw=.7)
    def curve(z):ax.plot(z.real,z.imag,color='#187a98',lw=2.2)
    def arrow(a,b):ax.annotate('',(b.real,b.imag),(a.real,a.imag),arrowprops={'arrowstyle':'->','color':'#bd485c','lw':2})
    curve(np.linspace(eps,R,200)*np.exp(1j*delta));curve(R*np.exp(1j*np.linspace(delta,2*np.pi-delta,700)))
    curve(np.linspace(R,eps,200)*np.exp(-1j*delta));curve(eps*np.exp(1j*np.linspace(2*np.pi-delta,delta,500)))
    arrow(.9*np.exp(1j*delta),1.4*np.exp(1j*delta));arrow(1.4*np.exp(-1j*delta),.9*np.exp(-1j*delta))
    arrow(R*np.exp(1j*1.0),R*np.exp(1j*1.3));arrow(eps*np.exp(1j*2.0),eps*np.exp(1j*1.5))
    ax.plot([0,2.4],[0,0],color='#555',ls='--',lw=1);ax.scatter([-1],[0],color='#c04c5d',s=40);ax.text(-1.25,-.35,'极点 -1');ax.text(-.12,-.15,'0');ax.text(1.25,.35,'上岸 →');ax.text(1.25,-.45,'← 下岸');ax.text(-1.6,1.55,r'$C_R$ 逆时针');ax.text(-.9,.5,r'$C_\varepsilon$ 顺时针')
    txt.axis('off');txt.text(0,.95,'分支固定：'+r'$0<\arg z<2\pi$'+'\n\n'+r'上岸：$z^{a-1}\to x^{a-1}$'+'\n\n'+r'下岸：$z^{a-1}\to e^{2\pi i(a-1)}x^{a-1}$'+'\n\n上岸从 ε 到 R；下岸从 R 回 ε。\n两岸方向相反，不能直接相加。\n\n示例 ε=0.3、R=2，满足 ε<1<R。\n红箭头给出完整围道的行进方向。',va='top',linespacing=1.8)
    save(fig,'EX-CH06-06','为了区分两岸，图中留了小夹角；两岸值指夹角趋于0时的边界极限。原点与正实切口不在解析域内。')
    (out/'draft-assets.json').write_text(json.dumps({'engine':'matplotlib','author':'/root','status':'draft_requires_independent_review','assets':assets},ensure_ascii=False,indent=2));return assets

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args();print('Draft SVGs',len(build(a.out)))
