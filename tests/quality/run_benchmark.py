#!/usr/bin/env python3
"""Execute both sides of all 14 controlled pairs and save inspectable evidence."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback

APP_ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(APP_ROOT/'src'))
from controlled_cases import CASES


def file_hash(path): return sha256(path.read_bytes()).hexdigest()


def execute(manifest):
    results=[]
    for row in manifest['adversarial_cases']:
        started=time.monotonic()
        fn=CASES.get(row.get('binding',{}).get('function'))
        if fn is None:
            measured={'observed':'pending','description':'No executable binding yet','evidence':{}}
        else:
            try:
                # Input selection is frozen separately from expected output:
                # changing the oracle must not change the exercised input.
                variant=row['binding']['variant']
                if variant not in {'valid','damaged'}:
                    raise ValueError('unknown input variant: '+str(variant))
                measured=fn(variant=='damaged')
            except Exception as exc:
                measured={'observed':'error','description':'Unexpected execution failure, never counted as correct rejection',
                          'evidence':{'exception':{'type':type(exc).__name__,'message':str(exc)},
                                      'traceback':traceback.format_exc()}}
        result=dict(case_id=row['case_id'], category=row['category'], expected=row['expected'],
                    binding=f'controlled_cases:{row["category"]}', **measured)
        result['elapsed_seconds']=round(time.monotonic()-started,4)
        result['matches_expected']=result['observed']==result['expected']
        results.append(result)
        print(f"{row['case_id']}: expected={row['expected']} observed={result['observed']} "
              f"({result['elapsed_seconds']}s)",flush=True)
    return results


def summarize(results):
    accept=[r for r in results if r['expected']=='accept']
    reject=[r for r in results if r['expected']=='reject']
    fp=sum(r['observed']=='reject' for r in accept)
    fn=sum(r['observed']=='accept' for r in reject)
    return {'total':len(results), 'matched':sum(r['matches_expected'] for r in results),
        'expected_accept':len(accept),'expected_reject':len(reject),
        'false_positive_count':fp,'false_positive_rate':fp/len(accept) if accept else None,
        'false_negative_count':fn,'false_negative_rate':fn/len(reject) if reject else None,
        'error_count':sum(r['observed']=='error' for r in results),
        'unresolved_count':sum(r['observed']=='unresolved' for r in results),
        'pending_count':sum(r['observed']=='pending' for r in results)}


def markdown(report,json_path):
    s=report['summary']
    lines=['# Q11 离线质量对照', '',
        f"生成时间：{report['created_at']}。实际耗时 {report['elapsed_seconds']:.2f} 秒；API 调用 0 次。", '',
        f"28 个受控输入的实际执行结果：{s['matched']}/{s['total']} 与预期一致。",
        f"误报（应接收却拒绝）{s['false_positive_count']}/{s['expected_accept']}；"
        f"漏报（应拒绝却接收）{s['false_negative_count']}/{s['expected_reject']}；"
        f"执行错误 {s['error_count']}，未决 {s['unresolved_count']}，未绑定 {s['pending_count']}。", '',
        f"执行过程中发生变化的生产文件：{len(report['files_changed_during_execution'])}；若非 0，本次结果不能视为固定代码快照复验，执行器返回非零退出码。", '',
        '## 方法与边界', '',
        '每对输入分别调用当前生产函数：内容本地策略、严格 JSON 契约、真实隔离 CAS、'
        '共享读者投影与批准器、审核证据验证、媒体需求验证、真实 Matplotlib 曲线编译、动画步骤契约。'
        '程序逐例保存输入、函数返回或阻断异常及耗时；预期只用于比对，不能直接产生 observed。'
        'CAS unknown/resource failure 不计作已正确查出的数学错误，意外异常也不计作正确拒绝。', '',
        '审核输运使用固定的微型对照回答，只测宿主是否强制来源、引用、版本和缺失审核门禁；'
        '没有运行生成模型或独立教师。图例只验证批准对象绑定、实际曲线分段和禁止坐标注入，'
        '不是教材讲解质量或视觉美观评分。动画用例验证动作契约，并未在此执行 Manim 视频。', '',
        '这 28 个样本为开发回归用例。误报/漏报率只描述这些已知正反例，不能外推真实书籍质量。'
        '6 个固定实书单元保持 pending_new_graph_validation；未读取或使用保留样本调优。'
        '独立教师评阅和学习者试验均未进行。', '',
        '## 复现', '', '在 `AIforStudy` 目录执行：', '', '```sh',
        report['command'], '```', '',
        '默认同时重写本报告及同名 JSON。JSON 保存全部 28 例输入、真实返回、异常、生产文件 SHA-256，'
        '可用 `--output-dir /tmp/q11-recheck` 把复验结果写到独立目录。退出码非 0 表示至少一例不匹配、未决或执行出错。', '',
        f'完整机器证据：[{json_path.name}]({json_path})。', '',
        '## 逐例结果', '', '| 用例 | 预期 | 实际 | 一致 | 秒 |', '|---|---|---|---|---:|']
    for r in report['cases']:
        lines.append(f"| {r['case_id']} | {r['expected']} | {r['observed']} | {'是' if r['matches_expected'] else '否'} | {r['elapsed_seconds']} |")
    lines += ['', '## 成对行为', '']
    for category in CASES:
        cases=[r for r in report['cases'] if r['category']==category]
        if cases: lines.append(f"- `{category}`：{cases[0]['description']}")
    if any(not r['matches_expected'] for r in report['cases']):
        lines += ['', '## 待处理', '']
        for r in report['cases']:
            if not r['matches_expected']:
                lines.append(f"- `{r['case_id']}`：{r['evidence'].get('exception',r['observed'])}")
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,default=Path(__file__).with_name('manifest.json'))
    parser.add_argument('--output-dir',type=Path,default=APP_ROOT.parent/'文档/02-记录与报告/教学质量内核重构-20261008')
    args=parser.parse_args()
    manifest=json.loads(args.manifest.read_text())
    source_paths=sorted((APP_ROOT/'src/aiforstudy/blocks/content').glob('*.py'))
    for folder in ('math','media','visual'):
        source_paths+=sorted((APP_ROOT/f'src/aiforstudy/blocks/{folder}').rglob('*.py'))
    source_before={str(p.relative_to(APP_ROOT)):file_hash(p) for p in source_paths}
    began=time.monotonic(); results=execute(manifest); summary=summarize(results)
    source_after={str(p.relative_to(APP_ROOT)):file_hash(p) for p in source_paths}
    changed=[name for name in source_before if source_before[name]!=source_after[name]]
    git=subprocess.run(['git','rev-parse','HEAD'],cwd=APP_ROOT,text=True,capture_output=True,check=False)
    report={'schema_version':'ControlledQualityBenchmarkV1','created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'controlled production-contract regression, not real model/human pedagogical evaluation',
        'command':'PYTHONPATH=src ../.venv-linux/bin/python tests/quality/run_benchmark.py',
        'python':sys.version,'platform':platform.platform(),'git_head':git.stdout.strip(),
        'working_tree_hashes':source_before,'files_changed_during_execution':changed,
        'runner_hashes':{str(p.relative_to(APP_ROOT)):file_hash(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
        'manifest_sha256':file_hash(args.manifest),'api_calls':0,
        'elapsed_seconds':round(time.monotonic()-began,4),'summary':summary,'cases':results,
        'real_units':deepcopy_units(manifest['real_units']),
        'independent_teacher_review':'not_performed','learner_study':'not_performed'}
    args.output_dir.mkdir(parents=True,exist_ok=True)
    target=args.output_dir/'Q11-离线质量对照.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    target.with_suffix('.md').write_text(markdown(report,target.resolve()))
    print(json.dumps(summary,ensure_ascii=False,indent=2)); print(str(target))
    return 0 if all(r['matches_expected'] for r in results) and not changed else 1


def deepcopy_units(units):
    # Identity/status only: deliberately do not open real source/holdout files.
    return [dict(row, benchmark_status='not_executed_by_controlled_runner') for row in units]


if __name__=='__main__':
    raise SystemExit(main())
