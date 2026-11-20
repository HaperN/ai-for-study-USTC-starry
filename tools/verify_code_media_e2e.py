#!/usr/bin/env python3
"""Current engine acceptance: fixed approved teaching example, new media to HTML.

Default is offline with explicit code/review fixtures. --live-media calls the
already configured API for Python generation and actual-pixel media review only.
No image-generation model, new keys, full book parse, or invented usage totals.
"""
from __future__ import annotations
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import replace
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests'),str(ROOT/'tools')]
from aiforstudy.core.digest import digest_file, digest_of
from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.core.workspace import open_workspace
from aiforstudy.contracts.request import RunRequest
from aiforstudy.errors import ContractError, ConfigError
from aiforstudy.pipeline.engine import run_request, resume_request
from aiforstudy.pipeline.services import CancelToken, Providers
from aiforstudy.providers.transport import RealTransport
from aiforstudy.blocks.media.validation import display_runtime, validate_media_report
from aiforstudy.blocks.visual.manim_adapter import default_services
from support.runner_v5 import setup_book
from support.book_factory import _request, UNIT
from verify_author_media_fresh_e2e import BODY, AcceptanceTransport, browser_check


def fixture_program(request):
    """A visible vector construction outside the old animation action DSL."""
    if request['kind']=='drawing':
        code='''import matplotlib.pyplot as plt
import numpy as np
def build_figure(context):
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.set_aspect('equal'); ax.set_xlim(-1, 4); ax.set_ylim(-.5, 4)
    ax.axis('off')
    for start,end,color in [((0,0),(2,1),'#1769aa'),((2,1),(3,3),'#dd7700'),((0,0),(3,3),'#15945a')]:
        ax.annotate('',xy=end,xytext=start,arrowprops=dict(arrowstyle='->',color=color,lw=4))
    ax.text(.6,-.35,'a=(2,1)',fontsize=28,color='#1769aa')
    ax.text(2.5,1.5,'b',fontsize=32,color='#dd7700')
    ax.text(-.7,2.4,'c=(3,3)',fontsize=28,color='#15945a')
    ax.text(-.15,-.15,'O',fontsize=26)
    fig.tight_layout()
    return fig
'''
        steps=[];backend='matplotlib'
    else:
        code='''from manim import *
from aifs_media_runtime import media_step
class GeneratedScene(Scene):
    def construct(self):
        first=MathTex('2+1',font_size=110,color=BLACK).shift(LEFT*2.5+UP)
        second=MathTex('1+2',font_size=110,color=BLACK).shift(RIGHT*2.5+UP)
        with media_step(self,'S1'):
            self.play(Write(first)); self.wait(1)
            self.play(Transform(first,MathTex('3',font_size=110,color=BLACK).move_to(first)))
            self.wait(1)
        with media_step(self,'S2'):
            self.play(Write(second)); self.wait(1)
            self.play(Transform(second,MathTex('3',font_size=110,color=BLACK).move_to(second)))
            self.wait(1)
'''
        steps=[{'step_id':'S'+str(i+1),'author_step_index':i,'caption':text,
            'observation':text,'expected_change':'加法表达式变为三'}
            for i,text in enumerate(request['animation']['steps'])];backend='manim'
    return {'schema_version':'GeneratedMediaCodeV1','request_id':request['request_id'],
        'backend':backend,'code':code,'steps':steps,
        'object_bindings':[{'display_id':'display-'+str(i),**ref} for i,ref in enumerate(request['object_refs'])]}


def _payload(messages):
    for message in reversed(messages):
        if message.get('role')=='user' and isinstance(message.get('content'),str):
            try: return json.loads(message['content'])
            except ValueError: pass
    return {}


class CodeTransport(AcceptanceTransport):
    def _answer(self,system,user):
        data=json.loads(user)
        if 'GeneratedMediaCodeV1' in system and 'review_items' not in data:
            request=data.get('request') or data.get('frozen_context',{}).get('request')
            result=fixture_program(request)
            self.exchanges.append({'input':data,'response':deepcopy(result)})
            return json.dumps(result,ensure_ascii=False)
        return super()._answer(system,user)


class LiveMediaTransport(RealTransport):
    """Explicit acceptance-only observer; never a production execution policy."""
    def __init__(self,*,approved_budget_tokens,approved_by='',**kwargs):
        if type(approved_budget_tokens) is not int or approved_budget_tokens<=0:
            raise ValueError('test observation limit must be positive')
        self.observer_token_limit=approved_budget_tokens
        self.spent_tokens=0
        super().__init__(**kwargs)

    def __post_init__(self):
        super().__post_init__();self.control=CodeTransport();self.exchanges=[]
        self.live_calls_started=0

    def request_body(self,**kwargs):
        body=super().request_body(**kwargs)
        # Capture the actual outbound protocol, not just the caller's role label.
        # Never include messages, images, URL headers or credentials here.
        self._wire_options={key:deepcopy(body.get(key))
            for key in ('model','max_tokens','thinking','reasoning_effort')}
        return body

    def _event(self,event,**fields):
        # Deliberately whitelist call metadata at callers: no endpoint headers,
        # credentials, raw response or complete model input enters this log.
        row={'event':event,**fields}
        if getattr(self,'evidence_out',None):
            with (self.evidence_out/'live-call-events.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(row,ensure_ascii=False)+'\n')
        print(json.dumps(row,ensure_ascii=False),flush=True)

    def _save_exchange(self,row):
        self.exchanges.append(row)
        if getattr(self,'evidence_out',None):
            write_json_atomic(self.evidence_out/'live-exchanges.json',self.exchanges)

    def __call__(self,**kwargs):
        data=_payload(kwargs['messages'])
        system=next((m.get('content','') for m in kwargs['messages'] if m.get('role')=='system'),'')
        live=('GeneratedMediaCodeV1' in system or ('review_items' in data and 'request' in data))
        if not live: return self.control(**kwargs)
        started=time.monotonic()
        phase='review' if 'review_items' in data else 'code'
        self.live_calls_started+=1
        call={'call_id':self.live_calls_started,'phase':phase,
              'request_id':data.get('request',{}).get('request_id'),
              'model':kwargs.get('model'),'effort':kwargs.get('effort'),
              'max_tokens':kwargs.get('max_tokens'),'timeout_seconds':kwargs.get('timeout'),
              'image_count':len(kwargs.get('images') or [])}
        self._event('api-start',**call)
        self._wire_options={}
        try:
            if self.spent_tokens >= self.observer_token_limit:
                raise RuntimeError('explicit test observer token limit reached')
            text,usage=super().__call__(**kwargs)
            try:
                count=(usage or {}).get('total_tokens')
                if type(count) is int and count>=0: self.spent_tokens+=count
            except (TypeError,AttributeError): pass
        except Exception as exc:
            failure={**call,'wall_seconds':round(time.monotonic()-started,3),
                     'wire_options':deepcopy(self._wire_options),
                     'error_type':type(exc).__name__,'usage':None,
                     'remote_unconfirmed':self.last_remote_unconfirmed}
            self._save_exchange({**failure,'input':data,'response_text':None,'response':None})
            self._event('api-failure',**failure)
            raise
        parsed=None;parse_error=None
        try:
            parsed=json.loads(text)
        except json.JSONDecodeError as exc:
            # Preserve returned usage and text. Gateway must record the actual
            # usage before deciding whether this is a retryable format error.
            parse_error={'message':exc.msg,'line':exc.lineno,'column':exc.colno,'position':exc.pos}
        row={**call,'wire_options':deepcopy(self._wire_options),'input':data,'response_text':text,'response':parsed,
             'response_json_error':parse_error,'usage':deepcopy(usage),
             'wall_seconds':round(time.monotonic()-started,3),'response_chars':len(text)}
        self._save_exchange(row)
        self._event('api-finish',**call,usage=usage,wall_seconds=row['wall_seconds'],
                    wire_options=row['wire_options'],
                    response_chars=len(text),response_json_valid=parse_error is None,
                    response_json_error=parse_error)
        return text,usage


@contextmanager
def _signal_cancellation(cancel):
    """Let the engine settle usage and stop its workers for SIGINT/SIGTERM."""
    main_thread=threading.current_thread() is threading.main_thread()
    previous={number:signal.getsignal(number) for number in (signal.SIGINT,signal.SIGTERM)} if main_thread else {}

    def interrupt(number,_frame):
        if not cancel.cancelled:
            cancel.cancel('验收收到 '+signal.Signals(number).name+'，正在停止 worker 并结算用量')

    try:
        for number in previous: signal.signal(number,interrupt)
        yield
    finally:
        for number,handler in previous.items(): signal.signal(number,handler)


def _reader_projection(store, context, rows):
    """Consume the same current delivery receipt and actual layout as the reader."""
    from aiforstudy.pipeline.delivery_stage import validate_reader_delivery
    ctx=SimpleNamespace(services=SimpleNamespace(store=store),
        plan=SimpleNamespace(resolved_scope=(UNIT,)),context=context)
    receipt=read_json(store.work/'products/render/CH01.json').get('payload') or {}
    if receipt.get('schema_version')!='ReaderProjectionV1':
        raise ContractError('current ReaderProjectionV1 required')
    evidence=validate_reader_delivery(ctx,chapter_key='CH01')['CH01']
    if evidence['partial'] or evidence['review_outcome']['status']!='verified':
        raise ContractError('ReaderProjection is partial or unverified')
    root=Path(receipt['out_dir'])
    text=(root/'data/data.js').read_text(encoding='utf-8')
    data=json.loads(text.split('window.__DATA__ = ',1)[1].rsplit(';',1)[0])
    unit=data['units'][UNIT];document=unit['reader_document'];layout=unit['reader_layout']
    media=unit['reader_media'];mounts=[m for m in layout['mounts'] if m['source_kind']=='media']
    expected={r['request_id']:r for r in rows}
    if (len(media)!=len(expected) or len(mounts)!=len(expected)
            or {r['request_id'] for r in media}!=set(expected)
            or {m['source_id'] for m in mounts}!=set(expected)):
        raise ContractError('ReaderProjection media/layout coverage differs from current manifest')
    for item in media:
        source=expected[item['request_id']]
        mount=next(m for m in mounts if m['source_id']==item['request_id'])
        if (item.get('review_outcome',{}).get('status')!='verified' or item['kind']!=source['kind']
                or item['source_hash']!=mount['source_hash']):
            raise ContractError('ReaderProjection media state/source differs from bound layout')
    return {'html':str(root/'index.html'),'reader_projection':evidence,
            'reader_layout':{'schema_version':layout['schema_version'],'sha256':digest_of(layout),'mounts':mounts}}


def run_acceptance(output, *, live_media=False, budget_tokens=1_000_000,
                   media_max_tokens=None, media_effort=None, resume_from=None):
    """Always retain an honest operational failure report for a fresh invocation."""
    output=Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('fresh media requires an empty output directory')
    # Invalid caller options are not an attempted acceptance run.
    if type(budget_tokens) is not int or budget_tokens<=0:
        raise ValueError('budget_tokens must be a positive integer')
    if media_max_tokens is not None and (type(media_max_tokens) is not int or media_max_tokens<=0):
        raise ValueError('media_max_tokens must be a positive integer')
    if media_effort is not None and media_effort not in ('none','low','medium','high'):
        raise ValueError('media_effort must be none, low, medium or high')
    if resume_from is not None:
        if media_max_tokens is not None or media_effort is not None:
            raise ValueError('resume preserves the original generation settings')
        prior=read_json(Path(resume_from)/'acceptance.json')
        if (prior.get('actual_media_api') is not live_media or
                prior.get('scope')!='fixed approved teaching fixture; production media and composition stages'):
            raise ValueError('resume requires the same fixture and live/offline provider mode')
    started=time.monotonic()
    try:
        return _run_acceptance(output,live_media=live_media,budget_tokens=budget_tokens,
            media_max_tokens=media_max_tokens,media_effort=media_effort,resume_from=resume_from)
    except Exception as exc:
        output.mkdir(parents=True,exist_ok=True)
        def saved(name):
            try: return read_json(output/name)
            except (ConfigError,OSError,ValueError): return {}
        report=saved('acceptance.json');observation=saved('execution-observation.json')
        report.update(observation)
        cancelled=bool(report.get('cancelled'))
        report.update(ok=False,exit_code=130 if cancelled else 1,
            actual_media_api=live_media,wall_seconds=round(time.monotonic()-started,3),
            exception={'type':type(exc).__name__,'message':str(exc)})
        report.setdefault('external_api_calls',None if live_media else 0)
        report.setdefault('acceptance_errors',[]).append(type(exc).__name__+': '+str(exc))
        report['engine']=saved('engine-result.json')
        write_json_atomic(output/'acceptance.json',report)
        return report


def _run_acceptance(output, *, live_media=False, budget_tokens=1_000_000,
                   media_max_tokens=None, media_effort=None, resume_from=None):
    if type(budget_tokens) is not int or budget_tokens<=0:
        raise ValueError('budget_tokens must be a positive integer')
    if media_max_tokens is not None and (type(media_max_tokens) is not int or media_max_tokens<=0):
        raise ValueError('media_max_tokens must be a positive integer')
    if media_effort is not None and media_effort not in ('none','low','medium','high'):
        raise ValueError('media_effort must be none, low, medium or high')
    output=Path(output).resolve()
    if output.exists() and any(output.iterdir()): raise ValueError('fresh media requires an empty output directory')
    prior=None;resume_run=None
    if resume_from is not None:
        if media_max_tokens is not None or media_effort is not None:
            raise ValueError('resume preserves the original generation settings')
        resume_from=Path(resume_from).resolve()
        prior=read_json(resume_from/'acceptance.json')
        if (prior.get('actual_media_api') is not live_media or
            prior.get('scope')!='fixed approved teaching fixture; production media and composition stages'):
            raise ValueError('resume requires the same fixture and live/offline provider mode')
        workspace=open_workspace(prior.get('workspace_root') or resume_from/'ws')
        run_dir=Path(prior['run_directory']).resolve();resume_run=run_dir.name
        request=RunRequest.from_dict(read_json(run_dir/'request.json'))
        expected=workspace.root/'data/books'/request.book_id/'runs'/resume_run
        fixed=lambda r:{k:v for k,v in r.as_dict().items() if k not in ('budget','resume','idempotency_key')}
        if run_dir!=expected.resolve() or fixed(request)!=fixed(_request()):
            raise ValueError('resume run is outside the controlled teaching fixture')
    output.mkdir(parents=True,exist_ok=True);started=time.monotonic()
    if prior is None:
        workspace=setup_book(output);parse=workspace.root/'book/parse'
        for path in (parse/'original_text').glob('*.md'): path.write_text(BODY,encoding='utf-8')
        for path in (parse/'concepts.parts').glob('*.json'):
            row=read_json(path);row['_meta']['text_sha256']=digest_file(parse/'original_text'/(row['unit_id']+'.md'));write_json_atomic(path,row)
        runtime=default_services(workspace_root=ROOT)
        caps=read_json(workspace.config/'capabilities.json')
        caps['render_environment'].update(worker_dir=str(runtime.worker_dir),interpreter_candidates=[runtime.interpreter])
        request=_request()
    else:
        write_json_atomic(output/'prior-acceptance.json',prior)
        for name,path in (('prior-state.json',run_dir/'state.json'),
                          ('prior-media.json',run_dir/'work/products/figures'/(UNIT+'.json'))):
            if path.is_file(): write_json_atomic(output/name,read_json(path))
    browser,node,playwright=display_runtime(ROOT)
    if prior is None:
        caps['browser_acceptance']['playwright_module']=str(playwright)
        write_json_atomic(workspace.config/'capabilities.json',caps)
    config=read_json(ROOT/'config/models.json' if live_media and prior is None else workspace.config/'models.json')
    config={k:deepcopy(v) for k,v in config.items() if k not in ('api_key_ref','secrets_file')}
    overrides={}
    if media_max_tokens is not None: overrides['max_tokens']=media_max_tokens
    if media_effort is not None: overrides['effort']=media_effort
    # These optional acceptance settings affect only Python generation. The
    # independent judge retains its production model/effort/output limit.
    config['roles']['figures'].update(overrides)
    if prior is None: write_json_atomic(workspace.config/'models.json',config)
    media_roles={role:{key:config['roles'][role].get(key) for key in ('model','effort','max_tokens')}
                 for role in ('figures','judge')}
    cancel=CancelToken()
    if live_media:
        transport=LiveMediaTransport(model=config['roles']['figures']['model'],approved_budget_tokens=budget_tokens,
            base_url=config['base_url'],workspace_root=ROOT,cancel=cancel,
            approved_by='user:existing API authorization; bounded media acceptance')
        transport.evidence_out=output
    else: transport=CodeTransport()
    request=request.validate()
    previous=os.environ.get('AIFS_BROWSER_EXECUTABLE');os.environ['AIFS_BROWSER_EXECUTABLE']=str(browser)
    try:
        with _signal_cancellation(cancel):
            if resume_run:
                result,store,engine=resume_request(request,resume_run,workspace,providers=Providers(llm=transport),until='render',cancel=cancel)
            else:
                result,store,engine=run_request(request,workspace,providers=Providers(llm=transport),until='render',cancel=cancel)
    finally:
        if previous is None: os.environ.pop('AIFS_BROWSER_EXECUTABLE',None)
        else: os.environ['AIFS_BROWSER_EXECUTABLE']=previous
        write_json_atomic(output/'model-exchanges.json',transport.exchanges)
        observation={'external_api_calls':transport.live_calls_started if live_media else 0,
            'completed_api_responses':transport.calls if live_media else 0,
            'cancelled':cancel.cancelled,'cancel_reason':cancel.reason}
        if live_media: observation['reported_tokens']=transport.spent_tokens
        write_json_atomic(output/'execution-observation.json',observation)
    write_json_atomic(output/'engine-result.json',result.as_dict())
    report={'ok':result.exit_code==0,'exit_code':result.exit_code,
        'cancelled':cancel.cancelled or result.exit_code==130,'cancel_reason':cancel.reason,
        'wall_seconds':round(time.monotonic()-started,3),
        'run_directory':str(store.root),'external_api_calls':transport.live_calls_started if live_media else 0,
        'workspace_root':str(workspace.root),
        'completed_api_responses':transport.calls if live_media else 0,
        'budget_tokens':budget_tokens,'media_overrides':{'figures':overrides} if overrides else {},
        'media_roles':media_roles,
        'actual_media_api':live_media,'scope':'fixed approved teaching fixture; production media and composition stages',
        'limitations':['上游正文与正文审核使用明确的受控数学例；本报告不代表整章或任意教材质量。']}
    if prior is not None:
        report.update(resumed_from=str(resume_from),budget_scope='explicit observer only; production execution attempts remain persistent')
    manifest_path=store.work/'products/figures'/(UNIT+'.json')
    rows=[];errors=[]
    if manifest_path.is_file():
        manifest=read_json(manifest_path)['payload'];rows=manifest['requests']
        report['media']=[{'request_id':r['request_id'],'kind':r['kind'],'state':r['state'],
            'reason':r.get('reason'),'source_dir':r.get('source_dir'),
            'render_profile':r.get('render_profile'),'attempts':len(r.get('code_attempts',[]))} for r in rows]
    if sorted(r['kind'] for r in rows)!=['animation','drawing']:
        errors.append('未取得完整的一图一动画媒体清单')
    for row in rows:
        if row['state']!='fulfilled':
            errors.append(row['request_id']+' 尚未通过媒体审核')
            continue
        if row['kind']=='animation' and row.get('render_profile')!='final':
            errors.append(row['request_id']+' 尚未完成 final 渲染审核')
        if not 1<=len(row.get('code_attempts',[]))<=3:
            errors.append(row['request_id']+' 缺少有效的有界代码候选记录')
        try:
            validate_media_report(row['request'],row,row['validation'])
        except (ContractError,ValueError,KeyError,OSError) as exc:
            errors.append(row['request_id']+' 的当前批准凭据无效：'+str(exc))
    html=store.work/'legacy/render/CH01/index.html'
    if html.is_file():
        report['html']=str(html)
        try:
            # Preparation can replace the original parse directory or reuse a
            # frozen prior run. Use the same final context that rendered this run.
            report.update(_reader_projection(store,engine.services.context,rows))
        except (ContractError,ConfigError,OSError,ValueError,KeyError,IndexError) as exc:
            errors.append('HTML缺有效 ReaderProjection：'+str(exc))
    else:
        errors.append('未取得组合 HTML')
    if live_media: report['reported_tokens']=transport.spent_tokens
    report['media_review_recovery']=[read_json(path) for path in
        sorted((store.work/'media-review-recovery').glob('*.json'))]
    report['acceptance_errors']=errors
    # Keep known usage and an explicitly incomplete report if browser acceptance
    # is interrupted. Exit 0 alone must never stand in for accepted products.
    write_json_atomic(output/'acceptance.json',{**report,'ok':False,'browser_pending':not errors and result.exit_code==0})
    if not errors and result.exit_code==0:
        try:
            report['browser']=browser_check(html,output,browser,node,playwright,[r['request_id'] for r in rows])
        except (AssertionError,RuntimeError,OSError,ValueError) as exc:
            report['browser_error']=str(exc)
            errors.append('浏览器验收失败：'+str(exc))
    report['ok']=result.exit_code==0 and not errors and bool(report.get('browser'))
    report['wall_seconds']=round(time.monotonic()-started,3)
    write_json_atomic(output/'acceptance.json',report)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--live-media',action='store_true')
    parser.add_argument('--resume-from',type=Path,help='Previous acceptance directory; preserve its workspace, code candidates and cumulative run budget')
    parser.add_argument('--budget-tokens',type=int,default=1_000_000)
    parser.add_argument('--media-max-tokens',type=int,default=None,
                        help='Acceptance-only figures output limit; judge and production config are unchanged')
    parser.add_argument('--media-effort',choices=('none','low','medium','high'),default=None,
                        help='Acceptance-only figures thinking effort; omitted uses production config')
    args=parser.parse_args(argv)
    answer=run_acceptance(args.out,live_media=args.live_media,budget_tokens=args.budget_tokens,
                          media_max_tokens=args.media_max_tokens,media_effort=args.media_effort,
                          **({'resume_from':args.resume_from} if args.resume_from else {}))
    print(json.dumps(answer,ensure_ascii=False,indent=2))
    return 0 if answer['ok'] else int(answer.get('exit_code') or 1)


if __name__=='__main__':
    sys.exit(main())
