"""Media pointers and source evidence retain strict mathematical boundaries."""
import json
from pathlib import Path
import pytest
from aiforstudy.blocks.visual.spec import parse_visual_spec
from aiforstudy.blocks.content.review import validate_verdicts
from aiforstudy.blocks.content.repair import finding_ticket
from aiforstudy.errors import ContractError
from test_feedback import review_case


def test_visual_dependency_is_json_pointer_not_filesystem_path():
    spec=json.loads((Path(__file__).parent/'fixtures/visual/vector-sum.spec.json').read_text())
    spec['dependency_paths']=['/teaching_plan/claims/0']
    assert parse_visual_spec(spec).dependency_paths==('/teaching_plan/claims/0',)


@pytest.mark.parametrize('source_quote', ['非负整数', 'n 的取值范围包括零'])
def test_source_evidence_is_separate_from_diagnosis_and_preserves_instructions(source_quote):
    content, source, policy, criterion, catalog, verdict = review_case()
    for ref in verdict['evidence_refs']:
        if catalog[ref['evidence_id']]['kind']=='source': ref['quote']=source_quote
    verdict['reason']='该表述夸大原文适用范围，因此须修改。'
    checked=validate_verdicts([criterion],{'verdicts':[verdict]},catalog)
    ticket=finding_ticket(content,source,policy,checked.blocking_issues[0],catalog)
    assert ticket.status=='ready'
    assert ticket.repair_instructions['action']=='明确包含零'
    assert ticket.criteria[0].criterion_id==criterion['criterion_id']
    assert all(span.quote in source['source_text'] for span in ticket.source_evidence)
    assert all(span.quote!=verdict['reason'] for span in ticket.source_evidence)
