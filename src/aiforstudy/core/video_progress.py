"""Durable suspension of content work that has stopped making evidence-bound progress.

This is not a cost/time budget. Technical failures and cancellation remain the
owner's responsibility; only a rejected model candidate is suspended here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .jsonio import write_json_atomic


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class VideoContentDeferred(ValueError):
    """A preserved, source-scoped content failure, not permission to accept it."""
    def __init__(self, key, candidate, reason, attempts):
        self.key, self.candidate, self.reason, self.attempts = key, deepcopy(candidate), reason, attempts
        super().__init__('局部模型内容未取得可验证进展，已保留证据并暂存待核；其余内容继续处理')


class VideoValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__('; '.join(error['message'] for error in errors))


class ContentProgress:
    def __init__(self, root, role, data, images, producer):
        self.root = Path(root).resolve()
        # Tags, revision UUIDs and prompt wording are not new source evidence.
        self.identity = {'role': role, 'context': _evidence_context(data), 'images': [_sha(p) for p in images], 'producer': producer}
        self.key = _digest(self.identity)
        self.path = self.root / 'content-progress' / (self.key + '.json')

    def resume(self):
        if not self.path.exists():
            return
        if self.path.is_symlink():
            raise ValueError('Content progress integrity mismatch')
        row = json.loads(self.path.read_text(encoding='utf-8'))
        if row.get('schema_version') != 1 or row.get('key') != self.key or row.get('state') not in {'deferred', 'completed'}:
            raise ValueError('Content progress integrity mismatch')
        relative = row.get('output', '')
        path = self.root / relative
        if (not relative or Path(relative).is_absolute() or '..' in Path(relative).parts or
                path.is_symlink() or not path.resolve().is_relative_to(self.root) or
                not path.is_file() or _sha(path) != row.get('output_sha256')):
            raise ValueError('Deferred model response integrity checksum mismatch')
        saved = json.loads(path.read_text(encoding='utf-8'))
        if row['state'] == 'completed':
            return {'candidate': deepcopy(saved.get('validated_data', saved.get('data'))), 'receipt': row}
        raise VideoContentDeferred(self.key, saved.get('retained_candidate', saved.get('validated_data', saved.get('data'))),
                                   row['reason'], row['attempts'])

    def complete(self, output, attempts):
        output = Path(output).resolve()
        if not output.is_relative_to(self.root):
            raise ValueError('Model response escaped job directory')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, {'schema_version': 1, 'key': self.key, 'state': 'completed',
            'attempts': attempts, 'output': output.relative_to(self.root).as_posix(), 'output_sha256': _sha(output)})

    def suspend(self, output, candidate, reason, attempts):
        output = Path(output).resolve()
        if not output.is_relative_to(self.root):
            raise ValueError('Deferred response escaped job directory')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, {'schema_version': 1, 'key': self.key, 'state': 'deferred',
            'reason': reason, 'attempts': attempts, 'output': output.relative_to(self.root).as_posix(),
            'output_sha256': _sha(output)})
        raise VideoContentDeferred(self.key, candidate, reason, attempts)


def _evidence_context(value):
    """Generated suspension bookkeeping is not new evidence for another request."""
    if isinstance(value, dict):
        return {key: _evidence_context(child) for key, child in value.items()
                if key not in {'deferred_work', 'processing_status', 'deferred_source_ids',
                               'reconciliation', 'tag_prefix', 'input_digest', 'reviewed_lesson_digest'} and
                not (key == 'previous_review' and isinstance(child, dict) and child.get('state') == 'deferred')}
    if isinstance(value, list):
        return [_evidence_context(child) for child in value]
    return value


def problem_keys(errors):
    """Compare stable problem locations, not the model's wording or response length."""
    return {(str(e.get('path') or 'response'), str(e.get('code') or 'invalid')) for e in errors}


def without_generated_progress(value):
    """Only the host may introduce processing gaps or dependency status."""
    if isinstance(value, dict):
        return {key: without_generated_progress(child) for key, child in value.items()
                if key not in {'deferred_work', 'processing_status', 'deferred_source_ids'}}
    if isinstance(value, list):
        return [without_generated_progress(child) for child in value]
    return deepcopy(value)


def content_gap(error, stage, source_ids, start, end):
    identity = {'request': error.key, 'stage': stage, 'sources': sorted(set(source_ids)), 'start': start, 'end': end}
    return {'id': 'deferred-' + _digest(identity)[:20], 'request_id': error.key, 'state': 'deferred', 'stage': stage,
            'start': start, 'end': end, 'source_ids': list(dict.fromkeys(source_ids)),
            'reason': error.reason, 'attempts': error.attempts,
            'message': '本处模型内容未能完成可靠整理，原始来源保留；不阻塞其余内容。'}


def stage_label(stage):
    return {'asr': '语音识别', 'ocr': '画面文字识别', 'vision': '画面理解', 'lesson': '分段讲义',
            'reconcile': '全片校订', 'review': '全片审核'}.get(stage, '局部整理')


def merge_deferred(*collections):
    rows = {}
    for collection in collections:
        for row in collection or []:
            rows[row['id']] = deepcopy(row)
    return list(rows.values())


def salvage_lesson(candidate, source_ids, duration, start, end, gap):
    """Keep independently source-valid neighbors; represent uncovered time as gaps."""
    from .video_analysis import validate_lesson
    candidate = candidate if isinstance(candidate, dict) else {}
    sections, formulas = [], []
    for section in candidate.get('sections', []) if isinstance(candidate.get('sections'), list) else []:
        try:
            validate_lesson({'sections': [section]}, source_ids, duration)
            if not start <= section['start'] < section['end'] <= end + .1:
                continue
        except (ValueError, TypeError, KeyError):
            continue
        section = deepcopy(section)
        section.update(processing_status='partial', deferred_work=[deepcopy(gap)])
        sections.append(section)
    intervals = sorted((s['start'], s['end']) for s in sections)
    missing, cursor = [], start
    for lo, hi in intervals:
        if lo > cursor:
            missing.append((cursor, lo))
        cursor = max(cursor, hi)
    if cursor < end:
        missing.append((cursor, end))
    refs = list(gap['source_ids'])
    if not refs:
        raise ValueError('No source evidence available for deferred window')
    for index, (lo, hi) in enumerate(missing):
        sections.append({'id': 'deferred-' + str(index), 'title': '本段讲义待核实', 'start': lo, 'end': hi,
            'markdown': '本段尚未整理出可靠讲义，已保留原始自动转写和画面。请按来源回看；不把缺失内容补成确定结论。' + ''.join('[' + x + ']' for x in refs),
            'evidence_ids': refs, 'processing_status': 'deferred', 'deferred_work': [deepcopy(gap)]})
    for formula in candidate.get('formulas', []) if isinstance(candidate.get('formulas'), list) else []:
        try:
            validate_lesson({'sections': sections, 'formulas': [formula]}, source_ids, duration)
            if formula.get('uncertainty', '').strip():
                continue
        except (ValueError, TypeError, KeyError):
            continue
        formulas.append(deepcopy(formula))
    notes = candidate.get('uncertainties', [])
    notes = [x for x in notes if isinstance(x, str)] if isinstance(notes, list) else []
    notes.append('本段有局部整理或来源校验未完成，问题已暂存；未核内容不作为确定结论。' + ''.join('[' + x + ']' for x in refs))
    return {'sections': sorted(sections, key=lambda s: (s['start'], s['end'])),
            'formulas': formulas, 'uncertainties': notes, 'deferred_work': [deepcopy(gap)]}


def evidence_gaps(frames, transcript):
    gaps = merge_deferred(*(frame.get('deferred_work', []) for frame in frames))
    for segment in transcript.get('segments', []):
        if segment.get('status') == 'failed' or {'repetition_loop', 'token_limit'} & set(segment.get('quality_warnings', [])):
            gaps.append({'id': 'asr-gap-' + _digest(segment['id'])[:20], 'state': 'deferred', 'stage': 'asr',
                'start': segment['start'], 'end': segment['end'], 'source_ids': [segment['id']],
                'reason': 'recognition_incomplete', 'message': '本音频窗口识别失败、重复或截断，原音保留，相关内容未核实。'})
    return merge_deferred(gaps)


def protect_deferred_dependencies(lesson, frames, transcript):
    """Keep uncertainties attached to explicit citations, never certify a guessed formula."""
    result = deepcopy(lesson)
    gaps = merge_deferred(result.get('deferred_work', []), evidence_gaps(frames, transcript))
    result['deferred_work'] = gaps
    blocked = {source for gap in gaps if gap.get('stage') in {'asr', 'vision'} for source in gap['source_ids']}
    for section in result['sections']:
        pending = sorted(blocked.intersection(section['evidence_ids']))
        if pending:
            section['processing_status'] = 'partial'
            section['deferred_source_ids'] = pending
    retained = []
    notes = list(result.get('uncertainties', []))
    for formula in result.get('formulas', []):
        pending = blocked.intersection(formula['source_ids'])
        if formula.get('uncertainty') or pending:
            notes.append('公式候选尚待核实，不进入确定公式索引：' + formula['latex'] + '；' +
                         (formula.get('uncertainty') or '引用了未完成识别的来源') +
                         ''.join('[' + x + ']' for x in formula['source_ids']))
        else:
            retained.append(formula)
    result['formulas'] = retained
    result['uncertainties'] = list(dict.fromkeys(notes))
    return result
