"""Run the same frozen positive/negative controls in the ordinary test suite."""
import json
from pathlib import Path
import pytest
from controlled_cases import CASES, frozen_review_inputs

MANIFEST=json.loads(Path(__file__).with_name('manifest.json').read_text())


@pytest.mark.parametrize('case',MANIFEST['adversarial_cases'],ids=lambda c:c['case_id'])
def test_controlled_production_behavior(case):
    binding=case['binding']
    assert binding['variant'] in {'valid','damaged'}
    measured=CASES[binding['function']](binding['variant']=='damaged')
    assert measured['observed']==case['expected'], json.dumps(measured,ensure_ascii=False)


def test_optional_quote_does_not_authorize_an_unknown_evidence_id():
    from aiforstudy.blocks.content.review import validate_verdicts
    from aiforstudy.errors import ContractError
    item,catalog,verdict=frozen_review_inputs()
    verdict['evidence_refs'][0]={'evidence_id':'reader:not-current'}
    with pytest.raises(ContractError,match='unknown_evidence_id'):
        validate_verdicts([item],{'verdicts':[verdict]},catalog)


def test_substantive_failure_remains_unaccepted_without_a_quote():
    from aiforstudy.blocks.content.review import validate_verdicts
    item,catalog,verdict=frozen_review_inputs()
    catalog['reader:step']['text']='对于包括零在内的任何实数，x/x=1。'
    verdict.update(status='fail',reason='正文“包括零”使 x=0 时除法无定义，应保留 x≠0 条件。',
        evidence_refs=[{'evidence_id':'reader:step'}])
    report=validate_verdicts([item],{'verdicts':[verdict]},catalog).as_dict()
    assert report['decision']=='needs_review'
    assert report['blocking_issues'][0]['reason']==verdict['reason']
