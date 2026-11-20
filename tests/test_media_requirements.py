"""Current media planning requires explicit goal decisions and V2 content approval."""
import pytest
from aiforstudy.errors import ContractError, CapabilityError
from aiforstudy.blocks.media.media_planning import plan_media_requirements
from support.content_case import approved_content as fixture, Planner, none_decision, author_media


def test_valid_empty_plan_covers_every_goal_with_explicit_reason():
    content, approval = fixture()
    client = Planner()
    plan = plan_media_requirements(content, approval, capabilities={'kinds':[]}, client=client, author_requirements=author_media(content))
    assert plan['schema_version'] == 'MediaRequirementsV2'
    assert plan['requests'] == []
    assert plan['decisions'] == [{**none_decision(),'not_produced_reason':''}]
    assert client.calls == 0, 'explicit author none needs no technical model call'


@pytest.mark.parametrize('field,value',[('decision','rejected'),('content_hash','old'),('policy_digest','')])
def test_invalid_receipt_stops_planning_before_call(field,value):
    content, approval = fixture(); approval[field]=value
    client = Planner()
    with pytest.raises(ContractError):
        plan_media_requirements(content, approval, capabilities={'kinds':[]}, client=client, author_requirements=author_media(content))
    assert client.calls == 0


def test_retired_automatic_none_does_not_replace_missing_review():
    content, approval = fixture()
    with pytest.raises(ContractError, match='作者|author'):
        plan_media_requirements(content, approval, capabilities={'kinds':[]})


def test_body_only_unit_retains_goal_specific_no_media_reason():
    content, approval = fixture()
    assert content['examples'] == [] and content['explainables'] == []
    plan=plan_media_requirements(content, approval, capabilities={'kinds':[]}, client=Planner(), author_requirements=author_media(content))
    assert plan['decisions'][0]['target_id'] == content['teaching_plan']['goals'][0]['id']
    assert plan['decisions'][0]['text_alternative'] and plan['requests'] == []
