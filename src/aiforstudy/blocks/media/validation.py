"""Single media acceptance service: real bytes, target-size pixels, frozen criteria.

A renderer's successful execution is not an approval. This service independently
collects current resource evidence and gives the same immutable items to the
reviewer on first review and every repair. Missing review stays needs_review.
"""
from __future__ import annotations
from copy import deepcopy
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess

from ...core.digest import digest_file, digest_of, digest_text
from ...core.review_mode import reviews_frozen, frozen_review, substantive_reviews_enabled
from ...errors import CapabilityError, ContractError
from ...contracts.review import MEDIA_VERDICT_STATUSES
from .svg_safety import safe_svg as _safe_svg
from ...core.resource_files import verified_resource_file as _safe_file
from .evidence_digest import manifest_digest
from .media_requirements import validate_criteria
from .review_policy import (DEFAULT_DISPLAY_PROFILES, code_review_criteria,
                            media_review_policy, build_media_review_prompt,
                            animation_step_caption_criterion)


def _is_code_product(result):
    program=result.get('candidate_spec')
    return ('code_product' in result or result.get('kind')=='drawing'
            or isinstance(program,dict) and program.get('schema_version')=='GeneratedMediaCodeV1')


def _validate_code_binding(request,result):
    """Validate frozen input identity, never infer correctness of Python pixels."""
    from .code_contracts import validate_media_code
    from ..math import parse_math_model
    product=result.get('code_product')
    fields={'schema_version','program_hash','code_hash','context_digest','backend',
            'input_binding_status','mathematical_status','object_bindings','steps'}
    if not isinstance(product,dict) or set(product)!=fields or product.get('schema_version')!='GeneratedMediaProductV1':
        raise ContractError('缺完整 GeneratedMediaProductV1 冻结代码产物')
    program=validate_media_code(result.get('candidate_spec'),request)
    if (product['program_hash']!=digest_of(program) or result.get('spec_hash')!=product['program_hash']
            or product['code_hash']!=digest_text(program['code']) or product['backend']!=program['backend']
            or product['object_bindings']!=program['object_bindings'] or product['steps']!=program['steps']):
        raise ContractError('代码/程序摘要或对象/步骤绑定与当前产物不一致')
    if (not isinstance(product['context_digest'],str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',product['context_digest'])
            or product['context_digest']!=result.get('math_context_digest')):
        raise ContractError('代码产物未绑定当前批准数学上下文摘要')
    if product['input_binding_status']!='pass' or product['mathematical_status']!='requires_visual_review':
        raise ContractError('代码产物只可声明输入绑定，不能自报画面数学通过')
    dependencies=result.get('approved_dependencies') or []
    expected=request.get('dependency_paths') or []
    if (not isinstance(dependencies,list) or any(not isinstance(d,dict) for d in dependencies)
            or not expected or len(dependencies)!=len(expected)
            or {d.get('pointer') for d in dependencies}!={d.get('pointer') for d in expected}
            or any(d.get('hash')!=next((e.get('hash') for e in expected if e.get('pointer')==d.get('pointer')),None)
                   or digest_of(d.get('value'))!=d.get('hash') for d in dependencies)):
        raise ContractError('代码审核缺冻结批准依赖或依赖值/摘要已变化')
    models={}
    for dep in dependencies:
        if re.fullmatch(r'/math_models/\d+',dep['pointer']):
            model=parse_math_model(dep['value'])
            if model.model_id in models: raise ContractError('重复批准数学模型证据')
            models[model.model_id]=model
    for ref in request.get('math_model_refs') or []:
        if ref['model_id'] not in models or models[ref['model_id']].digest!=ref['digest']:
            raise ContractError('代码缺当前数学模型的批准内容与摘要')
    for binding in product['object_bindings']:
        if binding['model_id'] not in models: raise ContractError('代码对象缺批准模型证据')
        models[binding['model_id']].object_by_id(binding['object_id'])
    if request['kind']=='animation':
        animation=result.get('animation') or {}
        if animation.get('spec_hash')!=product['program_hash']:
            raise ContractError('实际动画未绑定当前代码程序摘要')
        actual=animation.get('steps')
        if not isinstance(actual,list) or len(actual)!=len(program['steps']):
            raise ContractError('实际动画步骤遗漏或新增，未覆盖冻结作者步骤')
        previous_end=0
        for declared,step in zip(program['steps'],actual):
            if not isinstance(step,dict) or any(step.get(k)!=v for k,v in declared.items()):
                raise ContractError('实际动画步骤/作者索引/字幕/观察与冻结程序不一致')
            start,end=step.get('start'),step.get('end')
            if (type(start) not in (int,float) or type(end) not in (int,float)
                    or not 0<=start<end or abs(start-previous_end)>.002):
                raise ContractError('实际动画步骤时间缺失、不连续或无效')
            previous_end=end
    return product


def _code_math_report(result,*,accepted,input_ok,verdicts):
    product=result.get('code_product') or {}
    if not isinstance(product,dict): product={}
    return {'status':'pass' if accepted else 'needs_review',
            'basis':'approved_inputs_and_visual_review' if accepted else 'approved_inputs_only' if input_ok else 'unverified_inputs',
            'input_binding_status':'pass' if input_ok else 'fail','formal_proof':False,
            'program_hash':product.get('program_hash'),'context_digest':product.get('context_digest'),
            'criterion_id':'host.code.mathematical-correspondence',
            'evidence_ids':next((v.get('evidence_ids',[]) for v in verdicts
                                 if v.get('criterion_id')=='host.code.mathematical-correspondence'),[])}


def result_digest(result):
    """State/reason are consequences of validation, never its circular inputs."""
    excluded={'validation','review','state','reason','review_outcome','evidence_only','repair_instructions','cache_hit','media_review','code_attempts'}
    value=deepcopy({k:v for k,v in result.items() if k not in excluded})
    for animation in [value.get('animation')]+list((value.get('profile_results') or {}).values()):
        if isinstance(animation,dict):
            animation.pop('status',None);animation.pop('frame_report',None)
            if _is_code_product(result): animation.pop('math_report',None)
    return manifest_digest({'requests':[value]})


def _profiles(profiles, policy):
    values=deepcopy(list(profiles or DEFAULT_DISPLAY_PROFILES))
    host_widths={p['width']:p['content_width'] for p in DEFAULT_DISPLAY_PROFILES}
    ids=[]
    for p in values:
        if (not isinstance(p,dict) or not isinstance(p.get('profile_id'),str)
                or not p['profile_id'] or any(type(p.get(k)) is not int or p[k]<=0 for k in ('width','height','content_width'))
                or not 0<p['content_width']<=p['width']-32
                or p.get('min_font_px',0)<policy['minimum_label_css_px']):
            raise ContractError('invalid target display profile')
        if p['width'] in host_widths and p['content_width']>host_widths[p['width']]:
            raise ContractError('target display profile exceeds published desktop column after scrollbar allowance')
        ids.append(p['profile_id'])
    if len(ids)!=len(set(ids)) or not set(policy['required_viewports'])<={p['width'] for p in values}:
        raise ContractError('media validation requires unique profiles covering the desktop viewport')
    return values


def media_review_items(request, result, policy):
    validate_criteria(request)
    if request.get('criteria_digest')!=digest_of(request['acceptance_criteria']):
        raise ContractError('frozen media criteria digest mismatch')
    rows=deepcopy(request['acceptance_criteria'])+deepcopy(policy['criteria'])
    if _is_code_product(result): rows.extend(code_review_criteria(animation=request.get('kind')=='animation'))
    if request.get('kind')=='animation':
        rows.append(animation_step_caption_criterion())
    for ticket in result.get('repair_tickets') or []:
        if ticket.get('kind')=='evidence_format': continue
        for criterion in ticket.get('criteria') or []:
            if not criterion.get('required',True): continue
            rows.append({'criterion_id':'ticket:'+ticket['ticket_id']+':'+criterion['criterion_id'],
                'version':str(criterion['version']),'kind':'visual','statement':criterion['expected'],
                'evidence_required':['rendered_frame'],'ticket_id':ticket['ticket_id'],
                'frozen_criterion':deepcopy(criterion)})
    ids=[r['criterion_id'] for r in rows]
    if len(ids)!=len(set(ids)): raise ContractError('duplicate media criterion identity')
    return [{**r,'required_evidence_kinds':r['evidence_required']} for r in rows]


def _review_context(report):
    return {key:report[key] for key in ('request_hash','result_hash','criteria_digest',
        'policy_digest','display_profiles_hash','review_items')}


def media_evidence_groups(catalog, *, review_context):
    """Declare only complete, current frame/caption groups; never infer a citation."""
    if not isinstance(review_context,dict):
        raise ContractError('evidence groups require the current frozen review context')
    if not any(entry.get('kind')=='rendered_frame' for entry in catalog.values()):
        return {}
    binding=digest_of({'review_context':review_context,'evidence_catalog':catalog}).removeprefix('sha256:')
    groups={}
    for name,kind in (('all_rendered_frames','rendered_frame'),('all_captions','captions')):
        members=sorted(eid for eid,entry in catalog.items() if entry.get('kind')==kind)
        if members:
            groups['group:'+name+':'+binding]={'name':name,'kind':kind,'evidence_ids':members}
    return groups


def validate_verdicts(items, response, catalog, *, evidence_groups=None, review_context=None):
    if substantive_reviews_enabled():
        return _substantive_verdicts(response)
    if reviews_frozen():
        return {**frozen_review(),'ok':None,'decision':'unverified','verdicts':[],'issues':[],'skipped':True}
    issues=[]; verdicts=[]
    def fail(code,message): issues.append({'code':code,'stage':'figures','owner':'reviewer','message':message})
    raw=response.get('verdicts') if isinstance(response,dict) else None
    original=deepcopy(raw)
    groups={}
    if evidence_groups is not None:
        try:
            expected_groups=media_evidence_groups(catalog,review_context=review_context)
        except (ContractError,ValueError,TypeError,KeyError):
            expected_groups=None
        if not isinstance(evidence_groups,dict) or evidence_groups!=expected_groups:
            fail('MEDIA_REVIEW_EVIDENCE','证据组身份或完整成员与本次冻结审核不一致')
        else:
            groups=evidence_groups
    if not isinstance(raw,list):
        fail('MEDIA_REVIEW_FORMAT','审核必须返回逐冻结标准 verdicts；bool/散文不能批准')
        raw=[]
    expected={i['criterion_id']:i for i in items}; seen=[]
    code_review=any(ident.startswith('host.code.') for ident in expected)
    for row in raw:
        if not isinstance(row,dict): fail('MEDIA_REVIEW_FORMAT','verdict 必须为对象'); continue
        row=deepcopy(row)
        ident=row.get('criterion_id')
        if not isinstance(ident,str): fail('MEDIA_REVIEW_FORMAT','criterion_id 必须为字符串');continue
        item=expected.get(ident); seen.append(ident)
        if item is None: fail('MEDIA_REVIEW_FORMAT','未知标准 '+str(ident));continue
        refs=row.get('evidence_ids')
        if row.get('version')!=item['version'] or row.get('status') not in MEDIA_VERDICT_STATUSES or not isinstance(row.get('why'),str) or not row['why'].strip():
            fail('MEDIA_REVIEW_FORMAT','标准版本、状态或观察依据无效 '+ident)
        if (not isinstance(refs,list)
                or any(not isinstance(r,str) or (r not in catalog and r not in groups) for r in refs)):
            fail('MEDIA_REVIEW_EVIDENCE','证据引用无效 '+ident);refs=[]
        else:
            # Expand only explicit valid choices. Overlap with a specific frame is
            # useful for locating a problem; an invalid sibling ref is never removed.
            refs=list(dict.fromkeys(eid for ref in refs
                for eid in (groups[ref]['evidence_ids'] if ref in groups else [ref])))
            row['evidence_ids']=refs
        # References locate observations; they do not prove review coverage.
        # validate_media and validate_media_report enforce actual frame/profile
        # completeness independently of the reviewer's optional citation list.
        if row.get('status')!='pass':
            if code_review and row.get('status') == 'fail' and any(not isinstance(row.get(key),str) or not row[key].strip() for key in ('problem','action','expected')):
                fail('MEDIA_REVIEW_FORMAT','未通过判词须包含具体问题、修改建议和通过标准 '+ident)
            issues.append({'code':'MEDIA_CRITERION_UNRESOLVED','stage':'figures','owner':'visual_planner',
                'criterion_id':ident,'message':'标准未通过 '+ident+'：'+str(row.get('problem') or row.get('why') or ''),
                'problem':row.get('problem') or row.get('why') or '缺逐标准实际观察依据',
                'action':row.get('action') or '补齐该标准的真实画面证据及具体修改建议，再按原标准复核',
                'expected':item['statement'],'evidence_ids':deepcopy(refs)})
        verdicts.append(deepcopy(row))
    if len(seen)!=len(set(seen)) or set(seen)!=set(expected):
        fail('MEDIA_REVIEW_COVERAGE','标准遗漏、重复或额外标准')
    if isinstance(response,dict) and response.get('problems'):
        fail('MEDIA_REVIEW_PROBLEMS','审核仍含未解决问题')
    return {'ok':not issues,'verdicts':verdicts,'issues':issues,
        **({'raw_verdicts':original} if evidence_groups is not None else {})}


def _replay_report_verdicts(report, items, *, require_groups=False):
    """Reproduce persisted normalization for approval and independent failures."""
    catalog=report.get('evidence_catalog') or {}
    if 'evidence_groups' not in report and 'raw_verdicts' not in report:
        if require_groups or report.get('policy_digest')==digest_of(media_review_policy()):
            raise ContractError('current media review requires original verdicts and host groups')
        return validate_verdicts(items,{'verdicts':report.get('verdicts'),
            'problems':report.get('review_problems')},catalog)
    if 'evidence_groups' not in report or 'raw_verdicts' not in report:
        raise ContractError('media group review requires original verdicts and host groups')
    context=_review_context(report)
    if report['evidence_groups']!=media_evidence_groups(catalog,review_context=context):
        raise ContractError('media evidence groups are stale or their complete membership changed')
    checked=validate_verdicts(items,{'verdicts':report['raw_verdicts'],
        'problems':report.get('review_problems')},catalog,
        evidence_groups=report['evidence_groups'],review_context=context)
    if checked['verdicts']!=report.get('verdicts'):
        raise ContractError('media verdicts do not match explicitly selected evidence groups')
    return checked


def actionable_media_failures(report, *, items, include_needs_review=False):
    if substantive_reviews_enabled():
        # Rule 2026-10-16: a needs_review verdict is repairable only when the
        # reviewer gave a concrete fix in `action`; pure uncertainty stays recorded.
        def repairable(row):
            if row.get('status') == 'fail':
                return True
            return (include_needs_review and row.get('status') == 'needs_review'
                    and isinstance(row.get('action'), str) and row['action'].strip() != '')
        return [deepcopy(row) for row in report.get('verdicts', [])
                if isinstance(row, dict) and repairable(row)
                and _verdict_reason(row) and row.get('target_artifact_kind') != 'content']
    if reviews_frozen(): return []
    """Return independently valid failures for revision, never for approval.

    ``items`` must come from the host's current frozen request, not the model's
    response or the report's claimed outcome. A defective sibling verdict does
    not erase a supported failure; a content discovery still takes precedence.
    """
    if any(isinstance(p,dict) and p.get('target_artifact_kind')=='content'
           for p in report.get('review_problems',[])):
        return []
    raw=report.get('verdicts');catalog=report.get('evidence_catalog')
    if not isinstance(raw,list) or not isinstance(catalog,dict): return []
    if ('evidence_groups' in report or 'raw_verdicts' in report
            or report.get('policy_digest')==digest_of(media_review_policy())):
        try:
            raw=_replay_report_verdicts(report,report.get('review_items') or items)['verdicts']
        except (ContractError,ValueError,TypeError,KeyError):
            return []
    failures=[]
    for item in items:
        matches=[row for row in raw if isinstance(row,dict) and row.get('criterion_id')==item['criterion_id']]
        if len(matches)!=1: continue
        row=matches[0]
        if row.get('status')!='fail' or any(not isinstance(row.get(key),str) or not row[key].strip()
                                          for key in ('problem','action','expected')):
            continue
        checked=validate_verdicts([item],{'verdicts':[row],'problems':[]},catalog)
        if [issue['code'] for issue in checked['issues']]!=['MEDIA_CRITERION_UNRESOLVED']:
            continue
        failures.append({**deepcopy(row),'expected':item['statement']})
    return failures





def display_runtime(workspace_root=None):
    """Only declared workspace dependencies, PATH or explicit environment overrides."""
    from ...checks.offline import _browser_executable
    from ...core.workspace import open_workspace, Workspace
    import shutil
    workspace=Workspace(Path(workspace_root)) if workspace_root is not None else open_workspace()
    browser=_browser_executable(workspace.root)
    node=Path(os.environ.get('AIFS_BROWSER_NODE') or shutil.which('node') or '')
    config=(workspace.config_json('capabilities.json') if (workspace.config/'capabilities.json').is_file() else {}).get('browser_acceptance',{})
    playwright=workspace.path(os.environ.get('AIFS_PLAYWRIGHT_MODULE') or config.get('playwright_module','node_modules/playwright'))
    if browser is None or not node.is_file() or not playwright.exists():
        raise CapabilityError('target-size media evidence requires Chromium, Node and workspace Playwright (npm ci); use explicit AIFS_BROWSER_NODE/AIFS_PLAYWRIGHT_MODULE overrides if needed')
    return browser,node,playwright


def capture_display(path, *, profiles, out_dir, workspace_root=None):
    """Real Chromium DOM measurements and screenshots; never a synthetic pass."""
    path=Path(path);out_dir=Path(out_dir);out_dir.mkdir(parents=True,exist_ok=True)
    browser,node,playwright=display_runtime(workspace_root)
    from ..render.display_policy import reader_display_css, reader_display_policy
    from ...pipeline.stage_io import _repo_root
    job={'profiles':profiles,'out_dir':str(out_dir.resolve()),
         'display_policy_css':reader_display_css(), 'display_policy':reader_display_policy(),
         'media_surface_css':(_repo_root()/'blocks/render/assets/media-surface.css').read_text(encoding='utf-8')}
    if path.suffix.lower()=='.svg': job['svg']=_safe_svg(path)
    else:
        from PIL import Image
        with Image.open(path) as img: img.verify()
        job.update(image=base64.b64encode(path.read_bytes()).decode('ascii'),mime='image/png' if path.suffix.lower()=='.png' else 'image/jpeg')
    jobpath=out_dir/'probe-input.json';jobpath.write_text(json.dumps(job),encoding='utf-8')
    # 修改计划 D5：长 TMPDIR 会让 Chromium 的 profile/socket 路径超长而 SIGTRAP。
    # 用短临时目录作 TMPDIR（Playwright 的 profile 目录也落在这里）；
    # 完整 stderr 留成文件，报错只带末尾摘要与文件路径；超时同样记为截图环境失败。
    import tempfile
    stderr_path=out_dir/'probe-stderr.log'
    with tempfile.TemporaryDirectory(prefix='aifs-',dir='/tmp' if Path('/tmp').is_dir() else None) as scratch:
        env={**os.environ,'TMPDIR':scratch}
        try:
            proc=subprocess.run([str(node),str(Path(__file__).with_name('display_probe.cjs')),str(playwright),str(browser),str(jobpath)],
                                capture_output=True,text=True,timeout=60,env=env)
        except subprocess.TimeoutExpired as exc:
            stderr=exc.stderr.decode('utf-8','replace') if isinstance(exc.stderr,bytes) else (exc.stderr or '')
            stderr_path.write_text(stderr,encoding='utf-8')
            raise CapabilityError(f'target-size media browser timed out after {exc.timeout}s; stderr: {stderr_path}') from exc
    stderr_path.write_text(proc.stderr or '',encoding='utf-8')
    if proc.returncode:
        raise CapabilityError('target-size media browser failed: '+(proc.stderr or '')[-1500:]+'; full stderr: '+str(stderr_path))
    values=json.loads(proc.stdout)
    for row in values: row['image_hash']=digest_file(row['image_path'])
    return values


def capture_preflight(out_dir, *, workspace_root=None):
    """修改计划 D5：买候选前先用宿主自带的小图试拍一次；失败抛 CapabilityError。"""
    from PIL import Image
    out_dir=Path(out_dir);out_dir.mkdir(parents=True,exist_ok=True)
    sample=out_dir/'capture-preflight.png'
    Image.new('RGB',(320,200),'white').save(sample)
    from .review_policy import DEFAULT_DISPLAY_PROFILES
    return capture_display(sample,profiles=list(DEFAULT_DISPLAY_PROFILES)[:1],out_dir=out_dir/'display',
                           workspace_root=workspace_root)


def _verdict_reason(row):
    return next((row[key] for key in ('reason', 'why', 'problem')
                 if isinstance(row.get(key), str) and row[key].strip()), '')


def _substantive_verdicts(response):
    """Read semantic conclusions without binding their wording to host IDs."""
    from ..content.generation_corrections import LocalCorrectionRequired
    rows = response.get('verdicts') if isinstance(response, dict) else None
    problems = response.get('problems', []) if isinstance(response, dict) else []
    problem_rows = [p for p in problems if isinstance(p, dict) and _verdict_reason(p)] if isinstance(problems, list) else []
    usable = [r for r in rows if isinstance(r, dict) and r.get('status') in
              ('pass', 'fail', 'needs_review', 'unknown')] if isinstance(rows, list) else []
    if not usable and not problem_rows:
        raise LocalCorrectionRequired('审核答复缺可理解的实质结论', kind='schema', allowed_paths=['/'],
            errors=[{'path': '/verdicts', 'code': 'review_response',
                     'fix': '返回 verdicts 数组，每项写 status=pass/fail/needs_review 和 reason；'
                            '明确问题写出画面位置、原因和修改要求，不改判词为通过。'}])
    # A discovered prose error belongs to the lesson repair route. It cannot
    # turn an otherwise correct image into a known visual error.
    media_rows = [r for r in usable if r.get('target_artifact_kind') != 'content']
    media_problems = [p for p in problems if isinstance(p, dict) and
                      p.get('target_artifact_kind') != 'content'] if isinstance(problems, list) else []
    issues = []
    for row in media_rows:
        if row['status'] != 'pass':
            reason = _verdict_reason(row)
            issues.append({'code': 'MEDIA_CRITERION_UNRESOLVED', 'stage': 'figures',
                           'owner': 'visual_planner', 'message': reason or '审核未说明可理解的具体问题。'})
    # Preserve partial responses and expose their uncertainty, never discard a
    # concrete failure because another row has an unfamiliar shape.
    if isinstance(rows, list) and len(usable) != len(rows):
        issues.append({'code': 'MEDIA_REVIEW_UNKNOWN', 'message': '部分审核答复无法理解，原判词已保留。'})
    for problem in media_problems:
        if problem.get('status') != 'pass':
            issues.append({'code': 'MEDIA_CRITERION_UNRESOLVED',
                           'message': _verdict_reason(problem) or '审核提出待处理问题。'})
    if not media_rows and not media_problems:
        issues.append({'code': 'MEDIA_REVIEW_UNKNOWN', 'message': '审核仅指出正文问题，未给出媒体本身的结论。'})
    definite = any(r['status'] == 'fail' and _verdict_reason(r) for r in media_rows)
    definite = definite or any(p.get('status') not in ('unknown','needs_review','pass') and _verdict_reason(p)
                               for p in media_problems)
    status = 'known_error' if definite else 'unverified' if issues else 'verified'
    return {'ok': not issues, 'decision': 'needs_review' if issues else 'accepted',
            'verdicts': deepcopy(usable), 'raw_verdicts': deepcopy(rows), 'issues': issues,
            'review_problems': deepcopy(problems if isinstance(problems,list) else []), 'review_outcome': {
                'status': status, 'reasons': [i['message'] for i in issues],
                'review_mode': 'balanced', 'review_status': 'completed'}}


def _substantive_targets(request, result, base, workspace_root):
    """Observe final bytes; animation samples never claim complete video coverage."""
    files = [(r, _safe_file(base, r)) for r in result.get('resources', [])]
    if request.get('kind') != 'animation':
        primary = next(((r, p) for r, p in files if r.get('kind') in ('svg', 'image')), None)
        if primary is None:
            raise ContractError('缺可显示的实际图片')
        return [primary], []
    from .code_runtime import _video, _frame, default_services
    from .presentation_policy import RENDER_PROFILES
    import math
    video = next(((r, p) for r, p in files if r.get('kind') == 'mp4'), None)
    if video is None:
        raise ContractError('缺实际 MP4 动画')
    resource, path = video
    services = default_services(workspace_root=workspace_root)
    info, seconds, count = _video(path, services=services, quality=RENDER_PROFILES['final'])
    stream = next(s for s in info['streams'] if s.get('codec_type') == 'video')
    numerator, denominator = stream['r_frame_rate'].split('/')
    fps = float(numerator) / float(denominator)
    end = max(0., seconds - 1 / fps)
    steps = (result.get('animation') or {}).get('steps', result.get('steps', []))
    moments = []
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        start, stop = step.get('start'), step.get('end')
        if (type(start) in (float, int) and type(stop) in (float, int)
                and math.isfinite(start) and math.isfinite(stop)
                and 0 <= start < seconds and start < stop <= seconds + .002):
            # Use the actual media_step clock, not uniform or transition samples.
            # A tiny renderer-clock tail must not silently discard the last step.
            at = min(end, max(start, min(stop, seconds) - 1 / fps))
            moments.append({**{key: deepcopy(step[key]) for key in
                ('step_id', 'caption', 'observation', 'expected_change') if key in step},
                'timestamp': math.floor((at + 1e-9) * 1000) / 1000})
    limited = not moments
    if limited:
        # Old/malformed optional timing does not create a new rejection gate.
        # This final image is explicitly limited evidence, not a full step review.
        moments = [{'timestamp': math.floor((end + 1e-9) * 1000) / 1000}]
    moments.sort(key=lambda item: item['timestamp'])
    targets = []
    for index, moment in enumerate(moments):
        sample = base / '.validation' / 'video-samples' / f'{index:03d}.png'
        _frame(path, sample, moment['timestamp'], services.ffmpeg)
        targets.append(({'relative_path': resource['relative_path'], 'sha256': digest_file(path),
                         **moment}, sample))
    return targets, [{'name': 'animation.actual_samples', 'ok': True,
                      'detail': ('最终视频已解码；无可用分步时点，仅查看实际末帧，不能代表必要中间状态或全程。'
                                 if limited else '最终视频已解码；仅取已记录讲解步骤结束前的画面，不取均匀或起中末样本；时点本身不证明画面稳定，也不证明全程运动或字幕同步。'),
                      'evidence': {'duration_seconds': seconds, 'decoded_frames': count,
                                   'sample_times_seconds': [m['timestamp'] for m in moments]}}]


def _substantive_image_label(request, resource, frame_id):
    """Readable text immediately beside its matching evidence image."""
    if request.get('kind') != 'animation':
        return f"{frame_id}｜电脑显示的静态成品｜{request.get('caption') or request.get('request_id', '')}"
    at = resource.get('timestamp')
    time_label = f'{at:.3f} s' if isinstance(at, (float, int)) else '时间未提供'
    if not resource.get('step_id'):
        return f'{frame_id}｜电脑显示｜{time_label}｜无可用分步时点，仅核对此实际画面，不代表中间步骤或全程。'
    meaning = '；'.join(str(resource[key]) for key in ('caption', 'observation', 'expected_change')
                        if resource.get(key)) or '未提供步骤含义，只按所见画面核对。'
    return f"{frame_id}｜电脑显示｜步骤 {resource['step_id']}｜{time_label}｜步骤结束前画面；讲解含义：{meaning}"


def _validate_substantive_media(request, result, *, content, client, workspace_root):
    from ..content.generation_corrections import run_local_generation, _accepts_protocol_errors
    from ...core.prompt_assets import compose_prompt_assets
    from ...pipeline.services import reraise_control_error
    from ...core.jsonio import write_json_atomic
    base = Path(result.get('source_dir') or '').resolve()
    report = {'schema_version': 'MediaValidationV2', 'request_id': request.get('request_id'),
              'result_hash': result_digest(result), 'review_mode': 'balanced',
              'display_profiles': deepcopy(list(DEFAULT_DISPLAY_PROFILES)),
              'evidence_catalog': {}, 'technical_checks': [], 'verdicts': [], 'raw_verdicts': [],
              'review_problems': [], 'issues': [], 'warnings': [], 'review_failure': {},
              'ok': None, 'decision': 'needs_review'}
    phase='resources'
    journal=None
    try:
        targets, checks = _substantive_targets(request, result, base, workspace_root)
        report['technical_checks'] = checks
        if request.get('kind') == 'animation':
            # Existing subtitle files are evidence, not required metadata. Read
            # their actual bytes rather than substituting storyboard captions.
            for index, resource in enumerate(result.get('resources', [])):
                if resource.get('kind') not in ('captions', 'transcript'):
                    continue
                path = _safe_file(base, resource)
                report['evidence_catalog']['captions:' + str(index)] = {
                    'kind': 'captions', 'caption_form': resource['kind'],
                    'text': path.read_text(encoding='utf-8', errors='replace'),
                    'path': str(path), 'sha256': resource['sha256']}
        images, image_labels = [], []
        phase='capture'
        for index, (resource, path) in enumerate(targets):
            for capture in capture_display(path, profiles=report['display_profiles'],
                    out_dir=base/'.validation'/f'balanced-display-{index}', workspace_root=workspace_root):
                image_path = capture['image_path']
                images.append(image_path)
                frame_id = 'frame:' + str(len(images))
                label = _substantive_image_label(request, resource, frame_id)
                image_labels.append(label)
                report['evidence_catalog'][frame_id] = {
                    **capture, 'kind': 'rendered_frame', 'path': image_path,
                    'image_label': label,
                    'source_path': resource['relative_path'], 'source_time_seconds': resource.get('timestamp')}
        if not images:
            raise ContractError('没有可交给审核员的实际画面')
        phase='review'
        if client is None:
            raise CapabilityError('成品审核缺模型传输能力')
        from ...providers.capabilities import require_image_input
        require_image_input(client.chat_json)
        payload = {'role_task': 'media_substantive_review', 'request': deepcopy(request), 'content': deepcopy(content),
                   'display_profiles': report['display_profiles'],
                   'evidence_catalog': report['evidence_catalog'], 'technical_checks': checks}
        if request.get('kind') == 'animation':
            steps = (result.get('animation') or {}).get('steps', result.get('steps'))
            if steps is not None:
                payload['animation'] = {'steps': deepcopy(steps)}
        messages = [{'role': 'system', 'content': compose_prompt_assets('media/review-balanced.md').text},
                    {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False),
                     'image_labels': image_labels}]
        journal = base / '.validation' / ('review-corrections-' + digest_of(messages)[7:23] + '.json')
        # Reuse the existing finite correction journal, including the original
        # response. Gateway omits images only for explicit syntax-only repairs.
        class ImageReviewClient:
            def chat_json(self, messages, **kwargs):
                for option in ('protocol_errors', 'syntax_repair'):
                    if not _accepts_protocol_errors(client, option):
                        kwargs.pop(option, None)
                return client.chat_json(messages, images=images, **kwargs)
        def remember(raw):
            report['raw_verdicts'] = deepcopy(raw.get('verdicts')) if isinstance(raw, dict) else deepcopy(raw)
        checked = run_local_generation(messages, ImageReviewClient(), _substantive_verdicts,
            tag='media-validation:' + request['request_id'],
            correction_tag='media-review-format:' + request['request_id'], max_corrections=2,
            correction_journal=journal, response_sink=remember, correction_guard=False)
        report.update(checked)
    except Exception as exc:
        reraise_control_error(exc)
        from ...providers.review_failures import classify_review_failure
        report['review_failure'] = {'kind': classify_review_failure(exc) if phase=='review' else phase,
                                    'reason': type(exc).__name__ + ': ' + str(exc)}
        code='MEDIA_RESOURCE_INVALID' if phase=='resources' else 'MEDIA_CAPTURE_FAILED' if phase=='capture' else 'MEDIA_CALL_FAILED'
        report['issues'].append({'code': code, 'message': report['review_failure']['reason']})
        if phase=='resources':
            report.update(ok=False,decision='blocked')
        if phase=='capture':
            # 修改计划 D5：截图环境失败不是画错，不触发重画；恢复时只补截图与审核。
            report.update(ok=False,decision='blocked',capture_failed=True)
        if journal is not None and journal.is_file():
            from ...core.jsonio import read_json_soft
            attempts=(read_json_soft(journal) or {}).get('attempts',[])
            if attempts and 'response' in attempts[-1]:
                raw=attempts[-1]['response']
                report['raw_verdicts']=deepcopy(raw.get('verdicts')) if isinstance(raw,dict) else deepcopy(raw)
        report['review_outcome'] = {'status': 'unverified', 'reasons': [i['message'] for i in report['issues']],
                                    'review_mode': 'balanced', 'review_status': 'incomplete'}
    write_json_atomic(base / '.validation' / 'substantive-review.json', report)
    return report


def validate_media(request,result,*,policy=None,display_profiles=None,client=None,workspace_root=None,content=None):
    if substantive_reviews_enabled():
        return _validate_substantive_media(request, result, content=content, client=client, workspace_root=workspace_root)
    if reviews_frozen():
        return {**frozen_review(),'ok':None,'decision':'unverified','verdicts':[],'issues':[],
                'review_problems':[],'skipped':True}
    policy=deepcopy(policy or media_review_policy(request['kind']));profiles=_profiles(display_profiles,policy)
    items=media_review_items(request,result,policy)
    base=Path(result.get('source_dir') or '').resolve();catalog={};issues=[];warnings=[];checks=[];images=[]
    code_product=_is_code_product(result);code_input_ok=False
    def issue(code,message,owner='visual_planner'):
        issues.append({'code':code,'stage':'figures','owner':owner,'message':message,
            'action':'修复当前请求对应资源或证据后用同一冻结标准重新验收','expected':'本项真实检查及逐标准审核通过'})
    def evidence(kind,data,ident=None):
        eid=ident or kind+':'+str(len(catalog));catalog[eid]={'kind':kind,**data};return eid
    resources=result.get('resources') or [];files=[]
    if result.get('request_id')!=request['request_id'] or result.get('kind')!=request['kind'] or result.get('request_hash')!=digest_of(request):
        issue('MEDIA_REQUEST_BINDING','制作结果未绑定当前请求')
    if result.get('criteria_digest')!=request['criteria_digest']: issue('MEDIA_CRITERIA_BINDING','制作结果未绑定冻结标准')
    if not resources: issue('MEDIA_RESOURCE_INVALID','缺实际资源')
    for r in resources:
        try: files.append((r,_safe_file(base,r)))
        except ContractError as exc: issue('MEDIA_RESOURCE_INVALID',str(exc))
    dependencies=result.get('approved_dependencies') or []
    expected_dependencies={d['pointer']:d['hash'] for d in request.get('dependency_paths') or []}
    if expected_dependencies:
        if (not isinstance(dependencies,list) or any(not isinstance(d,dict) for d in dependencies)
                or len(dependencies)!=len(expected_dependencies)
                or {d.get('pointer') for d in dependencies}!=set(expected_dependencies)
                or any(d.get('hash')!=expected_dependencies.get(d.get('pointer')) or digest_of(d.get('value'))!=d.get('hash') for d in dependencies)):
            issue('MEDIA_APPROVED_CONTEXT','审核缺当前批准的实际依赖字段/引文')
    evidence('request',{'data':deepcopy(request),'approved_dependencies':deepcopy(dependencies)})
    if result.get('source_provenance'): evidence('source_provenance',{'data':deepcopy(result['source_provenance'])})
    for name in ('caption','alt_text','source_refs'):
        if request.get(name): evidence(name,{'data':deepcopy(request[name])})
    product=result.get('static_product') or {}
    if code_product:
        try:
            _validate_code_binding(request,result)
            code_input_ok=True
        except (ContractError,TypeError,ValueError,KeyError) as exc:
            issue('MEDIA_CODE_BINDING',str(exc))
        generated=result.get('code_product') or {}
        if not isinstance(generated,dict): generated={}
        evidence('approved_dependencies',{'data':deepcopy(dependencies)})
        evidence('generated_code',{'data':deepcopy(result.get('candidate_spec'))})
        evidence('code_product',{'data':deepcopy(generated)})
        evidence('object_bindings',{'data':deepcopy(generated.get('object_bindings') or [])})
        evidence('mathematical_report',{'data':_code_math_report(result,accepted=False,input_ok=code_input_ok,verdicts=[])})
    elif product:
        evidence('object_bindings',{'data':deepcopy(product.get('object_bindings') or [])})
        evidence('mathematical_report',{'data':deepcopy(product.get('technical_report') or {})})
        if (product.get('data_hash')!=digest_of(product.get('data')) or (request['kind']!='source_image' and product.get('technical_report',{}).get('mathematical_status')!='pass')
                or any(r.get('status')!='pass' for r in product.get('relations') or [])):
            issue('MEDIA_MATHEMATICS','数学数据/关系未通过，不得由视觉审核覆盖')
    if request['kind']=='source_image' and (not result.get('source_provenance') or product.get('technical_report',{}).get('source_status')!='pass'):
        issue('MEDIA_SOURCE_PROVENANCE','缺可核验原始来源与变换记录')
    if not code_product and not product and request['kind'] not in ('source_image','animation'):
        issue('MEDIA_MATHEMATICS','缺绑定批准数学对象的StaticMathProductV2')
    targets=[]
    if request['kind']=='animation':
        from ...checks.offline import check_animation_resources,check_webvtt
        animation=result.get('animation') or {}
        if animation.get('resources')!=resources: issue('MEDIA_RESOURCE_INVALID','动画和清单资源不一致')
        for checker in (lambda:check_animation_resources(animation,base,profile=result.get('render_profile','final')),
                        lambda:check_webvtt(next((p for r,p in files if r['kind']=='captions'),base/'missing.vtt'))):
            try:
                c=checker().as_dict();checks.append(c)
                if not c['ok']:
                    issue('MEDIA_DECODE' if c['name']=='animation.resources' and c.get('evidence',{}).get('video',{}).get('evidence',{}).get('decode_ok') is False else 'MEDIA_VIDEO' if c['name']=='animation.resources' else 'MEDIA_CAPTIONS',c['detail'])
            except CapabilityError as exc: issue('MEDIA_CAPABILITY',str(exc),'environment')
        steps=animation.get('steps') or []
        video_check=next((c for c in checks if c['name']=='animation.resources'),{})
        seconds=video_check.get('evidence',{}).get('video',{}).get('evidence',{}).get('duration')
        if steps and seconds is not None and (steps[0].get('start')!=0 or abs(float(steps[-1].get('end',-1))-seconds)>.2):
            issue('MEDIA_CAPTIONS','步骤/字幕总时长与实际视频流不对应')
        transcript=next((p for r,p in files if r['kind']=='transcript'),None)
        if transcript:
            try:
                text=transcript.read_text(encoding='utf-8')
                evidence('captions',{'caption_form':'transcript','text':text,'path':str(transcript),'sha256':digest_file(transcript)})
                if any(s.get('caption','') not in text for s in steps): issue('MEDIA_CAPTIONS','可见文字稿遗漏当前步骤字幕')
            except UnicodeDecodeError: issue('MEDIA_CAPTIONS','可见文字稿不是UTF-8')
        evidence('steps',{'data':deepcopy(steps)})
        if not code_product:
            evidence('object_bindings',{'data':deepcopy(animation.get('source_binding') or {})})
            evidence('mathematical_report',{'data':deepcopy(animation.get('math_report') or {})})
            if animation.get('math_report',{}).get('status')!='pass': issue('MEDIA_MATHEMATICS','动画数学报告未通过')
        caption=next((p for r,p in files if r['kind']=='captions'),None)
        if caption:
            evidence('captions',{'text':caption.read_text(encoding='utf-8',errors='replace'),'path':str(caption),'sha256':digest_file(caption)})
            _check_step_captions(steps,caption,issue)
        frames=[(r,p) for r,p in files if r['kind']=='keyframe']
        if not steps or not frames: issue('MEDIA_FRAME_COVERAGE','缺步骤和真实检查帧')
        if {r.get('step_id') for r,p in frames}!={s.get('step_id') for s in steps}: issue('MEDIA_FRAME_COVERAGE','实际帧未逐步覆盖')
        for s in steps:
            own=[r for r,p in frames if r.get('step_id')==s.get('step_id')]
            if any(type(r.get('timestamp')) not in (float,int) or not s.get('start',0)<=r['timestamp']<=s.get('end',0) for r in own):
                issue('MEDIA_FRAME_COVERAGE','步骤实际帧时点不在步骤内')
            phases={r.get('phase') for r in own}
            if code_product and (not {'before','after'}<=phases or not {'observe','during'}&phases):
                issue('MEDIA_FRAME_COVERAGE','代码动画步骤缺实际前/中/后帧 '+str(s.get('step_id')))
            if s.get('kind')=='change' and not {'before','after'}<=phases:
                issue('MEDIA_FRAME_COVERAGE','变化步骤缺前后真实帧')
            if s.get('kind')=='observe' and 'observe' not in phases: issue('MEDIA_FRAME_COVERAGE','观察步骤缺实际观察帧')
            from .presentation_policy import RENDER_PROFILES
            fps=RENDER_PROFILES[result.get('render_profile','final')]['frame_rate']
            for moment in s.get('evidence_moments') or []:
                candidates=[r for r in own if all(r.get(key)==moment.get(key) for key in ('phase','object_id','action','target_object_id'))]
                offset=-1/fps if moment.get('phase')=='before' else 1/fps if moment.get('phase')=='after' else 0
                at=max(s['start'],min(s['end']-1/fps,float(moment['at'])+offset))
                if not any(abs(r['timestamp']-at)<=.002 for r in candidates):
                    issue('MEDIA_FRAME_COVERAGE','动作/观察缺绑定真实帧 '+str(s.get('step_id'))+':'+str(moment))
        video=next((p for r,p in files if r['kind']=='mp4'),None)
        for r,p in frames:
            if video and type(r.get('timestamp')) in (int,float):
                try:
                    record=verify_frame_pixels(video,p,r['timestamp'],base/'.validation'/'decoded')
                    record['evidence']['frame_relative_path']=r['relative_path']
                    checks.append(record)
                    if not record['ok']: issue('MEDIA_FRAME_SOURCE','关键帧与视频该时点真实解码像素不一致 '+p.name)
                except CapabilityError as exc: issue('MEDIA_CAPABILITY',str(exc),'environment')
            targets.append((r,p))
    else:
        primary=next(((r,p) for r,p in files if r['kind'] in ('svg','image')),None)
        if primary: targets.append(primary)
        else: issue('MEDIA_RESOURCE_INVALID','缺可显示主图片')
    # Avoid using corrupt inputs to obtain reviewer approval.
    if not any(i['code']=='MEDIA_RESOURCE_INVALID' for i in issues):
        for index,(resource,path) in enumerate(targets):
            try:
                rows=capture_display(path,profiles=profiles,out_dir=base/'.validation'/('display-'+str(index)),workspace_root=workspace_root)
                for row in rows:
                    p=next(p for p in profiles if p['profile_id']==row['profile_id'])
                    image_path=Path(row['image_path']);images.append(str(image_path))
                    eid=evidence('rendered_frame',{**row,'path':str(image_path),'sha256':row['image_hash'],
                        'source_hash':resource['sha256'],'source_path':resource['relative_path'],'source_time_seconds':resource.get('timestamp'),'step_id':resource.get('step_id'),'phase':resource.get('phase'),'action':resource.get('action'),'object_id':resource.get('object_id'),'target_object_id':resource.get('target_object_id')})
                    if row['viewport_width']!=p['width'] or row['overflow'] or abs(row['media_width']-p['content_width'])>1:
                        warnings.append({'code':'MEDIA_LAYOUT','message':'实际占宽与建议不同或出现可滚动溢出 '+eid})
                    if any(t['font_px']+0.01<p['min_font_px'] for t in row['labels']):
                        warnings.append({'code':'MEDIA_LABEL_READABILITY','message':'标签小于建议字号 '+eid})
                    if any(t['clipped'] for t in row['labels']):
                        issue('MEDIA_LABEL_CLIPPED','关键标签被裁切 '+eid)
                    if any(t.get('unavailable') for t in row['labels']):
                        issue('MEDIA_FUNCTIONAL','关键标签不可见 '+eid)
            except CapabilityError as exc: issue('MEDIA_CAPABILITY',str(exc),'environment')
            except (ContractError,ValueError,OSError) as exc: issue('MEDIA_LAYOUT',str(exc))
    actual_frames={(e.get('source_path'),e.get('source_hash'),e.get('profile_id'))
                   for e in catalog.values() if e['kind']=='rendered_frame'}
    required_frames={(r['relative_path'],r['sha256'],p['profile_id']) for r,path in targets for p in profiles}
    if not required_frames or actual_frames!=required_frames:
        issue('MEDIA_FRAME_COVERAGE','缺全部目标尺寸下的实际图片/逐步真实帧')
    identity={'request_hash':digest_of(request),'criteria_digest':request['criteria_digest'],
        'result_hash':result_digest(result),'policy_digest':digest_of(policy),
        'display_profiles_hash':digest_of(profiles),'review_items':items}
    groups=media_evidence_groups(catalog,review_context=identity) if not issues else {}
    verdicts=[];response={};review_failure={}
    if not issues:
        try:
            if client is None:
                raise CapabilityError('独立实际画面审核缺模型传输能力')
            from ...providers.capabilities import require_image_input
            require_image_input(getattr(client,'chat_json',None))
            prompt=build_media_review_prompt(request['kind'])
            payload={'request':request,'review_items':items,'evidence_catalog':catalog,
                'evidence_groups':groups}
            # D16：证据返修轮**必须**告诉审核员上次为什么被打回。以前这一轮给的材料
            # 与第一次逐字节相同，审核员只能照抄上一次的答案。
            # 这同时解决第二件事：gateway 的缓存指纹含 messages，材料一字不变时会直接
            # 命中缓存取回同一条坏判词（gateway.py:228-242），那一次预算等于白花；
            # 加上这段失败原因后消息变了，指纹随之改变，缓存不会再挡住真正的重问。
            prior=result.get('validation') if isinstance(result,dict) else None
            if isinstance(prior,dict):
                failures=[{'code':row.get('code'),'message':row.get('message')}
                          for row in (prior.get('issues') or []) if isinstance(row,dict)][:8]
                if isinstance(prior.get('review_failure'),dict):
                    failures.append({'code':'review_failure',**prior['review_failure']})
                if failures: payload['previous_review_failure']=failures
            response=client.chat_json([{'role':'system','content':prompt.text},{'role':'user','content':json.dumps(
                payload,ensure_ascii=False)}],
                tag='media-validation:'+request['request_id'],images=images)['data']
            reviewed=validate_verdicts(items,response,catalog,evidence_groups=groups,review_context=identity)
            verdicts=reviewed['verdicts'];issues.extend(reviewed['issues'])
        except Exception as exc:
            from ...pipeline.services import reraise_control_error
            from ...providers.review_failures import classify_review_failure
            reraise_control_error(exc)
            review_failure={'kind':classify_review_failure(exc),'reason':type(exc).__name__+': '+str(exc)}
            issue('MEDIA_REVIEW_FORMAT' if review_failure['kind']=='protocol' else 'MEDIA_CALL_FAILED',
                review_failure['reason'],'reviewer')
    report={'schema_version':'MediaValidationV2','request_id':request['request_id'],**identity,
        'display_profiles':profiles,'evidence_groups':groups,
        'raw_verdicts':deepcopy(response.get('verdicts')) if isinstance(response,dict) else None,
        'evidence_catalog':catalog,'technical_checks':checks,'verdicts':verdicts,'issues':issues,'warnings':warnings,
        'review_failure':review_failure,
        'review_problems':response.get('problems',[]) if isinstance(response,dict) else [],
        'ok':not issues,'decision':'accepted' if not issues else 'blocked' if any(i['code']=='MEDIA_CAPABILITY' for i in issues) else 'needs_review'}
    if code_product:
        report['math_report']=_code_math_report(result,accepted=not issues,input_ok=code_input_ok,verdicts=verdicts)
    return report


def verify_frame_pixels(video,frame,at,out_dir):
    """Re-extract registered checkpoints; a separate PNG is not proof of video pixels."""
    from PIL import Image,ImageChops
    out_dir=Path(out_dir);out_dir.mkdir(parents=True,exist_ok=True)
    target=out_dir/(Path(frame).stem+'.png')
    try:
        proc=subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-threads','1','-filter_threads','1',
            '-ss',f'{at:.3f}','-i',str(video),'-frames:v','1','-update','1',str(target)],capture_output=True,timeout=60)
    except (OSError,subprocess.TimeoutExpired) as exc: raise CapabilityError('实际帧解码能力失败：'+str(exc)) from exc
    same=False
    if proc.returncode==0 and target.is_file():
        with Image.open(frame) as a,Image.open(target) as b:
            same=a.size==b.size and ImageChops.difference(a.convert('RGB'),b.convert('RGB')).getbbox() is None
    return {'name':'animation.frame_source','status':'accepted' if same else 'failed','ok':same,
        'evidence':{'at_seconds':at,'video_sha256':digest_file(video),'frame_sha256':digest_file(frame),
                    'decoded_sha256':digest_file(target) if target.is_file() else None}}


def _check_step_captions(steps,path,issue):
    from ...checks.offline import parse_webvtt_cues
    try: cues=parse_webvtt_cues(path.read_text(encoding='utf-8'))
    except (ValueError,UnicodeDecodeError) as exc: issue('MEDIA_CAPTIONS',str(exc));return
    if len(cues)!=len(steps): issue('MEDIA_CAPTIONS','字幕和步骤数量不一致');return
    for step,cue in zip(steps,cues):
        if (cue['id']!=step.get('step_id') or abs(cue['start']-step.get('start',-1))>.002
                or abs(cue['end']-step.get('end',-1))>.002 or cue['text']!=step.get('caption')):
            issue('MEDIA_CAPTIONS','字幕ID/时段/文本未精确对应步骤 '+str(step.get('step_id')))


def validate_media_report(request,result,report,*,policy=None,display_profiles=None):
    if reviews_frozen(): return
    policy=policy or media_review_policy(request['kind']);profiles=_profiles(display_profiles,policy)
    expected={'schema_version':'MediaValidationV2','request_id':request['request_id'],'request_hash':digest_of(request),
        'criteria_digest':request['criteria_digest'],'result_hash':result_digest(result),'policy_digest':digest_of(policy),
        'display_profiles_hash':digest_of(profiles),'review_items':media_review_items(request,result,policy),'ok':True,'decision':'accepted'}
    if not isinstance(report,dict) or any(report.get(k)!=v for k,v in expected.items()) or report.get('issues'):
        raise ContractError('media approval requires current complete MediaValidationV2')
    catalog=report.get('evidence_catalog') or {}
    if _is_code_product(result):
        product=_validate_code_binding(request,result)
        for kind,data in [('request',request),('approved_dependencies',result.get('approved_dependencies') or []),
                          ('generated_code',result.get('candidate_spec')),('code_product',product),
                          ('object_bindings',product['object_bindings']),
                          ('mathematical_report',_code_math_report(result,accepted=False,input_ok=True,verdicts=[]))]:
            entries=[e for e in catalog.values() if e.get('kind')==kind]
            if len(entries)!=1 or entries[0].get('data')!=data:
                raise ContractError('code media report missing exact approved evidence: '+kind)
            if kind=='request' and entries[0].get('approved_dependencies')!=result.get('approved_dependencies'):
                raise ContractError('code media request evidence changed approved dependencies')
        expected_math=_code_math_report(result,accepted=True,input_ok=True,verdicts=report.get('verdicts') or [])
        if report.get('math_report')!=expected_math:
            raise ContractError('code media math report requires approved_inputs_and_visual_review')
    frames=[e for e in catalog.values() if e.get('kind')=='rendered_frame']
    if not frames or not {p['profile_id'] for p in profiles}<={e.get('profile_id') for e in frames}:
        raise ContractError('media report missing actual display profiles')
    resources=result.get('resources') or []
    sources=[r for r in resources if r.get('kind')=='keyframe'] if request['kind']=='animation' else [r for r in resources if r.get('kind') in ('svg','image')][:1]
    expected_frames={(r['relative_path'],r['sha256'],p['profile_id']) for r in sources for p in profiles}
    actual_frames=[(e.get('source_path'),e.get('source_hash'),e.get('profile_id')) for e in frames]
    if len(actual_frames)!=len(set(actual_frames)) or set(actual_frames)!=expected_frames:
        raise ContractError('media report omits viewport/actual-frame evidence')
    for frame in frames:
        p=next(p for p in profiles if p['profile_id']==frame['profile_id'])
        if frame.get('media_width',0)<=0 or any(label.get('clipped') is not False or label.get('unavailable') for label in frame.get('labels',[])):
            raise ContractError('media critical labels are clipped or media is empty')
    if request['kind']=='animation':
        checks=report.get('technical_checks') or []
        video=next((c for c in checks if c.get('name')=='animation.resources'),{})
        captions=next((c for c in checks if c.get('name')=='animation.captions'),{})
        if video.get('ok') is not True or video.get('evidence',{}).get('video',{}).get('evidence',{}).get('decode_ok') is not True or captions.get('ok') is not True:
            raise ContractError('media report missing actual video decode/WebVTT evidence')
        actual_source_checks={(c.get('evidence',{}).get('frame_relative_path'),c.get('evidence',{}).get('frame_sha256')) for c in checks if c.get('name')=='animation.frame_source' and c.get('ok') is True}
        if actual_source_checks!={(r['relative_path'],r['sha256']) for r in sources}: raise ContractError('media report missing actual video frame extraction evidence')
        animation=result.get('animation') or {}
        if not any(e.get('kind')=='steps' and e.get('data')==animation.get('steps') for e in catalog.values()):
            raise ContractError('media report missing exact animation step evidence')
        for caption_resource in (r for r in resources if r.get('kind') in ('captions','transcript')):
            if not any(e.get('kind')=='captions' and e.get('sha256')==caption_resource.get('sha256') for e in catalog.values()):
                raise ContractError('media report missing current subtitles/transcript')
    for entry in catalog.values():
        if entry.get('path') and (not Path(entry['path']).is_file() or digest_file(entry['path'])!=entry.get('sha256')):
            raise ContractError('media evidence missing or changed')
    reviewed=_replay_report_verdicts(report,expected['review_items'],
        require_groups=policy.get('version')==media_review_policy()['version'])
    if not reviewed['ok']: raise ContractError('media criterion evidence incomplete')
    return True
