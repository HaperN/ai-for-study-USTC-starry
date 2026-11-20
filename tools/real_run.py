#!/usr/bin/env python3
"""Opt-in local observation of the production engine; default is a read-only plan.

No scripted responses, book-specific IDs, accounting policy or automatic chain
from preparation into generation belongs here. The caller explicitly runs each
phase and the engine remains the sole owner of execution and resumability.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aiforstudy.cli import _run_cancellation
from aiforstudy.contracts.enums import DeliveryFormat, Goal, PreparePolicy, Profile, SelectorKind
from aiforstudy.contracts.request import MAX_PARALLELISM, RunRequest, Selectors
from aiforstudy.core.registry import load_registry
from aiforstudy.core.workspace import open_workspace
from aiforstudy.errors import AifsError, InputError
from aiforstudy.pipeline.engine import run_request, resume_request
from aiforstudy.pipeline.planner import plan, read_catalog
from aiforstudy.pipeline.preparation import prepared_context
from aiforstudy.pipeline.state import book_runs_root
from aiforstudy.providers.factory import configured_providers


def _normalized(value):
    return unicodedata.normalize('NFC', str(value).strip())


def resolve_targets(parse_root: Path, targets: list[str]) -> tuple[str, ...]:
    """Match both anchor and title against current content, never stale IDs."""
    catalog = read_catalog(parse_root)
    if catalog is None:
        raise InputError('尚无当前目录；先执行 prepare')
    selected = set()
    for target in targets:
        anchor, sep, title = target.partition('=')
        anchor, title = _normalized(anchor), _normalized(title)
        if not sep or not anchor or not title:
            raise InputError('--target 格式必须为 教材锚点=完整标题')
        matches = [u for u in catalog.units if _normalized(u.anchor)==anchor and _normalized(u.title)==title]
        if len(matches)!=1:
            raise InputError(f'目标 {target!r} 在当前目录中有 {len(matches)} 个匹配；不猜测或替换范围')
        unit = matches[0]
        if unit.level not in ('section','subsection'):
            raise InputError(f'{target!r} 不是实质节/子节；不将章引言或容器当目标')
        source = (parse_root / unit.original_text).resolve()
        if (not unit.original_text or not source.is_relative_to(parse_root.resolve())
                or not source.is_file() or not source.read_text(encoding='utf-8-sig').strip()):
            raise InputError(f'{target!r} 缺少当前目录内可读取的非空原文')
        if unit.unit_id in selected:
            raise InputError(f'重复目标：{target!r}')
        selected.add(unit.unit_id)
    if not selected:
        raise InputError('至少指定一个 --target 或 --units')
    return tuple(u.unit_id for u in catalog.units if u.unit_id in selected)


def runtime_readiness() -> dict:
    required=('pymupdf','schemdraw','matplotlib','numpy','sympy','PIL','jsonschema')
    missing=[name for name in required if importlib.util.find_spec(name) is None]
    if sys.version_info[:2] != (3,12):
        missing.append('Python 3.12')
    return {'ready':not missing,'python':sys.executable,'missing':missing}


def build_parser():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace')
    parser.add_argument('--book',required=True)
    parser.add_argument('--phase',choices=('prepare','deliver','resume'),default='deliver')
    selection=parser.add_mutually_exclusive_group()
    selection.add_argument('--units',help='仅用于已准备完成的新目录，逗号分隔')
    selection.add_argument('--target',action='append',default=[],help='按新目录锚点=标题定位，可重复')
    parser.add_argument('--run-id',help='仅 resume 使用；不改变原请求')
    parser.add_argument('--parallel',type=int,help='新deliver默认用满config/execution.json的release并发，prepare默认1；resume沿用原请求')
    parser.add_argument('--observations',type=Path,help='本地测试观察目录；execute 时必填')
    parser.add_argument('--execute',action='store_true',help='实际运行并可能调用付费 API；默认只读')
    return parser


def _request(args, workspace):
    registry=load_registry(workspace)
    original=registry.resolve(args.book)
    if args.phase=='resume':
        if args.units or args.target or not args.run_id or args.parallel is not None:
            raise InputError('resume 只接受 --run-id；不重新指定范围或并发')
        if not re.fullmatch(r'run-[A-Za-z0-9_-]+',args.run_id):
            raise InputError('非法 run-id')
        root=book_runs_root(workspace.root,args.book)/args.run_id
        request=RunRequest.from_dict(json.loads((root/'request.json').read_text(encoding='utf-8')))
        if request.book_id!=args.book or request.profile is not Profile.RELEASE:
            raise InputError('只可继续同一本书的 release 运行')
        return request, {'phase':'resume','run_id':args.run_id,
                         'request':request.as_dict(),'requires_preparation':None}
    if args.run_id:
        raise InputError('--run-id 仅用于 resume')
    if args.phase=='prepare' and (args.units or args.target):
        raise InputError('prepare 只做全书基础准备，不接受教学单元选择')
    if args.phase=='deliver' and not (args.units or args.target):
        raise InputError('deliver 必须提供 --target 或 --units')
    preparation=RunRequest(book_id=args.book,selectors=Selectors(SelectorKind.ALL),
        goal=Goal.PREPARE,profile=Profile.RELEASE,prepare_policy=PreparePolicy.ENSURE,
        parallelism=1 if args.parallel is None else args.parallel,delivery_formats=(DeliveryFormat.CHAPTER_HTML,)).validate()
    prepared_plan=plan(preparation,registry)
    if args.phase=='prepare':
        return preparation,{'phase':'prepare',**prepared_plan.as_dict()}
    if prepared_plan.requires_preparation:
        return None,{'phase':'deliver','requires_preparation':True,'resolved_scope':None,
                     'requested_targets':args.target,'requested_units':args.units,
                     'preparation': [task.as_dict() for task in prepared_plan.preparation],
                     'note':'先独立完成 prepare，再按新目录选择目标；本工具不会自动扩跑或复用旧范围'}
    context=prepared_context(original)
    units=resolve_targets(context.parse,args.target) if args.target else tuple(s.strip() for s in args.units.split(',') if s.strip())
    request=RunRequest(book_id=args.book,selectors=Selectors(SelectorKind.UNITS,units),
        goal=Goal.DELIVER,profile=Profile.RELEASE,prepare_policy=PreparePolicy.REUSE,
        parallelism=MAX_PARALLELISM if args.parallel is None else args.parallel,delivery_formats=(DeliveryFormat.CHAPTER_HTML,)).validate()
    selected=plan(request,registry)
    if selected.requires_preparation or selected.resolved_scope is None:
        raise InputError('准备资料已变化；请重新检查 prepare，尚未启动生成')
    catalog=read_catalog(context.parse)
    ids=set(selected.resolved_scope)
    return request,{'phase':'deliver',**selected.as_dict(),
                    'selected_units':[{'unit_id':u.unit_id,'title':u.title,'anchor':u.anchor}
                                      for u in catalog.units if u.unit_id in ids]}


def main(argv=None):
    args=build_parser().parse_args(argv)
    try:
        workspace=open_workspace(args.workspace)
        request,view=_request(args,workspace)
        runtime=runtime_readiness()
        view.update(executed=False,runtime=runtime)
        if not args.execute:
            print(json.dumps(view,ensure_ascii=False,indent=2))
            return 0
        if request is None:
            raise InputError('基础资料未完成；先以 --phase prepare 单独准备，不能使用旧范围直接开跑')
        if args.observations is None:
            raise InputError('实际本地测试必须指定 --observations 目录')
        if not runtime['ready']:
            raise InputError('当前 Python 缺少运行依赖：'+', '.join(runtime['missing'])+'；请使用源码入口 ./aifs local-test')
        from tools.real_run_observer import LocalRunObserver
        observer=LocalRunObserver(args.observations,book_id=args.book,
                                  resume_run_id=args.run_id if args.phase=='resume' else None)
        providers=configured_providers(request,workspace)
        if providers.llm is None:
            raise InputError('缺少真实模型服务凭据，尚未创建运行')
        providers.call_observer=observer
        with _run_cancellation() as cancel:
            if args.phase=='resume':
                result,store,_=resume_request(request,args.run_id,workspace,providers=providers,cancel=cancel)
            else:
                result,store,_=run_request(request,workspace,providers=providers,cancel=cancel)
        print(json.dumps({'executed':True,'run_id':store.run_id,'run_dir':str(store.root),
                          'result':result.as_dict(),'observations':observer.summary(execution_path=store.root/'execution.json')},ensure_ascii=False,indent=2))
        return result.exit_code
    except (AifsError,OSError,ValueError) as exc:
        print(f'{type(exc).__name__}: {exc}',file=sys.stderr)
        return getattr(exc,'exit_code',2)
    except KeyboardInterrupt:
        print('本地测试已取消',file=sys.stderr)
        return 130


if __name__=='__main__':
    raise SystemExit(main())
