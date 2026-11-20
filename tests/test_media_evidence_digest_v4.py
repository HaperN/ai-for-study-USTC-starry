"""Media receipt hashes preserve timeline metadata without admitting run clocks.

The tiny files exercise the byte-binding contract only. The real default Runner
Manim integration separately exercises decoding, rendering and final delivery.
"""
from copy import deepcopy

import pytest

from aiforstudy.core.digest import DigestError, canonical_json, digest_of, digest_text
from aiforstudy.blocks.media.media_manifest import approve_media, validate_manifest
from test_animation_result import _fake_output, _full_result
from support.content_case import approved_content as fixture


def animation_inputs(tmp_path):
    content, approval = fixture()
    text = content['body_markdown']
    request = {'request_id': 'req-1', 'kind': 'animation', 'serves': ['S'],
        'placement': {'target_pointer': '/body_markdown', 'owner_id': content['unit_id'],
            'field_hash': digest_of(text), 'anchor_text': text, 'span': [0, len(text)], 'position': 'after'}}
    requirements = {'content_hash': approval['content_hash'], 'requests': [request], 'decisions': []}
    root = _fake_output(tmp_path)
    animation = _full_result(root)
    animation['steps'][0].update(start=0, end=4)
    animation['resources'][2].update(timestamp=0.5, step_id='s1')
    animation['technical_report']['video_info'] = {
        'streams': [{'codec_type': 'video', 'duration': '4.000'}], 'format': {'duration': '4.000'}}
    row = {'request_id': 'req-1', 'kind': 'animation', 'state': 'fulfilled',
        'request_hash': digest_of(request), 'source_dir': str(root),
        'resources': deepcopy(animation['resources']), 'animation': animation,
        'profile_results': {'draft': deepcopy(animation), 'final': deepcopy(animation)},
        'media_review': {'results': [deepcopy(animation)], 'reports': {'final': {'status': 'pass'}}}}
    manifest = {'schema_version': 'MediaManifestV1', 'content_hash': approval['content_hash'],
        'requirements_hash': digest_of(requirements), 'requests': [row],
        'animation_payload': {content['unit_id']: [deepcopy(animation)]}}
    return content, approval, requirements, manifest


def test_historical_animation_sample_times_hash_but_cannot_issue_current_approval(tmp_path):
    from aiforstudy.blocks.media.evidence_digest import manifest_digest
    from aiforstudy.errors import ContractError
    content, approval, requirements, manifest = animation_inputs(tmp_path)
    original = deepcopy(manifest)
    assert manifest_digest(manifest) == digest_text(canonical_json(manifest))
    with pytest.raises(ContractError, match='媒体批准要求当前需求契约'):
        approve_media(requirements, manifest, approval)
    assert manifest == original


@pytest.mark.parametrize('field', ['timestamp', 'duration', 'wall_clock', 'run_id', 'created_at', 'cwd'])
def test_run_metadata_outside_media_semantic_fields_is_rejected(tmp_path, field):
    _, _, requirements, manifest = animation_inputs(tmp_path)
    manifest[field] = '2026-10-08T16:00:00Z'
    with pytest.raises(DigestError):
        validate_manifest(requirements, manifest)


def test_global_digest_still_rejects_video_timestamp():
    with pytest.raises(DigestError):
        digest_of({'resources': [{'kind': 'keyframe', 'timestamp': 0.5}]})


@pytest.mark.parametrize('location', ['request', 'animation', 'worker', 'resource_metadata', 'video_metadata'])
def test_host_locations_do_not_exempt_nested_wall_clock_metadata(tmp_path, location):
    _, _, requirements, manifest = animation_inputs(tmp_path)
    row = manifest['requests'][0]
    locations = {'request': row, 'animation': row['animation'],
        'worker': row['animation']['technical_report'].setdefault('worker', {}),
        'resource_metadata': row['resources'][2],
        'video_metadata': row['animation']['technical_report']['video_info']['format']}
    locations[location]['metadata'] = {'timestamp': '2026-10-08T16:00:00Z'}
    with pytest.raises(DigestError):
        validate_manifest(requirements, manifest)


@pytest.mark.parametrize('value', ['2026-10-08T16:00:00Z', -1, float('nan'), float('inf'), True])
def test_keyframe_timestamp_requires_valid_video_seconds(tmp_path, value):
    _, _, requirements, manifest = animation_inputs(tmp_path)
    manifest['requests'][0]['resources'][2]['timestamp'] = value
    with pytest.raises(DigestError):
        validate_manifest(requirements, manifest)


def test_timeline_and_worker_evidence_mutations_invalidate_digest(tmp_path):
    from aiforstudy.blocks.media.evidence_digest import manifest_digest, placements_digest
    _, _, _, manifest = animation_inputs(tmp_path)
    worker = manifest['requests'][0]['animation']['technical_report'].setdefault('worker', {})
    worker['cwd'] = str(tmp_path / 'worker')
    original = manifest_digest(manifest)
    manifest['requests'][0]['resources'][2]['timestamp'] = 0.75
    assert manifest_digest(manifest) != original
    manifest['requests'][0]['resources'][2]['timestamp'] = 0.5
    worker['cwd'] = str(tmp_path / 'other-worker')
    assert manifest_digest(manifest) != original
    placement={'kind':'animation','resources':deepcopy(manifest['requests'][0]['resources'])}
    original_placements=placements_digest([placement])
    placement['resources'][2]['timestamp']=0.75
    assert placements_digest([placement]) != original_placements
    assert placements_digest([]) == digest_of([])


def test_frame_findings_and_frozen_source_spans_keep_sample_times(tmp_path):
    from aiforstudy.blocks.media.evidence_digest import manifest_digest
    _, _, _, manifest = animation_inputs(tmp_path)
    finding = {'step_id': 's1', 'object_id': 'vector', 'frame_hash': 'sha256:frame', 'timestamp': 0.5}
    row = manifest['requests'][0]
    row['frame_findings'] = [finding]
    row['media_review']['findings'] = [deepcopy(finding)]
    row['media_review']['reports']['final']['findings'] = [deepcopy(finding)]
    row['media_review']['reports']['final']['dropped_frames'] = [deepcopy(row['resources'][2])]
    span = {'step_id': 's1', 'frame_hash': 'sha256:frame', 'artifact_hash': 'sha256:spec', 'timestamp': 0.5}
    manifest['tickets'] = [{'source_evidence': [span]}]
    first = manifest_digest(manifest)
    span['timestamp'] = 0.75
    assert manifest_digest(manifest) != first
