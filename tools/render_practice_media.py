"""Render the reviewed scene programs with the existing isolated Manim worker."""
from pathlib import Path
import argparse,json,os,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from aiforstudy.blocks.media.code_runtime import render_generated_code
from aiforstudy.core.review_mode import review_mode

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--specs',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--profile',choices=['draft','final'],default='draft');p.add_argument('--only')
    p.add_argument('--manim-python',type=Path);p.add_argument('--speech-config',type=Path)
    a=p.parse_args()
    if a.manim_python:os.environ['AIFORSTUDY_MANIM_PYTHON']=str(a.manim_python.expanduser().absolute())
    narration=json.loads(a.speech_config.read_text()) if a.speech_config else {'enabled':False}
    if a.profile=='final' and not narration.get('enabled'):raise ValueError('final narrated edition needs an explicit enabled speech configuration')
    for item in json.loads(a.specs.read_text()):
        if a.only and item['id']!=a.only:continue
        out=a.out/item['id'];out.mkdir(parents=True,exist_ok=False)
        with review_mode('full'):
            result=render_generated_code(item['program'],context=item['context'],out_dir=out,workspace_root=ROOT,profile=a.profile,narration=narration)
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'id':item['id'],'status':result['status'],'reason':result.get('reason')},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
