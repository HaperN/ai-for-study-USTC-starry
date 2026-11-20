import pytest
from aiforstudy.pipeline.repair import RepairRouteV1
from aiforstudy.errors import ContractError
from aiforstudy.contracts.stage import StageResult


@pytest.mark.parametrize('kind,expected',[('content','lesson'),('media_requirements','media_plan'),('visual_spec','figures'),('media_resource','figures'),('interaction_spec','interaction'),('evidence','audit')])
def test_artifact_controls_repair_target(kind, expected):
    route=RepairRouteV1('T','audit',kind,'A')
    assert route.target_stage == expected
    assert RepairRouteV1.from_dict(route.as_dict()) == route


@pytest.mark.parametrize('kind', ['unknown', 'interaction_result', 'composition'])
def test_route_rejects_unsupported_kind_and_extra_fields(kind):
    with pytest.raises(ContractError):
        RepairRouteV1('T','audit',kind,'A')
    with pytest.raises(ContractError):
        RepairRouteV1.from_dict({'ticket_id':'T','origin_stage':'audit','target_artifact_kind':'content','target_artifact_id':'A','owner_stage':'figures'})


def test_stage_route_is_separate_from_ticket_schema():
    route=RepairRouteV1('T','audit','content','A')
    result=StageResult.ok('audit', repair_routes=(route.as_dict(),))
    assert result.as_dict()['repair_routes']==[route.as_dict()]
    assert StageResult.ok('audit').repair_routes==()
