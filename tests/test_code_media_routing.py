"""The production code backend cannot be replaced by an inline legacy spec."""
from copy import deepcopy
import pytest
from aiforstudy.blocks.media.media_planning import plan_media_requirements
from aiforstudy.errors import ContractError
from support.content_case import author_media
from test_media_decisions_v2 import math_request_case


class NeverPlanner:
    def chat_json(self, *args, **kwargs):
        pytest.fail('author-to-backend projection must not call a planning model')


def test_code_backend_preserves_author_and_ignores_legacy_inline_spec():
    content, approval, raw, _ = math_request_case()
    raw['requests'][0]['spec'] = {'arbitrary': 'old shortcut'}
    author = author_media(content, raw)
    caps = {'kinds': ['drawing', 'animation'], 'code_generation': 'GeneratedMediaCodeV1'}
    plan = plan_media_requirements(content, approval, author_requirements=author,
        capabilities=caps, client=NeverPlanner())
    request = plan['requests'][0]
    assert request['kind'] == 'drawing'
    assert 'spec' not in request
    assert request['observe'] == raw['requests'][0]['observe']
    assert request['acceptance_criteria'] == raw['requests'][0]['acceptance_criteria']
    assert request['placement']['anchor_text'] == raw['requests'][0]['placement']['anchor_text']


def test_code_backend_missing_capability_does_not_drop_request():
    content, approval, raw, _ = math_request_case()
    caps = {'kinds': [], 'code_generation': 'GeneratedMediaCodeV1'}
    with pytest.raises(ContractError, match='drawing'):
        plan_media_requirements(content, approval, author_requirements=author_media(content,raw),
            capabilities=caps, client=NeverPlanner())
