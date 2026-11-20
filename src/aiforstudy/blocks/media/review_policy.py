"""One host-owned policy for first review, repair and media approval."""
from copy import deepcopy
import json

from ...core.digest import digest_text
from ...core.prompt_assets import PromptAsset, compose_prompt_assets
from ...errors import ContractError
from ..render.display_policy import reader_display_policy

REVIEW_POLICY_VERSION = 'media-review-v7-desktop-comparison'
DEFAULT_DISPLAY_PROFILES = tuple(reader_display_policy()['profiles'])

def code_review_criteria(*, animation=False):
    """Host criteria that cannot be omitted by a code producer or its prompt."""
    rows=[{'criterion_id':'host.code.mathematical-correspondence','version':'1','kind':'mathematical',
           'statement':'对照批准依赖中的数学对象、数值、条件和正文引文，实际画面的曲线/几何/公式/标签准确对应；输入绑定不是画面数学证明。',
           'evidence_required':['rendered_frame','approved_dependencies','object_bindings','mathematical_report']}]
    if animation:
        rows.append({'criterion_id':'host.code.author-step-coverage','version':'1','kind':'pedagogical',
                     'statement':'作者必要讲解状态按顺序完成并停留，保留关键中间式、条件和最终结论；审核仅核对已提供完成状态的公式、关系和标签，不能由少数画面认证全程运动或字幕同步。',
                     'evidence_required':['request','rendered_frame','steps','captions']})
    return rows


def _media_kind(kind):
    if kind in ('drawing', 'image', 'source_image', 'static', 'plot', 'geometry', 'diagram'):
        return 'static'
    if kind == 'animation':
        return 'animation'
    raise ContractError('unknown media prompt kind '+str(kind))


def _host_criteria():
    return [
        {'criterion_id':'host.media.readability','version':'1','kind':'visual',
         'statement':'关键标签和图示可访问，没有使对象或数学关系无法辨认的遮挡裁切；字号、留白、配色与轻微占宽差异仅作外观提醒，不判内容失败。',
         'evidence_required':['rendered_frame']},
        {'criterion_id':'host.media.alignment','version':'1','kind':'pedagogical',
         'statement':'实际图片与冻结请求、数学对象、图题alt和正文教学观察一致。',
         'evidence_required':['rendered_frame']},
    ]


def animation_step_caption_criterion():
    return {'criterion_id':'host.animation.step-caption','version':'1','kind':'pedagogical',
            'statement':'所给步骤完成画面、相邻步骤含义说明及实际字幕文本不矛盾；未拍到连续变化不等于缺步骤，几张状态图不证明全程同步。',
            'evidence_required':['rendered_frame','captions','steps']}


def build_media_quality_contract(kind: str) -> dict:
    """Only host quality, display and evidence requirements; no judge protocol."""
    animation = _media_kind(kind) == 'animation'
    criteria = _host_criteria() + code_review_criteria(animation=animation)
    if animation:
        criteria.append(animation_step_caption_criterion())
    return {'schema_version':'MediaQualityContractV1', 'kind':_media_kind(kind),
            'display_profiles':deepcopy(list(DEFAULT_DISPLAY_PROFILES)), 'criteria':criteria}


def build_media_review_prompt(kind: str) -> PromptAsset:
    """Compose one type's prose and protocol examples from real host criteria."""
    media_kind = _media_kind(kind)
    asset = compose_prompt_assets('media/review-common.md', f'media/review-{media_kind}.md')
    criteria = code_review_criteria(animation=media_kind == 'animation')
    examples = []
    for status in ('pass', 'fail'):
        verdicts = []
        for item in criteria:
            ids = ['frame:wide', 'approved:objects', 'bindings', 'math-report']
            if item['criterion_id'] == 'host.code.author-step-coverage':
                ids = ['frame:wide', 'request', 'steps', 'captions']
            verdicts.append({'criterion_id':item['criterion_id'], 'version':item['version'],
                'status':status, 'evidence_ids':ids,
                'why':'示例：实际画面与对应批准对象及教学要求一致。' if status == 'pass' else '示例：实际画面未体现对应冻结要求。',
                'problem':'' if status == 'pass' else '示例：指定对象或步骤未按冻结要求呈现。',
                'action':'' if status == 'pass' else '按冻结对象或步骤修复实际画面并重新取证。',
                'expected':'' if status == 'pass' else item['statement']})
        examples.append({'verdicts':verdicts, 'problems':[]})
    text = asset.text + '\n宿主生成的协议示例（合成证据；不可用于本次判定）：\n' + '\n'.join(
        '```json\n'+json.dumps(row, ensure_ascii=False)+'\n```' for row in examples)
    return PromptAsset(asset.name, text, digest_text(text))


def media_review_policy(kind='drawing'):
    return {'schema_version':'MediaReviewPolicyV2', 'version':REVIEW_POLICY_VERSION,
            'review_prompt_digest':build_media_review_prompt(kind).sha256,
            'minimum_label_css_px':min(p['min_font_px'] for p in DEFAULT_DISPLAY_PROFILES),
            'required_viewports':[p['width'] for p in DEFAULT_DISPLAY_PROFILES],
            'criteria':_host_criteria()}
