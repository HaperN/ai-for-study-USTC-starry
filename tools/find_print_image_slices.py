"""Find whitespace cuts in a rendered long figure, without editing its pixels."""
import argparse,json
from PIL import Image

def slices(path,max_height):
    im=Image.open(path).convert('RGB');w,h=im.size
    white=[]
    for y in range(h):
        ink=sum(min(r,g,b)<246 for r,g,b in (im.getpixel((x,y)) for x in range(w)))
        white.append(ink<=max(2,int(w*.003)))
    runs=[];start=None
    for y,blank in enumerate(white+[False]):
        if blank and start is None:start=y
        if not blank and start is not None:
            if y-start>=12:runs.append((start,y))
            start=None
    cuts=[0]
    while h-cuts[-1]>max_height:
        lower=cuts[-1]+max_height*.42;upper=cuts[-1]+max_height
        options=[(a,b) for a,b in runs if lower<=(a+b)/2<=upper]
        if not options:return {'width':w,'height':h,'segments':[],'status':'no_safe_whitespace_cut'}
        # Prefer a genuine panel gutter over a small gap inside an axis label.
        widest=max(b-a for a,b in options)
        a,b=max(((a,b) for a,b in options if b-a>=max(12,widest*.65)),key=lambda x:(x[0]+x[1])/2)
        cuts.append(round((a+b)/2))
    cuts.append(h)
    return {'width':w,'height':h,'segments':[[a,b] for a,b in zip(cuts,cuts[1:])],'status':'whitespace_slices','all_pixels_retained':cuts[0]==0 and cuts[-1]==h}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('image');p.add_argument('--max-height',type=int,default=880);a=p.parse_args();print(json.dumps(slices(a.image,a.max_height)))
