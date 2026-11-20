from copy import deepcopy
import pytest
from aiforstudy.errors import ContractError
from aiforstudy.core.digest import digest_of


def test_precise_explainable_span_rejects_stale_field_or_owner():
    from aiforstudy.blocks.media.media_requirements import resolve_bound_placement
    content={'explainables':[{'id':'E','markdown':'关系。关系。'}]}
    placement={'target_pointer':'/explainables/0/markdown','owner_id':'E','field_hash':digest_of('关系。关系。'),'anchor_text':'关系。','span':[3,6],'position':'after'}
    assert resolve_bound_placement(content,placement)['offset']==6
    placement['owner_id']='wrong'
    with pytest.raises(ContractError): resolve_bound_placement(content,placement)


def test_interaction_requires_each_assignment_and_rejects_extra():
    from aiforstudy.blocks.interaction.spec import validate_assignment_coverage
    requirements={'content_hash':'C','decisions':[{'target_id':'A','requirement':'required','handled_by':'interaction'},{'target_id':'B','requirement':'required','handled_by':'interaction'}]}
    models=[{'id':mid,'binds_to':target,'template':'harmonic-contours','kind':'svg',
             'params':{'initial_point':[1,1],'extent':3}} for mid,target in [('M','A'),('N','B')]]
    result={'content_hash':'C','requirements_hash':digest_of(requirements),'models':models,
            'results':[{'target_id':'A','state':'verified','model_id':'M','checks':{'ok':True},'spec':deepcopy(models[0])}]}
    assert not validate_assignment_coverage(requirements,result,'C')['ok']
    result['results'].append({'target_id':'B','state':'verified','model_id':'N','checks':{'ok':True},'spec':deepcopy(models[1])})
    assert validate_assignment_coverage(requirements,result,'C')['ok']
    result['results'].append({'target_id':'X','state':'verified','checks':{'ok':True},'spec':{'id':'X'}})
    assert not validate_assignment_coverage(requirements,result,'C')['ok']
