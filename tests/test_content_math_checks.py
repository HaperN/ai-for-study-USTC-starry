from copy import deepcopy
import pytest
from aiforstudy.blocks.content.content_contracts import content_digest
from aiforstudy.blocks.math.contracts import math_schema_example


def content():
    return {'unit_id':'U','math_models':[math_schema_example()]}


def test_content_math_checks_execute_and_bind_actual_models():
    from aiforstudy.blocks.content.math_checks import check_content_math
    c=content(); report=check_content_math(c)
    assert report['input_hash']==content_digest(c)
    assert report['status']=='pass'
    assert report['checks'] and all(r['status']=='pass' for r in report['checks'])
    assert all(r['model_digest']==report['models'][r['model_id']] for r in report['checks'])


def test_cancelled_math_is_not_eligible_for_semantic_fallback():
    from aiforstudy.blocks.content.math_checks import check_content_math, math_review_items
    from aiforstudy.blocks.content.content_policy import load_content_policy
    c=content(); report=check_content_math(c,cancel=lambda:True)
    assert report['status']=='unknown'
    assert report['checks'][0]['reason_code']=='cancelled'
    assert math_review_items(c,load_content_policy(),report)==()


def test_unknown_checks_get_separate_frozen_source_review_items():
    from aiforstudy.blocks.content.math_checks import math_review_items
    from aiforstudy.blocks.content.content_policy import load_content_policy
    c=content(); p=load_content_policy()
    report={'checks':[{'model_id':'M','claim_id':'K','relation_id':'R','status':'unknown',
                      'reason_code':'capability_limit','reason':'abstract relation'}]}
    items=math_review_items(c,p,report)
    assert len(items)==1 and items[0]['subject_id']=='K'
    assert items[0]['criterion_id']=='MATH-SEMANTIC:M:R'
    assert items[0]['required_evidence_kinds']==[]
    assert items[0]['content_hash']==content_digest(c)
    assert 'abstract relation' in items[0]['expected']


def test_empty_models_are_explicitly_not_applicable():
    from aiforstudy.blocks.content.math_checks import check_content_math
    r=check_content_math({'unit_id':'U','math_models':[]})
    assert r['status']=='not_applicable' and r['checks']==[]
