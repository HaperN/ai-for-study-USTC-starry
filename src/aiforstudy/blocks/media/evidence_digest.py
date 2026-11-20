"""Hashes for immutable media receipts, distinct from resource cache identities.

The original JSON (including video time and worker evidence) is fully bound.
Before hashing, only these host-owned schema locations may use keys forbidden
by the general logical-cache digest. Unknown metadata still uses its blacklist.
Nothing is removed or renamed in the persisted record or its hash input.
"""
from math import isfinite

from ...core.digest import DigestError, canonical_json, digest_of, digest_text


def _plain(value):
    digest_of(value)


def _fields(value, handlers):
    if not isinstance(value, dict):
        _plain(value)
        return
    for key, item in value.items():
        if key in handlers:
            handlers[key](item)
        else:
            _plain({key: item})


def _items(value, check):
    if not isinstance(value, list):
        _plain(value)
        return
    for item in value:
        check(item)


def _mapping(value, check):
    if not isinstance(value, dict):
        _plain(value)
        return
    for key, item in value.items():
        _plain({key: None})
        check(item)


def _seconds(value, *, ffprobe=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str) if ffprobe else (int, float)):
        raise DigestError('媒体时间必须为有限非负视频秒数')
    try:
        valid = isfinite(float(value)) and float(value) >= 0
    except (ValueError, OverflowError):
        valid = False
    if not valid:
        raise DigestError('媒体时间必须为有限非负视频秒数')


def _timed_resource(resource):
    def timestamp(value):
        if resource.get('kind') != 'keyframe' or resource.get('mime') != 'image/png' or not resource.get('step_id') or not resource.get('relative_path') or not str(resource.get('sha256', '')).startswith('sha256:'):
            raise DigestError('timestamp 只允许带步骤与字节身份的动画关键帧资源')
        _seconds(value)
    _fields(resource, {'timestamp': timestamp})


def _frame_finding(finding):
    def timestamp(value):
        if not finding.get('step_id') or not finding.get('object_id') or not finding.get('frame_hash'):
            raise DigestError('timestamp 只允许定位到步骤/对象/实际帧的画面发现')
        _seconds(value)
    _fields(finding, {'timestamp': timestamp})


def _source_span(span):
    def timestamp(value):
        if not span.get('step_id') or not span.get('frame_hash') or not span.get('artifact_hash'):
            raise DigestError('timestamp 只允许绑定实际帧的工单证据')
        _seconds(value)
    _fields(span, {'timestamp': timestamp})


def _ticket(ticket):
    _fields(ticket, {'source_evidence': lambda rows: _items(rows, _source_span)})


def _frame_report(report):
    _fields(report, {'findings': lambda rows: _items(rows, _frame_finding),
                    'dropped_frames': lambda rows: _items(rows, _timed_resource)})


def _video_info(info):
    def track(row):
        _fields(row, {'duration': lambda value: _seconds(value, ffprobe=True)})
    _fields(info, {'format': track, 'streams': lambda rows: _items(rows, track)})


def _technical_report(report):
    def worker(row):
        # A worker's cwd is execution evidence, never a resource cache identity.
        # Other clock/run metadata remains forbidden even within this record.
        def cwd(value):
            if not isinstance(value, str) or not value:
                raise DigestError('worker.cwd 必须为明确的执行目录证据')
        _fields(row, {'cwd': cwd})
    _fields(report, {'video_info': _video_info, 'worker': worker})


def _animation(result):
    _fields(result, {'resources': lambda rows: _items(rows, _timed_resource),
                     'frame_report': _frame_report, 'technical_report': _technical_report})


def _media_review(review):
    _fields(review, {'results': lambda rows: _items(rows, _animation),
                    'reports': lambda rows: _mapping(rows, _frame_report),
                    'findings': lambda rows: _items(rows, _frame_finding)})


def _offline_check(check):
    def evidence(row):
        def seconds(value):
            if check.get('name')!='animation.video': raise DigestError('duration 只允许实际视频流证据')
            _seconds(value,ffprobe=True)
        def video(value):
            if check.get('name')=='animation.video':
                _fields(value, {'duration': seconds})
            elif check.get('name')=='animation.resources': _offline_check(value)
            else: raise DigestError('嵌套video只允许动画资源检查或视频流属性')
        _fields(row, {'duration': seconds,'video': video})
    _fields(check, {'evidence': evidence})


def _validation(report):
    _fields(report, {'technical_checks': lambda rows: _items(rows, _offline_check)})


def validation_digest(report):
    """Bind a validation receipt, with seconds only at offline video evidence."""
    from ...core.review_mode import reviews_frozen
    if reviews_frozen(): return digest_text(canonical_json(report))
    _validation(report)
    return digest_text(canonical_json(report))


def _request(row):
    if not isinstance(row, dict) or row.get('kind') != 'animation':
        _plain(row)
        return
    _fields(row, {'resources': lambda rows: _items(rows, _timed_resource),
                 'animation': _animation,
                 'profile_results': lambda rows: _mapping(rows, _animation),
                 'media_review': _media_review,
                 'frame_findings': lambda rows: _items(rows, _frame_finding),
                 'repair_tickets': lambda rows: _items(rows, _ticket),
                 'validation': _validation})


def manifest_digest(manifest):
    """Bind an entire MediaManifest, allowing timeline fields only at its schema paths."""
    from ...core.review_mode import reviews_frozen
    if reviews_frozen(): return digest_text(canonical_json(manifest))
    _fields(manifest, {'requests': lambda rows: _items(rows, _request),
                      'animation_payload': lambda units: _mapping(units, lambda rows: _items(rows, _animation)),
                      'tickets': lambda rows: _items(rows, _ticket)})
    return digest_text(canonical_json(manifest))


def placements_digest(placements):
    """Bind every placement field, with video times only on animation resources."""
    from ...core.review_mode import reviews_frozen
    if reviews_frozen(): return digest_text(canonical_json(placements))
    def placement(row):
        if isinstance(row, dict) and row.get('kind') == 'animation':
            _fields(row, {'resources': lambda rows: _items(rows, _timed_resource)})
        else:
            _plain(row)
    _items(placements, placement)
    return digest_text(canonical_json(placements))
