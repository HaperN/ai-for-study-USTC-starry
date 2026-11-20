import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context


def test_run_mode_is_frozen_and_restores_without_cross_thread_leak(tmp_path):
    policy = importlib.import_module('aiforstudy.core.review_mode')
    (tmp_path / 'config').mkdir()
    path = tmp_path / 'config/review_mode.json'
    path.write_text(json.dumps({'mode': 'json_only'}))
    mode = policy.configured_review_mode(tmp_path)
    with policy.review_mode(mode):
        path.write_text(json.dumps({'mode': 'full'}))
        assert policy.reviews_frozen()
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(copy_context().run, policy.reviews_frozen).result()
        with policy.review_mode('full'):
            assert not policy.reviews_frozen()
        assert policy.reviews_frozen()
    assert policy.configured_review_mode(tmp_path) == 'full'


def test_valid_json_metadata_can_be_hashed_without_business_field_rejection():
    from aiforstudy.core.digest import digest_of, DigestError
    from aiforstudy.core.review_mode import review_mode
    import pytest
    data = {'timestamp': 'author optional note', 'nested': {'duration': 2}}
    with review_mode('json_only'):
        assert digest_of(data) == digest_of(data, check=False)
    with review_mode('full'), pytest.raises(DigestError):
        digest_of(data)


def test_frozen_json_feedback_does_not_require_quality_review():
    import pytest
    from aiforstudy.blocks.content.generation_corrections import _decode_protocol, LocalCorrectionRequired
    from aiforstudy.core.review_mode import review_mode
    with review_mode('json_only'), pytest.raises(LocalCorrectionRequired) as caught:
        _decode_protocol('{"x":}', None, None)
    assert '教学内容仍须审核' not in caught.value.errors[0]['passing_criterion']
