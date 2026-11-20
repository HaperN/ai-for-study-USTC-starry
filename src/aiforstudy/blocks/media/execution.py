"""V2 static resources: approved math and exact registered provenance → real pixels."""
from __future__ import annotations
import json
from copy import deepcopy
from pathlib import Path
import subprocess
import tempfile
from ...core.digest import digest_file,digest_of
from ...core.jsonio import write_json_atomic,read_json
from ...errors import CapabilityError,ContractError
from .math_context import ApprovedMathContext
from .svg_safety import safe_svg as _safe_svg


def rasterize_svg(svg_path,out_path,*,workspace_root=None):
    from ...checks.offline import _browser_executable
    browser=_browser_executable(Path(workspace_root) if workspace_root else None)
    if browser is None: raise CapabilityError('静态图缺真实 Chromium 截图能力')
    # Our own bounded SVG only: no scripts, external files, or URLs.
    with tempfile.TemporaryDirectory(prefix='aifs-svg-browser-') as profile:
        result=subprocess.run([str(browser),'--headless','--no-sandbox','--disable-gpu','--disable-background-networking','--disable-component-update','--no-first-run','--hide-scrollbars','--force-device-scale-factor=1','--window-size=800,500','--user-data-dir='+profile,'--screenshot='+str(Path(out_path).resolve()),Path(svg_path).resolve().as_uri()],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    if result.returncode or not Path(out_path).is_file(): raise CapabilityError('Chromium SVG 截图失败：'+result.stderr.decode('utf-8','replace')[-500:])
    from PIL import Image
    with Image.open(out_path) as image:
        if image.size!=(800,500) or len(image.getcolors(800*500) or [])<3: raise ContractError('静态实际截图空白或尺寸错误')
    return Path(out_path)



def compile_static(kind,spec,math_context):
    from .plot import compile_plot
    from .geometry import compile_geometry
    from .diagram import compile_diagram
    parser={'plot':compile_plot,'geometry':compile_geometry,'diagram':compile_diagram}.get(kind)
    if parser is None: raise ContractError('unsupported static kind; animation has an independent executor')
    return parser(spec,math_context)


def describe_static_specs():
    ref={'model_id':'approved-model-id','object_id':'approved-object-id'}
    return {'rule':'JSON only. Extra keys rejected. No code, coordinates, expressions, free labels or visibility. Mathematical values and labels come only from approved object_ref. Unsupported: {unsupported:reason}.',
        'layout':{'font_size':'14..36 (default 24)','label_offsets':'{existing_display_id:[dx,dy]}, abs<=50'},
        'plot':{'schema_version':'MediaPlotV2','series':[{'id':'curve','object_ref':ref,'variable':'declared-real-variable','domain':[-2,2]}],'layout':{}},
        'geometry':{'schema_version':'MediaGeometryV2','construction':{'kind':'vector_sum','a':ref,'b':ref,'result':ref},'layout':{}},
        'plane_construction':{'kind':'plane','normal':ref,'offset':ref,'classification':'affine|subspace'},
        'diagram':{'schema_version':'MediaDiagramV2','construction':{'kind':'row_operation','before':ref,'after':ref,'operation':{'kind':'add_multiple','source':0,'target':1,'factor':ref}},'layout':{}},
        'other_diagram_constructions':[{'kind':'row_operation','before':ref,'after':ref,'operation':{'kind':'swap','source':0,'target':1}},{'kind':'derivation','model_id':'approved-model-id','claim_ids':['approved-math-claim-id']}]}





def _asset(request,source_assets,collection,identity):
    matches=[a for a in (source_assets or {}).get(collection,[]) if a.get('asset_id')==identity]
    if len(matches)!=1: raise ContractError('source asset ID must exist once in registered source_assets')
    asset=matches[0]
    if not any(s.get('kind')=='asset' and s.get('asset_id')==identity and s.get('hash')==asset.get('hash') for s in request.get('source_refs',[])): raise ContractError('source asset hash does not match request provenance')
    return asset


def _source_product(request,path,source_image,source_assets):
    if source_image is None: raise CapabilityError('source_image needs a registered textbook source backend')
    asset=_asset(request,source_assets,'source_images',request.get('source_image_id')); evidence=source_image(request,path)
    if not isinstance(evidence,dict) or evidence.get('asset_id')!=asset['asset_id'] or evidence.get('asset_hash')!=asset['hash'] or evidence.get('source_file_hash')!=asset.get('file_hash') or not asset.get('file_hash') or evidence.get('output_hash')!=digest_file(path) or evidence.get('origin')!='textbook' or evidence.get('transform') not in ('copy','crop','normalize'): raise ContractError('source_image needs verified original file, transform and output provenance')
    from PIL import Image
    with Image.open(path) as im: im.verify()
    return {'schema_version':'StaticMathProductV2','kind':'source_image','data':{'source':evidence},'data_hash':digest_of({'source':evidence}),'object_bindings':[],'relations':[],'layout':{},'technical_report':{'ok':True,'source_status':'pass','mathematical_status':'not_applicable'}}


def _content_problem(content,reason):
    text=content.get('body_markdown') or content.get('summary') or ''
    return {'target_artifact_kind':'content','pointer':'/body_markdown' if content.get('body_markdown') else '/summary','issue_key':'static-approved-math-relation','problem':reason,'evidence':text,'action':'核对已批准数学对象、关系及正文；内容数学检查通过并重新批准后重建本图。','expected':'批准对象精确满足当前数学关系，原数学条件、教学主张和图示一致。'}


def resource_regeneration_tickets(tickets,request,previous):
    """Authorize restoring bytes from current frozen authority, never spec changes."""
    from ..audit.contracts import validate_ticket
    selected=[t for t in tickets if t.get('target',{}).get('rule')=='MEDIA_RESOURCE_INVALID:RESOURCE_INVALID']
    if not selected: return []
    if (not previous or previous.get('request_hash')!=digest_of(request)
            or previous.get('criteria_digest')!=request.get('criteria_digest')):
        raise ContractError('resource regeneration requires current frozen request and standards')
    expected=previous.get('spec_hash') or digest_of(previous.get('visual_spec') or previous.get('invalid_spec') or previous.get('request',{}))
    for data in selected:
        ticket=validate_ticket(data)
        if (ticket.status!='ready' or ticket.owner!='visual_renderer' or ticket.kind!='visual_math'
                or ticket.allowed_paths!=('/resources',) or ticket.target['artifact_id']!='media:'+request['request_id']
                or ticket.target['artifact_hash']!=expected):
            raise ContractError('stale or unauthorized resource regeneration')
    return selected


def execute_static(request,*,content,approval=None,out_dir,client=None,reviewer=None,workspace_root=None,source_image=None,library_source=None,source_assets=None,repair_context=None,previous=None,evidence_only=False):
    from ...pipeline.services import reraise_control_error
    row={'request_id':request.get('request_id'),'kind':request.get('kind'),'state':'blocked','reason':'','resources':[],'request_hash':digest_of(request),'criteria_digest':request.get('criteria_digest')}
    try:
        if request.get('kind') not in ('source_image','plot','geometry','diagram'): raise ContractError('unsupported static kind; arbitrary SVG/code is retired')
        if approval is None: raise ContractError('current ContentApprovalV2 approval is required')
        context=ApprovedMathContext.from_content_binding(content,approval,request); row['math_context_digest']=context.digest
        if evidence_only:
            if (not previous or previous.get('request_hash')!=digest_of(request)
                    or previous.get('criteria_digest')!=request.get('criteria_digest')
                    or previous.get('math_context_digest')!=context.digest
                    or not previous.get('resources')):
                raise ContractError('evidence-only reuse requires current complete frozen resources')
            base=Path(previous['source_dir']).resolve()
            for resource in previous['resources']:
                path=base/resource['relative_path']
                if path.is_symlink() or not path.resolve().is_relative_to(base) or not path.is_file() or digest_file(path)!=resource['sha256']:
                    raise ContractError('evidence-only resource bytes are missing or corrupt')
            reused=deepcopy(previous)
            for key in ('validation','review','media_review'):
                reused.pop(key,None)
            reused.update(state='rendered',evidence_only=True,reason='复用已核验资源字节，等待补当前画面审核证据')
            return reused
        out_dir=Path(out_dir); out_dir.mkdir(parents=True,exist_ok=True); stem='request-'+digest_of(request)[7:27]
        media_path=out_dir/(stem+('.png' if request['kind']=='source_image' else '.svg')); spec=request.get('spec'); tickets=repair_context or []
        if isinstance(tickets,dict): tickets=tickets.get('tickets',[])
        regeneration=resource_regeneration_tickets(tickets,request,previous)
        tickets=[t for t in tickets if t not in regeneration]
        if request['kind']=='source_image':
            if tickets and not evidence_only: raise ContractError('source image repair requires revalidated provenance')
            product=_source_product(request,media_path,source_image,source_assets); image_path=media_path
            row['source_provenance']=product['data']['source']
        else:
            if regeneration:
                spec=deepcopy(previous.get('candidate_spec'))
                if spec is None or digest_of(spec)!=previous.get('spec_hash'):
                    raise ContractError('resource regeneration lacks intact frozen static spec')
            elif previous and (evidence_only or tickets):
                if previous.get('request_hash')!=digest_of(request) or previous.get('criteria_digest')!=request.get('criteria_digest') or previous.get('math_context_digest')!=context.digest: raise ContractError('previous media authority is stale')
                resource=next((r for r in previous.get('resources',[]) if r.get('kind')=='spec'),None)
                if resource is None:
                    spec=previous.get('candidate_spec')
                    if spec is None: raise ContractError('previous media lacks a frozen spec')
                else:
                    base=Path(previous['source_dir']).resolve(); saved=base/resource['relative_path']
                    if saved.is_symlink() or not saved.resolve().is_relative_to(base) or not saved.is_file() or digest_file(saved)!=resource['sha256']: raise ContractError('previous media spec is corrupt')
                    spec=read_json(saved)
                if digest_of(spec)!=previous.get('spec_hash'): raise ContractError('previous spec hash is stale')
            if tickets and not evidence_only:
                if client is None: raise CapabilityError('media repair requires a transport')
                from ..audit.contracts import validate_ticket
                for ticket in tickets:
                    authority=validate_ticket(ticket)
                    if authority.status!='ready' or authority.target['artifact_id']!='media:'+request['request_id'] or authority.target['artifact_hash']!=digest_of(spec):
                        raise ContractError('stale or unauthorized media spec repair')
                if all(t['kind']=='visual_layout' for t in tickets):
                    from .media_patches import apply_media_patches
                    patches=client.chat_json([{'role':'system','content':'Return {patches:[MediaPatchV2]}; one patch per original ticket. Each has schema_version,request_id,base_spec_hash,ticket_id,operations:[{op:replace,path:/layout/...,value:...}]. All refer to the same base hash. Only authorized existing layout fields; paths across patches must be disjoint.'},{'role':'user','content':json.dumps({'request':request,'spec':spec,'base_spec_hash':digest_of(spec),'tickets':tickets},ensure_ascii=False)}],tag='media-layout-patch:'+request['request_id'])['data']
                    if not isinstance(patches,dict) or set(patches)!= {'patches'}: raise ContractError('invalid layout patch batch')
                    spec=apply_media_patches(request,spec,patches['patches'],tickets,math_context=context)
                else:
                    if any(t['kind'] not in ('visual_layout','visual_math') or '/spec' not in t['allowed_paths'] for t in tickets if t['kind']!='visual_layout'):
                        raise ContractError('spec repair requires explicit original spec scope')
                    spec=client.chat_json([{'role':'system','content':json.dumps(describe_static_specs(),ensure_ascii=False)+' Repair this typed spec under the original request, approved object identities and frozen criteria. Never return executable code.'},{'role':'user','content':json.dumps({'request':request,'previous_spec':spec,'tickets':tickets,'approved_models':[context.model_by_id(r['model_id']).as_dict() for r in request['math_model_refs']]},ensure_ascii=False)}],tag='media-spec-repair:'+request['request_id'])['data']
            if spec is None:
                if client is None: raise CapabilityError('static request requires typed mathematical spec and planning transport')
                spec=client.chat_json([{'role':'system','content':json.dumps(describe_static_specs(),ensure_ascii=False)},{'role':'user','content':json.dumps({'request':request,'approved_models':[context.model_by_id(r['model_id']).as_dict() for r in request['math_model_refs']]},ensure_ascii=False)}],tag='media-math-spec:'+request['request_id'])['data']
            if isinstance(spec,dict) and spec.get('unsupported'): raise CapabilityError(str(spec['unsupported']))
            row.update(candidate_spec=spec,spec_hash=digest_of(spec))
            product=compile_static(request['kind'],spec,context)
            # Freeze the effective defaults so a layout ticket can address real,
            # existing fields and cannot silently change the mathematical spec.
            spec={**spec,'layout':product['layout']}
            product['spec_hash']=digest_of(spec)
            row.update(candidate_spec=spec,spec_hash=digest_of(spec))
            if request.get('library_id'):
                asset=_asset(request,source_assets,'library','library:'+request['library_id'])
                if library_source is None: raise CapabilityError('registered library backend missing')
                evidence=library_source(request,media_path)
                if not isinstance(evidence,dict) or evidence.get('ok') is not True or evidence.get('asset_id')!=asset['asset_id'] or evidence.get('asset_hash')!=asset['hash'] or evidence.get('object_bindings')!=product['object_bindings']: raise ContractError('library needs exact approved object bindings and registered source identity')
                product['library_source']=evidence
            else: media_path.write_text(product['svg'],encoding='utf-8')
            _safe_svg(media_path); image_path=out_dir/(stem+'.screenshot.png'); rasterize_svg(media_path,image_path,workspace_root=workspace_root)
        resources=[{'relative_path':media_path.name,'kind':'image' if request['kind']=='source_image' else 'svg','sha256':digest_file(media_path)}]
        if image_path!=media_path: resources.append({'relative_path':image_path.name,'kind':'screenshot','sha256':digest_file(image_path)})
        if spec is not None:
            p=out_dir/(stem+'.spec.json');write_json_atomic(p,spec);resources.append({'relative_path':p.name,'kind':'spec','sha256':digest_file(p)})
        product={k:v for k,v in product.items() if k!='svg'}; p=out_dir/(stem+'.math.json');write_json_atomic(p,product);resources.append({'relative_path':p.name,'kind':'math_evidence','sha256':digest_file(p)})
        row.update(source_dir=str(out_dir),resources=resources,figure_id='FIG-'+stem,name=stem,svg=media_path.name if request['kind']!='source_image' else None,image=media_path.name if request['kind']=='source_image' else None,screenshot=image_path.name,spec_hash=digest_of(spec) if spec is not None else digest_file(media_path),static_product=product,technical_report=product['technical_report'])
        row['display_evidence']=[{'profile':'reader','width':800,'height':500,'image_path':image_path.name,'image_hash':digest_file(image_path)}]
        row.update(state='rendered',reason='资源已制作，待统一实际读者尺寸与逐项审核')
        row['entry']={'id':'FIG-'+stem,'name':stem,'title':request.get('caption') or '','svg':'figures/'+media_path.name if request['kind']!='source_image' else None,'file':'figures/'+media_path.name if request['kind']=='source_image' else None,'unit_key':content.get('unit_id'),'request_id':request['request_id'],'concepts':[],'checks':product['technical_report']}
    except Exception as exc:
        reraise_control_error(exc); reason=f'{type(exc).__name__}: {exc}'; content_error=isinstance(exc,ContractError) and str(exc).startswith('content:')
        row.update(state='blocked' if isinstance(exc,CapabilityError) else 'failed',reason=reason,failure_owner='content' if content_error else 'media',repair_instructions='数学真值错误回内容阶段重新批准；布局仅按原工单修复当前 request。')
        if content_error: row['review']={'accepted':False,'problems':[_content_problem(content,reason)]}
    return row
