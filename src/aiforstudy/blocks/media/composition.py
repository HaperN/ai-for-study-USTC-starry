"""Frozen reader hashes and outcome labels; no independent composition replay.

D8: delivery verifies snapshot and member consistency only. It does not
rederive placement/layout, so a consistently assembled layout error is a
rendering defect that this boundary deliberately does not detect.
"""
from copy import deepcopy
import json
from pathlib import Path
from ...core.digest import digest_of, digest_file
from ...errors import ContractError
from ...core.review_mode import reviews_frozen, frozen_review, substantive_reviews_enabled


def _reader_data(snapshot):
    try:
        text=(Path(snapshot['output_dir'])/'data/data.js').read_text(encoding='utf-8')
        return json.loads(text.split('window.__DATA__ = ',1)[1].rsplit(';',1)[0])
    except (OSError,ValueError,KeyError,IndexError,TypeError) as exc:
        raise ContractError('reader composition data envelope invalid') from exc


def reader_projection_binding(snapshot):
    """Record current output identities; this alone never grants acceptance.

    Full file digests include animation sample times and all emitted fields. No
    clocks, paths, layout metadata or media declarations are silently dropped.
    """
    data=_reader_data(snapshot); output=Path(snapshot['output_dir'])
    return {'schema_version':'ReaderCompositionV1','snapshot_hash':snapshot['snapshot_hash'],
        'view_hash':digest_file(output/'data/data.js'),
        'placements_hash':digest_file(output/'media-placement-plan.json'),
        'resources_hash':digest_of(snapshot['resources']),
        'units':{unit:{'content_hash':row['reader_document']['content_hash'],
            'reader_document_hash':digest_of(row['reader_document']),
            'reader_layout_hash':digest_of(row['reader_layout']),
            'reader_media_hash':digest_of(row['reader_media'])}
            for unit,row in data['units'].items()}}


def reader_projection_status(snapshot, browser_report):
    """Outer display labels are derived from evidence, never approval inputs."""
    if reviews_frozen() and not substantive_reviews_enabled(): return frozen_review(),bool(snapshot.get('missing'))
    from ..content.review_outcome import outcome, aggregate_outcomes
    status=browser_report.get('status')
    if status not in ('accepted','failed','blocked'):
        raise ContractError('reader browser check status invalid')
    review=deepcopy(snapshot['review_outcome'])
    if status!='accepted':
        review=aggregate_outcomes([review,outcome('unverified' if status=='blocked' else 'missing',
            [browser_report.get('detail','')])])
    return review,bool(snapshot['missing'] or status=='failed')



def validate_reader_export(snapshot, binding, *, exported, browser_report):
    """Compare frozen bytes plus the explicit browser annotation, not layout."""
    if reviews_frozen(): return
    from ..render.diagnostics import reader_check_html
    if reader_projection_binding(snapshot)!=binding:
        raise ContractError('reader composition output differs from recorded binding')
    source=Path(snapshot['output_dir']); exported=Path(exported)
    source_members={str(p.relative_to(source)) for p in source.rglob('*') if p.is_file()}
    exported_members={str(p.relative_to(exported)) for p in exported.rglob('*') if p.is_file()}
    if exported_members!=source_members|{'browser-report.json'}:
        raise ContractError('reader exported member inventory differs from snapshot')
    for name in source_members:
        if name=='index.html':
            expected=reader_check_html((source/name).read_text(encoding='utf-8'),browser_report).encode('utf-8')
            if (exported/name).read_bytes()!=expected:
                raise ContractError('reader exported HTML differs from snapshot and browser annotation')
        elif digest_file(source/name)!=digest_file(exported/name):
            raise ContractError('reader exported member bytes differ from snapshot: '+name)
    expected=json.dumps(browser_report,ensure_ascii=False,indent=2).encode('utf-8')
    if (exported/'browser-report.json').read_bytes()!=expected:
        raise ContractError('reader exported browser report differs from receipt')
    return True
