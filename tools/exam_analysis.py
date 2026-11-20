#!/usr/bin/env python3
"""Export real A/B historical audit; requires an existing portable course library."""
import argparse
import json
from pathlib import Path
from aiforstudy.core.course_library import load_library
from aiforstudy.core.exam_analysis import analyze, load_annotations

def main():
 p=argparse.ArgumentParser();p.add_argument('--bundle',required=True);p.add_argument('--out',required=True);args=p.parse_args()
 catalog,_=load_library(args.bundle);result=analyze(catalog, load_annotations(Path(args.bundle)/"exam-annotations.json"))
 out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
 (out/'real-backtest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 receipts=[{'target':f['target_session_id'],'predictions':f['predictions']} for f in result['folds']]
 (out/'freeze-receipts.json').write_text(json.dumps(receipts,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'atomic_tasks':len(result['questions']),'real_folds':len(result['folds']),'mode':result['mode']},ensure_ascii=False))
if __name__=='__main__':main()
