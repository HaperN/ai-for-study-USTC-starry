"""Produce reviewed solutions from source-checked question metadata; no silent repair."""
from pathlib import Path
import argparse,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from aiforstudy.practice.production import produce_batch

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for k in ('workspace','questions','out'):p.add_argument('--'+k,type=Path,required=True)
 a=p.parse_args();r=produce_batch(a.workspace,json.loads(a.questions.read_text(encoding='utf-8')),a.out);print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r['blocked']==0 else 2
if __name__=='__main__':raise SystemExit(main())
