"""Host binding from a delegated goal to its exact approved reader evidence."""
from copy import deepcopy

from ...core.digest import digest_of
from ...errors import ContractError
from ..media.media_requirements import candidates, content_hash, resolve_bound_placement


def model_source_id(unit_id, model_id):
    if not isinstance(unit_id, str) or not unit_id or not isinstance(model_id, str) or not model_id:
        raise ContractError('交互 unit/model ID 必须非空')
    return unit_id+'.'+model_id


def bind_assignment_placement(content, target_id, model, placement=None):
    """Accept explicit source placement or uniquely resolve a legacy anchor hint."""
    target = next((row for row in candidates(content) if row['target_id'] == target_id), None)
    if target is None:
        raise ContractError('交互目标不属于批准教学目标: '+str(target_id))
    # One field may serve a goal in several evidence roles; it is still one field.
    evidence = {row['pointer']:row for row in target['reader_evidence']}
    if placement is None:
        anchor = model.get('anchor_hint')
        matches = [(row, index) for row in evidence.values() for index in range(len(row['text']))
                   if isinstance(anchor, str) and anchor and row['text'].startswith(anchor, index)]
        if len(matches) != 1:
            raise ContractError('交互 anchor_hint 必须唯一匹配目标教学证据；重复需显式 occurrence')
        row, _ = matches[0]
        placement = {'target_pointer':row['pointer'], 'owner_id':row['owner_id'],
                     'step_id':row.get('step_id',''), 'anchor_text':anchor, 'position':'after'}
    if not isinstance(placement, dict) or placement.get('target_pointer') not in evidence:
        raise ContractError('交互落点不属于委派目标 teaching_evidence')
    if 'field_hash' in placement or 'span' in placement:
        resolved = resolve_bound_placement(content, placement)
        return {key:deepcopy(value) for key,value in resolved.items()
                if key in ('target_pointer','owner_id','step_id','anchor_text','span','field_hash','position')}
    from ..content.content_contracts import AuthorPlacementV1
    from ..media.author_requirements import _placement
    try:
        authored = AuthorPlacementV1.model_validate(placement).as_dict()
    except (TypeError, ValueError) as exc:
        raise ContractError('交互落点结构无效: '+str(exc)) from exc
    return _placement(content, authored)


def validate_model_mounts(models, mounts, unit_id):
    """Every registered model is consumed exactly once by a matching mount."""
    if not isinstance(models, list) or not isinstance(mounts, list):
        raise ContractError('交互 models/mounts 必须为数组')
    expected = {}
    for model in models:
        if not isinstance(model, dict): raise ContractError('交互模型必须为对象')
        ident = model_source_id(unit_id, model.get('id'))
        if ident in expected: raise ContractError('交互模型 ID 重复')
        expected[ident] = digest_of(model)
    actual = []
    for mount in mounts:
        ident = mount.get('source_id')
        if (mount.get('source_kind') != 'interaction' or mount.get('unit_id') != unit_id
                or ident not in expected or mount.get('source_hash') != expected[ident]):
            raise ContractError('交互 mount/model 身份或规格摘要不一致')
        actual.append(ident)
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ContractError('交互 model/mount 必须一一对应，不能遗漏或多挂')
    return True


def bind_interaction_placements(content, requirements, interaction):
    from .spec import validate_assignment_coverage
    report = validate_assignment_coverage(requirements, interaction, content_hash(content))
    if not report['ok']:
        raise ContractError('；'.join(report['problems']))
    models = {model['id']:model for model in interaction.get('models', [])}
    rows = {row['target_id']:row for row in interaction.get('results', [])}
    mounts = []
    for decision in requirements.get('decisions', []):
        if decision.get('handled_by') != 'interaction': continue
        row = rows[decision['target_id']]
        if row['state'] != 'verified': continue
        model = models[row['model_id']]
        placement = bind_assignment_placement(content, row['target_id'], model, row.get('placement'))
        mounts.append({'unit_id':content['unit_id'], 'owner_id':placement['owner_id'],
            'target_pointer':placement['target_pointer'], 'source_kind':'interaction',
            'source_id':model_source_id(content['unit_id'], row['model_id']),
            'source_hash':digest_of(model), 'requested_span_cp':deepcopy(placement['span']),
            'edge':placement['position'], 'field_hash':placement['field_hash'],
            'anchor_text':placement['anchor_text']})
    validate_model_mounts(interaction.get('models', []), mounts, content['unit_id'])
    return mounts
