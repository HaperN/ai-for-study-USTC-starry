"""Independent post-approval media planner; no execution side effects."""
from copy import deepcopy
import json
from ...core.digest import digest_of
from ...core.review_mode import reviews_frozen, frozen_review
from ...errors import CapabilityError, ContractError
from ..content.content_contracts import as_dict
from .media_requirements import candidates, content_hash, require_content_binding, validate_requirements, visible_fields, semantic_dependencies, POLICY_VERSION

REQUEST_SCHEMA={
    'request_id':'host-assigned-from-stable-request_key','request_key':'stable semantic identity',
    'kind':'source_image/plot/geometry/diagram/animation','serves':['goal_id'],'goal_refs':['goal_id'],
    'claim_ids':['approved teaching claim ID'],
    'math_model_refs':[{'model_id':'approved model ID','digest':'approved model digest'}],
    'object_refs':[{'model_id':'approved model ID','object_id':'math_object_id'}],
    'learning_goal':'reader task','observe':'which relation and where to look','misconception':'misreading to avoid',
    'source_image_id':'only for source_image: registered asset_id',
    'source_refs':[{'kind':'approved_content','pointer':'/body_markdown','hash':'provided field hash'}],
    'constraints':{},'spec':'optional typed executor spec; never executable code',
    'placement':{'target_pointer':'/body_markdown','owner_id':'unit_id/object_id','step_id':'required when landing in a step','anchor_text':'exact substring','span':[0,3],'field_hash':'provided field hash','position':'after'},
    'dependency_paths':[{'pointer':'/body_markdown','hash':'provided field hash'}],
    'acceptance_criteria':[{'criterion_id':'stable ID','version':'1','kind':'mathematical/pedagogical/visual/source','statement':'observable pass standard','evidence_required':['rendered_frame','request']}],
    'caption':'media-only text','alt_text':'text'}
DECISION_SCHEMA={'goal_id':'approved goal ID','target_id':'same goal ID','requirement':'required/recommended/none',
                 'reason':'why this decision serves this task','benefit':'what relation the reader understands',
                 'cost':'cognitive/production cost','text_alternative':'what prose already establishes or cannot show',
                 'request_ids':['request_key'],'handled_by':'media/interaction','not_produced_reason':'required for unproduced recommended media'}


class MediaPlanningError(ContractError):
    def __init__(self,message,candidate):
        super().__init__(message); self.candidate=candidate


def planning_prompt(content,approval,capabilities,author_requirements=None):
    from .author_requirements import validate_author_artifact, project_author_requests
    record=validate_author_artifact(author_requirements,content)
    _,requests=project_author_requests(record['requirements'],content)
    return [{'role':'system','content':
      '你是作者媒体需求的技术编译器。只返回 {requests:[{request_key,kind,source_image_id?,source_refs?,spec?}]} JSON。'
      '作者已同次决定所有教学需求；不得返回 decisions，不得新增、遗漏或改写请求、图意、位置、数学对象和验收标准。'
      '每个作者 request_key 恰有一个技术项。作者 image 选择 source_image/plot/geometry/diagram；animation 必须保持 animation。'
      '能力不足须返回原请求与所需后端，由宿主明确阻断，不得删请求、补 none 或假称完成。'
      'spec 可省略，由现有绘制/动画模块补齐；若给出只能是类型化声明规格，不能可执行代码。'
      'source_refs 仅补充登记 source/asset 的真实来源，禁止改写宿主提供的 approved_content hash；source_image 必须登记 source_image_id 和 asset 来源。'},
      {'role':'user','content':json.dumps({'role_task':'media_compile','content':content,'approval':approval,
        'author_requirements':record,'requests':requests,'capabilities':capabilities,
        'visible_fields':visible_fields(content)},ensure_ascii=False)}]


def _freeze_repair_criteria(raw,repair_context):
    """Same semantic request retains exact standards through repair rounds."""
    if reviews_frozen(): return
    if not isinstance(repair_context,dict): return
    previous=repair_context.get('previous_requirements')
    if not previous: return
    old={r.get('request_key') or r['request_id']:r for r in previous.get('requests',[])}
    if set(old)!={r['request_key'] for r in raw.get('requests',[])}:
        raise ContractError('返修必须保持请求身份；结构性重规划需要宿主另行发起并保留原轮次与原因')
    for request in raw.get('requests',[]):
        before=old.get(request['request_key'])
        if before and request.get('acceptance_criteria')!=before.get('acceptance_criteria'):
            raise ContractError('返修不得替换或削弱冻结 acceptance_criteria；新目标须重新批准内容')


def plan_media_requirements(content,approval,*,author_requirements=None,capabilities,client=None,
                            repair_context=None,book_id=None,revision=1):
    from .author_requirements import validate_author_artifact, project_author_requests
    content=as_dict(content); approval=as_dict(approval)
    require_content_binding(content,approval)
    if not reviews_frozen() and book_id is not None and book_id!=content['book_id']: raise ContractError('媒体 book_id 与批准内容不一致')
    record=validate_author_artifact(author_requirements,content)
    requirements,requests=project_author_requests(record['requirements'],content)
    source_images=(capabilities.get('source_assets') or {}).get('source_images') or []
    has_source_image=('source_image' in capabilities.get('kinds',[]) and any(
        isinstance(asset,dict) and asset.get('asset_id') and asset.get('hash') for asset in source_images))
    unavailable=[request['request_key'] for request in requests if request['kind']=='image'
        and not (request['math_model_refs'] and request['object_refs']) and not has_source_image]
    if unavailable and not capabilities.get('code_generation') and not reviews_frozen():
        raise CapabilityError('作者图片请求 '+', '.join(unavailable)+' 缺少批准数学模型/对象，且没有当前后端可引用的登记教材源图；'
            '请回正文/作者补齐合法数学绑定，或提供可引用教材源图。技术编译器不能猜补、删除请求或降为 none。')
    mapping={r['request_key']:r['request_id'] for r in requests}
    decisions=[{**d,'request_ids':[mapping.get(key,key) if reviews_frozen() else mapping[key]
               for key in d.get('request_ids',[])]} for d in requirements['decisions']]
    plan={'schema_version':'MediaRequirementsV2','book_id':content['book_id'],'unit_id':content['unit_id'],
          'content_hash':content_hash(content),'approval_ref':digest_of(approval),'policy_digest':approval.get('policy_digest'),
          'media_policy_version':POLICY_VERSION,'capability_digest':digest_of(capabilities),'capabilities':deepcopy(capabilities),
          'author_media':record,'author_media_hash':digest_of(record),
          'revision':revision,'decisions':decisions,'requests':requests}
    if approval.get('schema_version')=='ContentIdentityV1':
        plan['schema_version']='MediaRequirementsV3'
        plan['content_identity_hash']=plan.pop('approval_ref')
    try:
        if reviews_frozen() or capabilities.get('code_generation') == 'GeneratedMediaCodeV1':
            # Production has one code producer per author media kind. No second
            # model may rewrite intent or sneak in an underspecified inline spec.
            for request in requests:
                request['kind']='animation' if request['kind']=='animation' else 'drawing'
        elif requests:
            # Explicit historical capability profiles remain replayable. The
            # current engine never advertises these template/DSL producers.
            if client is None: raise CapabilityError('作者媒体请求缺技术编译传输；不得撤销请求')
            messages=planning_prompt(content,approval,capabilities,record)
            if repair_context:
                messages.append({'role':'user','content':json.dumps({'repair_context':repair_context,
                    'instruction':'只修授权技术问题；所有作者请求身份、语义和原标准必须保留'},ensure_ascii=False)})
            raw=client.chat_json(messages,tag='media-plan:'+str(content['unit_id']))['data']
            if not isinstance(raw,dict) or set(raw)!={'requests'} or not isinstance(raw['requests'],list):
                raise ContractError('技术编译器只能返回 requests，不得重做作者 decisions')
            technical={}
            for row in raw['requests']:
                if (not isinstance(row,dict) or not {'request_key','kind'}<=set(row)
                        or set(row)-{'request_key','kind','source_image_id','source_refs','spec','library_id'}):
                    raise ContractError('技术编译器不得返回或改写作者语义字段')
                key=row['request_key']
                if not isinstance(key,str) or key in technical: raise ContractError('技术编译请求身份缺失或重复')
                technical[key]=row
            if set(technical)!=set(mapping): raise ContractError('技术编译不得丢失或新增作者请求')
            for request in requests:
                row=technical[request['request_key']]
                allowed={'animation'} if request['kind']=='animation' else {'source_image','plot','geometry','diagram'}
                if row['kind'] not in allowed: raise ContractError('技术编译改变作者媒体类型')
                request['kind']=row['kind']
                for key in ('source_image_id','spec','library_id'):
                    if key in row: request[key]=deepcopy(row[key])
                for source in row.get('source_refs',[]):
                    if source.get('kind')=='approved_content':
                        if source not in request['source_refs']: raise ContractError('技术编译不得替换作者正文来源绑定')
                    elif source not in request['source_refs']: request['source_refs'].append(deepcopy(source))
        _freeze_repair_criteria({'requests':requests},repair_context)
        validate_requirements(plan,content,approval,capabilities=capabilities)
    except ContractError as exc: raise MediaPlanningError(str(exc),plan) from exc
    if reviews_frozen(): plan['review_outcome']=frozen_review()
    return plan
