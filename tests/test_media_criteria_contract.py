"""Author schema and media executor share protocol keys, not prose labels."""
from typing import get_args

import pytest
from pydantic import ValidationError

from aiforstudy.blocks.content.content_contracts import AuthorCriterionV1, AuthorMediaEvidence
from aiforstudy.blocks.media.media_requirements import CRITERIA_VERSION, validate_criteria
from aiforstudy.core.digest import digest_of
from aiforstudy.errors import ContractError


def criterion(evidence):
    return dict(criterion_id='axis', version=CRITERIA_VERSION, kind='visual',
                statement='实轴、垂足与对称轴的标注完整且位置正确。', evidence_required=evidence)


def request(evidence, kind='image'):
    criteria = [criterion(evidence)]
    return dict(kind=kind, acceptance_criteria=criteria, criteria_digest=digest_of(criteria))


@pytest.mark.parametrize('key', get_args(AuthorMediaEvidence))
def test_each_author_evidence_key_has_the_same_pre_backend_runtime_contract(key):
    row = criterion([key])
    assert AuthorCriterionV1.model_validate(row).evidence_required == [key]
    validate_criteria(request([key]), backend=False)


def test_free_text_is_a_teaching_standard_but_not_an_evidence_key():
    value = criterion(['rendered_frame'])
    assert AuthorCriterionV1.model_validate(value).statement == value['statement']
    value['evidence_required'] = ['实轴、垂足与对称轴的标注', '成图']
    with pytest.raises(ValidationError):
        AuthorCriterionV1.model_validate(value)
    with pytest.raises(ContractError, match='证据'):
        validate_criteria(request(value['evidence_required']), backend=False)


@pytest.mark.parametrize('kind,key,allowed', [
    ('source_image', 'source_provenance', True),
    ('source_image', 'object_bindings', False),
    ('drawing', 'source_provenance', False),
    ('drawing', 'object_bindings', True),
    ('drawing', 'steps', False),
    ('animation', 'captions', True),
    ('animation', 'steps', True),
])
def test_shared_schema_keys_do_not_override_backend_evidence_capability(kind, key, allowed):
    if allowed:
        validate_criteria(request([key], kind))
    else:
        with pytest.raises(ContractError, match='无法提供'):
            validate_criteria(request([key], kind))


def test_criterion_version_remains_string_one_in_both_interfaces():
    assert CRITERIA_VERSION == '1'
    value = criterion(['rendered_frame']); value['version'] = 1
    with pytest.raises(ValidationError):
        AuthorCriterionV1.model_validate(value)
    target = request(['rendered_frame']); target['acceptance_criteria'][0]['version'] = 1
    with pytest.raises(ContractError, match='version'):
        validate_criteria(target, backend=False)
