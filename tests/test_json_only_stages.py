from copy import deepcopy
import json

from aiforstudy.core.review_mode import review_mode
from support.content_case import approved_inputs, author_media
from test_media_plan_stage import ctx


def forbidden(*args, **kwargs):
    raise AssertionError('frozen review must not execute')


def test_text_audit_does_not_judge_invalid_math_or_create_approval(tmp_path, monkeypatch):
    from aiforstudy.pipeline import content_stages as stage
    content, source, *_ = approved_inputs()
    content = deepcopy(content)
    content['math_models'] = [{'id': 'invalid', 'relations': 'not a valid model'}]
    monkeypatch.setattr(stage, 'load_content', lambda *_: (content, {}))
    monkeypatch.setattr(stage, '_frozen_context', lambda *_: source)
    monkeypatch.setattr(stage, 'check_content', forbidden)
    monkeypatch.setattr(stage, 'approve_content', forbidden)
    with review_mode('json_only'):
        result = stage.text_audit_stage(ctx(tmp_path, 'text_audit'))
    assert result.execution_state.value == 'succeeded', result.error
    assert result.quality_state.value == 'not_checked'
    assert not result.repair_requests
    audit = json.loads((tmp_path / 'products/text_audit/U.json').read_text())['payload']
    assert audit['approval'] is None
    assert audit['review_outcome']['status'] == 'unverified'
    assert audit['review_status'] == 'not_run'


def test_stale_author_binding_and_known_error_do_not_block_media_chain(tmp_path, monkeypatch):
    from aiforstudy.pipeline import media_stages as stage
    content, *_ = approved_inputs()
    record = author_media(content)
    record.update(status='stale', errors=[{'code':'AUTHOR_MEDIA_DECISION_STALE'}])
    monkeypatch.setattr(stage, '_load_content', lambda *_: (content, {}))
    monkeypatch.setattr(stage, '_load_author_media', lambda *a, **k: record)
    monkeypatch.setattr(stage, '_capabilities', lambda *_: {'kinds': [], 'code_generation':'GeneratedMediaCodeV1'})
    monkeypatch.setattr(stage, '_client', forbidden)
    monkeypatch.setattr(stage, '_content_not_passed', lambda *_: '正文未过审（known_error）')
    with review_mode('json_only'):
        for name in ('media_plan', 'figures', 'audit', 'interaction'):
            result = getattr(stage, name + '_stage')(ctx(tmp_path, name))
            assert result.execution_state.value in ('succeeded', 'not_applicable'), (name, result.error)
            assert not result.repair_requests
        audit = json.loads((tmp_path / 'products/audit/U.json').read_text())['payload']
        assert audit['approval'] is None
        assert audit['review_status'] == 'not_run'


def test_interaction_preserves_generated_parameters_without_template_review(tmp_path, monkeypatch):
    from aiforstudy.pipeline.stage_io import _legacy
    module = _legacy('model_generate')
    content, *_ = approved_inputs()
    model = {'id':'visual', 'template':'complex_plane', 'binds_to':'unknown',
             'params': {'real':100}, 'target_id':'G', 'placement':{}}
    class Client:
        def chat_json(self, *a, **k):
            return {'data': {'models':[model]}}
    monkeypatch.setattr(module, 'normalize_models', forbidden)
    with review_mode('json_only'):
        result=module.generate_assigned_models(content, assignments=[{'target_id':'G'}],
            client=Client(),unit_id='U',parse_dir=tmp_path,requirements_hash='source')
    assert result['models'][0]['params']['real'] == 100
    assert result['results'][0]['state'] == 'unverified'


def test_publish_does_not_reject_legal_non_object_media_metadata(tmp_path):
    from aiforstudy.pipeline.content_stages import _publish_revision, load_author_media
    content, source, *_ = approved_inputs()
    context = ctx(tmp_path, 'lesson')
    with review_mode('json_only'):
        frozen, _, _ = _publish_revision(context, content, source, author_media=[])
        record = load_author_media(context, frozen)
    assert record['status'] == 'unverified'
    assert record['requirements']['unreviewed_requirements'] == []
