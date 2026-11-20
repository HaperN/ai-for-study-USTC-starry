"""Read-only inventory of historical lesson/receipt files; historical writers retired."""
import argparse
import json
from pathlib import Path

def reconcile(parse_dir,lessons,audit,*,delivery_dirs=None,check=False,log=print):
    if not check:
        raise ValueError("历史产物只读：只支持 --check；当前运行请使用 aifs run/resume")
    rows=[]
    for kind,root in (("parse",parse_dir),("lesson",lessons),("audit",audit)):
        for path in sorted(Path(root).rglob("*.json")):
            try:
                value=json.loads(path.read_text(encoding="utf-8"))
                rows.append({"kind":kind,"path":str(path),"schema_version":(value.get("_meta") or {}).get("schema_version") if isinstance(value,dict) else None,"readable":True})
            except (OSError,ValueError) as exc:rows.append({"kind":kind,"path":str(path),"readable":False,"error":str(exc)})
    return {"read_only":True,"approval_inherited":False,"files":rows}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parse_dir");parser.add_argument("--lessons",required=True);parser.add_argument("--audit",required=True);parser.add_argument("--check",action="store_true")
    args=parser.parse_args(argv)
    try:result=reconcile(args.parse_dir,args.lessons,args.audit,check=args.check)
    except ValueError as exc:parser.error(str(exc))
    print(json.dumps(result,ensure_ascii=False,indent=2));return int(any(not row['readable'] for row in result['files']))

if __name__ == "__main__": raise SystemExit(main())
