"""Content directives and routes must classify the same persisted ticket alike."""
from types import SimpleNamespace

import pytest

from aiforstudy.blocks.audit.contracts import validate_ticket
from aiforstudy.contracts.stage import StageResult
from aiforstudy.pipeline.content_stages import _with_routes
from aiforstudy.pipeline.stage_io import _ticket_directives
from aiforstudy.errors import ContractError
from aiforstudy.pipeline.runner import Runner
from test_repair_contract_v2 import _full_ticket_data


@pytest.mark.parametrize('changes,owner,kind,target', [
    ({}, 'lesson', 'text_content', 'lesson'),
    ({'previous_attempt':{'evidence_rejections':['unlocated quote']}}, 'lesson', 'text_content', 'lesson'),
    ({'kind':'evidence_format'}, 'reviewer', 'evidence_format', 'text_audit'),
    ({'owner':'reviewer'}, 'reviewer', 'evidence_format', 'text_audit'),
])
def test_production_routes_match_directives_and_register(changes, owner, kind, target):
    ticket = validate_ticket(_full_ticket_data(**changes))
    directives = _ticket_directives([ticket], ticket.unit_id, ticket.target['artifact_hash'])
    result = _with_routes(StageResult.ok('text_audit', repair_requests=tuple(directives)), [ticket])
    runner = Runner.__new__(Runner); runner._repair_routes = {}
    runner._register_repair_routes(SimpleNamespace(stage='text_audit'), result)
    assert directives[0].owner == owner and directives[0].kind == kind
    from aiforstudy.pipeline.repair import RepairRouteV1
    assert RepairRouteV1.from_dict(result.repair_routes[0]).target_stage == target


def test_retired_source_conflict_ticket_is_rejected_before_routing():
    with pytest.raises(ContractError, match='kind'):
        validate_ticket(_full_ticket_data(kind='source_conflict'))
