"""Run evidence-preserving author/blind/compare jobs with configured production roles."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from aiforstudy.practice.model import call_batch

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',type=Path,required=True);p.add_argument('--questions',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--mode',choices=['author','blind','compare'],default='blind');p.add_argument('--role');p.add_argument('--max-tokens',type=int,default=22000);a=p.parse_args()
 r=call_batch(a.workspace,json.loads(a.questions.read_text()),a.out,mode=a.mode,role=a.role,max_tokens=a.max_tokens)
 print(json.dumps({k:r[k] for k in ['ok','mode','request_sha256','fingerprint','model']},ensure_ascii=False))
if __name__=='__main__':main()
