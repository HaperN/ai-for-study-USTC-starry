"""The production planner describes the exact VisualSpecV2 parser capabilities."""
from . import spec as S
from ..media.presentation_policy import presentation_policy
from ..math import parse_math_model


def visual_planner_contract():
    model=parse_math_model({'schema_version':'1.0','model_id':'example-square',
      'symbols':[{'name':'x','domain':'real','assumptions':[]}],
      'objects':[{'math_object_id':'f','kind':'scalar','expr':{'op':'div','left':{'op':'pow','base':{'op':'var','name':'x'},'exponent':2},'right':{'op':'const','value':2}}}],
      'conditions':[],'claims':[{'claim_id':'same','kind':'identity','math_object_id':'f','lhs_object_id':'f','rhs_object_id':'f'}],
      'source_refs':['approved-example']})
    example={'schema_version':'2.0','request_id':'animation-example','unit_id':'EXAMPLE',
      'goal_ids':['G'],'claim_ids':['K'],'math_model_refs':[{'model_id':model.model_id,'digest':model.digest}],
      'objects':[{'object_id':'formula','kind':'formula','model_id':model.model_id,'math_object_id':'f'}],
      'steps':[{'step_id':'s1','kind':'change','goal_id':'G','claim_ids':['K'],
        'math_claim_refs':[{'model_id':model.model_id,'claim_id':'same'}],'object_ids':['formula'],
        'observation':'观察平方项位于分子，二位于分母。','expected_change':'公式逐渐出现，使平方指数与分数层次可见。',
        'caption':'先看右上方平方指数，再看分母二。','caption_claim_ids':['K'],
        'start':0,'end':8,'actions':[{'action':'reveal','object_id':'formula'}],
        'checkpoints':[{'at':4,'note':'读出分子和分母'}]}]}
    example=S.parse_visual_spec(example).as_dict()
    return {'schema_version':'2.0','schema':S.describe_visual_contract(),'root_fields':sorted(S.V2_ROOT_FIELDS),'additional_fields':False,
      'object_fields':{k:sorted(v|{'object_id','kind','color','label'}) for k,v in S.V2_OBJECT_FIELDS.items()},
      'step_fields':sorted(S.V2_STEP_FIELDS),'step_kinds':list(S.V2_STEP_KINDS),
      'action_fields':{k:sorted(v) for k,v in S.V2_ACTION_FIELDS.items()},
      'spaces':list(S.V2_SPACES),'colors':list(S.COLORS),
      'limits':{**presentation_policy(),'max_objects':S.MAX_OBJECTS,'max_actions':S.MAX_ACTIONS,'max_caption_chars':S.MAX_CAPTION_CHARS,'max_curve_samples':S.MAX_CURVE_SAMPLES,'max_surface_resolution':S.MAX_MESH},
      'math_binding':{'math_model_refs':'exact approved request [{model_id,digest}]',
        'objects':'model_id + math_object_id from request.object_refs; never copy AST or TeX',
        'math_claim_refs':'[{model_id,claim_id,relation_id?}]: claim_id selects every mathematical relation for that teaching claim; an optional relation_id selects exactly one frozen relation and must belong to that claim. Every selected relation is independently verified.',
        'coverage':'change steps show every referenced mathematical claim object; observe may read a visible object of a relation fully demonstrated earlier; every changed mathematical object must belong to those claims'},
      'step_semantics':{'change':'nonempty supported actions must affect a bound mathematical object; reveal requires hidden object; hide/highlight/transform requires visible object',
        'observe':'empty actions, explicit observation purpose, positive duration, all object_ids already visible',
        'transform':'replace visible object_id with hidden target_object_id of the same supported kind',
        'caption_claim_ids':'must equal this step claim_ids; caption is plain text only',
        'timing':'start at 0, consecutive positive step intervals; checkpoints {at,note} inside step'},
      'example':example,'example_math_model':model.as_dict(),
      'example_policy':'Syntax example only. The host must approve and bind its model before compilation; never replace requested mathematics with this example.'}


def visual_planner_instructions():
    return ('你是数学动画规划器，只返回 VisualSpecV2 JSON，schema_version=2.0。'
      '严格采用 visual_contract 中当前解析器支持的字段、类型、动作和上限。'
      '对象只能引用批准 request 的 math_model_refs/object_refs，禁止新建 AST、任意 TeX、Python、shell、路径或 URL。'
      '每步绑定 goal_id、内容 claim_ids、数学 math_claim_refs、object_ids、observation、expected_change 与 caption_claim_ids。'
      'change 必须有可核对的数学对象变化，observe 必须有现存可见对象及阅读目的，空动作不能冒充变化。'
      '通常建议 4–8 步；单个8秒动态步骤也可合格。总时长遵循 visual_contract.limits 资源上限。'
      '公式使用 formula 对象，中文说明使用 label，字幕是纯文字。render_profile 由宿主决定，不能输出。'
      '不支持时返回 {"unsupported":"具体能力缺口"}，不得换题。')
