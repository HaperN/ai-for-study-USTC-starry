"""A bad local fragment must not prevent a source-bound whole-lecture result."""
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest


def session_at(root, respond, relative="attempt", *, model="fixture", session_type=None):
    from aiforstudy.core.video_analysis_models import VideoModelSession
    from aiforstudy.pipeline.services import CancelToken
    from aiforstudy.providers.gateway import CallResult
    from aiforstudy.providers.llm import RoleConfig, ProviderConfig
    directory = root / relative
    (directory / "exchanges").mkdir(parents=True, exist_ok=True)
    session_type = session_type or VideoModelSession
    session = session_type.__new__(session_type)
    session.job_directory, session.directory = root, directory
    session.token, session.job_id = CancelToken(), "fixture"
    session.service = SimpleNamespace(
        config={"vision_batch_size": 1, "chapter_seconds": 10},
        _register_artifacts=lambda *args: None, _progress=lambda *args: None)
    session.config = ProviderConfig('fixture', '', '', None,
        {role: RoleConfig(role, model, 'low', 1024) for role in ('lesson', 'source_repair', 'text_judge')})
    def gateway(request):
        payload = respond(request)
        with (directory / 'model-calls.jsonl').open('a') as log:
            log.write(json.dumps({'event': 'response', 'tag': request.tag}) + '\n')
        return CallResult(ok=True, payload=payload, text=json.dumps(payload))
    session.gateway = SimpleNamespace(call=gateway)
    return session


def lesson_section(identifier, start, end, source, text):
    return {"id": identifier, "title": identifier, "start": start, "end": end,
            "markdown": text + " [" + source + "]", "evidence_ids": [source]}


def test_same_failed_content_is_not_retried_by_renaming_revision(tmp_path):
    calls = []
    def respond(request):
        calls.append(request.tag)
        return {"refs": ["invented"]}
    def validate(value):
        if value["refs"] != ["asr-1"]:
            raise ValueError("refs: invalid evidence")
    first = session_at(tmp_path, respond, "revisions/first")
    with pytest.raises(ValueError):
        first.call("lesson", "first-name", "instructions", {"evidence": "same"}, validate=validate)
    assert len(calls) == 2
    second = session_at(tmp_path, respond, "revisions/second")
    with pytest.raises(ValueError):
        second.call("lesson", "different-name", "cosmetic prompt change", {"evidence": "same"}, validate=validate)
    assert len(calls) == 2, "a new tag or revision is not new evidence"


def test_changed_evidence_can_reopen_suspended_content(tmp_path):
    calls = []
    def respond(request):
        calls.append(request.tag)
        return {"refs": ["asr-1" if "clearer" in request.messages[1]["content"] else "invented"]}
    def validate(value):
        if value["refs"] != ["asr-1"]:
            raise ValueError("refs: invalid evidence")
    first = session_at(tmp_path, respond, "revisions/first")
    with pytest.raises(ValueError):
        first.call("lesson", "first", "instructions", {"evidence": "unclear"}, validate=validate)
    second = session_at(tmp_path, respond, "revisions/second")
    result = second.call("lesson", "second", "instructions", {"evidence": "clearer"}, validate=validate)
    assert result == {"refs": ["asr-1"]}
    assert len(calls) == 3


def test_repairs_can_continue_while_the_validation_problem_set_shrinks(tmp_path):
    calls = []
    class Problems(ValueError):
        def __init__(self, count):
            self.errors = [{"path": f"formulas[{i}]", "code": "invalid", "message": "unbound"}
                           for i in range(count)]
            super().__init__("unresolved formulas")
    def respond(request):
        calls.append(request.tag)
        return {"remaining": 4 - len(calls)}
    def validate(value):
        if value["remaining"]:
            raise Problems(value["remaining"])
    result = session_at(tmp_path, respond).call("lesson", "progress", "instructions", {}, validate=validate)
    assert result["remaining"] == 0
    assert len(calls) == 4, "real progress is not cut off by a monetary or fixed two-attempt budget"


def test_bad_visual_fragment_keeps_good_later_frame_and_original_ocr(tmp_path):
    frames = [{"id": f"frame-{i}", "start": (i-1)*10, "end": i*10, "text": f"OCR {i}",
               "path": f"frame-{i}.jpg"} for i in (1, 2)]
    session = session_at(tmp_path, lambda req: (
        {"frames": [{"id": "invented", "visual_text": "wrong", "explanation": "", "uncertainties": []}]}
        if req.tag.startswith("vision-001") else
        {"frames": [{"id": "frame-2", "visual_text": "valid second slide", "explanation": "explanation", "uncertainties": []}]}))
    for frame in frames:
        (session.directory / frame["path"]).write_bytes(b"fixture-image-" + frame["id"].encode())
    original = deepcopy(frames)
    result, files = session.vision(frames, {"segments": []}, 20)
    assert frames == original
    assert len(result) == 2
    assert result[0]["text"] == "OCR 1" and result[0]["visual_text"] == ""
    assert result[0]["processing_status"] == "deferred"
    assert result[1]["visual_text"] == "valid second slide"
    assert result[0]["uncertainties"]


def test_invalid_lesson_window_does_not_hide_later_success(tmp_path):
    asr = [{"id": "asr-1", "start": 0, "end": 10, "text": "unclear original"},
           {"id": "asr-2", "start": 10, "end": 20, "text": "clear original"}]
    requests = []
    def respond(request):
        requests.append(request)
        if request.tag.startswith("lesson-001"):
            return {"sections": [], "formulas": [], "uncertainties": []}
        return {"sections": [lesson_section("good", 10, 20, "asr-2", "A usable explanation")],
                "formulas": [], "uncertainties": []}
    result, files = session_at(tmp_path, respond).lesson([], {"segments": asr}, 20)
    assert len(result["sections"]) == 2
    assert result["sections"][0]["processing_status"] == "deferred"
    assert result["sections"][0]["evidence_ids"] == ["asr-1"]
    assert result["sections"][1]["markdown"] == "A usable explanation [asr-2]"
    later = next(x for x in requests if x.tag == "lesson-002")
    assert json.loads(later.messages[1]["content"])["previous_ending"] == ""
    assert result["deferred_work"][0]["source_ids"] == ["asr-1"]


def test_valid_neighbor_in_rejected_window_survives_but_unbound_formula_does_not(tmp_path):
    valid = lesson_section("good", 0, 4, "asr-1", "Supported neighboring explanation")
    invalid = lesson_section("bad", 4, 10, "imaginary", "Unsupported claim")
    payload = {"sections": [valid, invalid], "formulas": [{"latex": "x=9", "source_ids": ["imaginary"]}], "uncertainties": []}
    session = session_at(tmp_path, lambda req: payload)
    result, _ = session.lesson([], {"segments": [{"id": "asr-1", "start": 0, "end": 10, "text": "raw"}]}, 10)
    assert any(s["markdown"] == valid["markdown"] for s in result["sections"])
    assert all("Unsupported claim" not in s["markdown"] for s in result["sections"])
    assert result["formulas"] == []
    assert result["deferred_work"]


def test_cancel_is_never_projected_as_a_local_gap(tmp_path):
    from aiforstudy.errors import Cancelled
    def respond(request):
        raise Cancelled("user cancelled")
    session = session_at(tmp_path, respond)
    with pytest.raises(Cancelled):
        session.lesson([], {"segments": [{"id": "asr-1", "start": 0, "end": 10, "text": "raw"}]}, 10)
    assert not (session.directory / "lesson.json").exists()


def test_saved_deferred_response_tamper_is_not_accepted_as_a_gap(tmp_path):
    def validate(value):
        raise ValueError("refs: invalid")
    session = session_at(tmp_path, lambda req: {"refs": ["bad"]})
    with pytest.raises(ValueError):
        session.call("lesson", "bad", "instructions", {}, validate=validate)
    saved = session.directory / "exchanges/bad-repair-1.output.json"
    record = json.loads(saved.read_text()); record["data"] = {"forged": True}; saved.write_text(json.dumps(record))
    next_session = session_at(tmp_path, lambda req: pytest.fail("must not call model"), "new-attempt")
    with pytest.raises(ValueError, match="integrity|完整性|checksum"):
        next_session.call("lesson", "new", "instructions", {}, validate=validate)


def test_valid_uncertainty_is_not_a_reason_to_repeat_identical_work(tmp_path):
    calls = []
    def respond(request):
        calls.append(request.tag)
        return {"uncertainties": ["The source is not clear enough"]}
    a = session_at(tmp_path, respond, "revisions/a")
    b = session_at(tmp_path, respond, "revisions/b")
    first = a.call("lesson", "first", "instructions", {"source": "unchanged"})
    assert b.call("lesson", "renamed", "instructions", {"source": "unchanged"}) == first
    assert calls == ["first"]


def test_global_content_failure_retains_draft_and_independent_review_can_continue(tmp_path):
    from aiforstudy.core.video_lecture_reconciliation import reconcile_lecture, re_review_lecture
    original = {"sections": [lesson_section("original", 0, 10, "asr-1", "Usable draft")],
                "formulas": [], "uncertainties": ["Unclear word [asr-1]"]}
    transcript = {"segments": [{"id": "asr-1", "start": 0, "end": 10, "text": "raw"}]}
    def respond(request):
        if request.role == "lesson":
            return {"sections": [], "formulas": [], "uncertainties": [], "corrections": []}
        return {"decision": "issues_found", "issues": [], "limitations": ["Text evidence only"]}
    session = session_at(tmp_path, respond)
    result = reconcile_lecture(session, original, [], transcript, 10, "global")
    assert result["sections"] == original["sections"]
    assert result["uncertainties"] == original["uncertainties"]
    assert result["reconciliation"]["state"] == "deferred"
    assert result["deferred_work"]
    review = re_review_lecture(session, result, [], transcript, 10, "global")
    assert review["state"] == "completed" and review["automatic_approval"] is False


def test_failed_global_review_is_visible_without_discarding_the_lesson(tmp_path):
    from aiforstudy.core.video_lecture_reconciliation import re_review_lecture
    lesson = {"sections": [lesson_section("original", 0, 10, "asr-1", "Usable draft")], "formulas": [], "uncertainties": []}
    transcript = {"segments": [{"id": "asr-1", "start": 0, "end": 10, "text": "raw"}]}
    before = deepcopy(lesson)
    result = re_review_lecture(session_at(tmp_path, lambda req: {"decision": "invalid"}), lesson, [], transcript, 10, "global")
    assert lesson == before
    assert result["state"] == "deferred" and result["automatic_approval"] is False
    assert result["decision"] != "no_issues_found"
    assert result["deferred_work"][0]["stage"] == "review"


def test_asr_local_failure_returns_partial_and_does_not_fail_the_process(tmp_path, monkeypatch):
    import wave
    from aiforstudy.core import video_recognition_worker as worker
    wav = tmp_path / "input.wav"
    with wave.open(str(wav), "wb") as f:
        f.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        f.writeframes(b'\0\0' * 40 * 16000)
    model = tmp_path / "model"; model.mkdir(); (model / "config.json").write_text('{}')
    (model / "model.safetensors").write_bytes(b'weights')
    calls = []
    def backend(*args):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("one unreadable window")
        return {"text": "Recognized", "generation_tokens": 4}
    monkeypatch.setattr(worker, "_load_asr_backend", lambda model: backend)
    output = tmp_path / "asr.json"
    exit_code = worker.main(['--mode', 'asr', '--input', str(wav), '--output', str(output), '--model', str(model)])
    result = json.loads(output.read_text())
    assert exit_code == 0
    assert result['status'] == 'partial'
    assert [x['status'] for x in result['segments']] == ['completed', 'failed', 'completed']
    assert result['coverage']['complete'] is False


def test_ocr_local_failure_preserves_other_frames(tmp_path, monkeypatch):
    from aiforstudy.core import video_recognition_worker as worker
    executable = tmp_path / 'vision'; executable.write_bytes(b'fixture')
    monkeypatch.setenv('AIFORSTUDY_VIDEO_OCR_EXECUTABLE', str(executable))
    rows = []
    for i in (1, 2):
        p = tmp_path / f'{i}.jpg'; p.write_bytes(b'frame')
        rows.append({'id': f'frame-{i}', 'path': str(p), 'start': i-1, 'end': i})
    manifest = tmp_path / 'frames.json'; manifest.write_text(json.dumps({'frames': rows}))
    def recognize(executable, path):
        if Path(path).name == '1.jpg':
            raise RuntimeError('one frame unavailable')
        return {'success': True, 'observations': [{'text': 'Valid OCR', 'confidence': 1}]}
    monkeypatch.setattr(worker, '_recognize_frame', recognize)
    result = worker.run_ocr(manifest, tmp_path / 'ocr.json')
    assert result['status'] == 'partial'
    assert result['frames'][1]['text'] == 'Valid OCR'
    assert result['coverage']['failed_frames'] == 1


def test_lecture_using_deferred_evidence_is_marked_and_formula_is_not_confirmed(tmp_path):
    frames = [{'id': 'frame-1', 'start': 0, 'end': 10, 'text': '', 'visual_text': '',
               'processing_status': 'deferred', 'uncertainties': ['unreadable'],
               'deferred_work': [{'id': 'gap-1', 'stage': 'vision', 'state': 'deferred', 'source_ids': ['frame-1'], 'start': 0, 'end': 10, 'message': 'unreadable'}]}]
    payload = {'sections': [lesson_section('s', 0, 10, 'frame-1', 'Conclusion depending on missing diagram')],
               'formulas': [{'latex': 'x=9', 'source_ids': ['frame-1'], 'uncertainty': ''}], 'uncertainties': []}
    result, _ = session_at(tmp_path, lambda req: payload).lesson(frames, {'segments': []}, 10)
    assert result['sections'][0]['processing_status'] == 'partial'
    assert result['sections'][0]['deferred_source_ids'] == ['frame-1']
    assert result['formulas'] == []
    assert result['uncertainties']


def test_global_uncertain_formula_does_not_throw_away_valid_rewritten_prose(tmp_path):
    from aiforstudy.core.video_lecture_reconciliation import reconcile_lecture
    original = {'sections': [lesson_section('old', 0, 10, 'asr-1', 'Old prose')], 'formulas': [], 'uncertainties': []}
    answer = {'sections': [lesson_section('new', 0, 10, 'asr-1', 'Improved prose')],
              'formulas': [{'latex': 'x=9', 'source_ids': ['asr-1'], 'uncertainty': 'cannot verify exponent'}],
              'uncertainties': [], 'covered_section_ids': ['old'], 'corrections': []}
    transcript = {'segments': [{'id': 'asr-1', 'start': 0, 'end': 10, 'text': 'source'}]}
    result = reconcile_lecture(session_at(tmp_path, lambda request: answer), original, [], transcript, 10, 'global')
    assert result['sections'][0]['markdown'] == 'Improved prose [asr-1]'
    assert result['formulas'] == []
    assert any('x=9' in x and 'cannot verify exponent' in x for x in result['uncertainties'])
    assert result['reconciliation']['state'] == 'partial'


def test_whole_pipeline_exports_later_content_despite_local_and_review_failures(tmp_path, monkeypatch):
    import io
    from aiforstudy.core.video_analysis import VideoAnalysisService
    from aiforstudy.core import video_analysis_models as models
    from aiforstudy.pipeline.services import CancelToken
    service = VideoAnalysisService(tmp_path)
    service.config.update(vision_batch_size=1, chapter_seconds=10)
    monkeypatch.setattr(service, '_launch', lambda job_id: None)
    source = service.register_upload(io.BytesIO(b'controlled video bytes'), 22, 'sample.mp4')
    row = service.submit({'source_id': source['id']}); job = row['job_id']; directory = service._jobdir(job)
    frames = [{'id': f'frame-{i}', 'path': f'frames/{i}.jpg', 'start': (i-1)*10, 'end': i*10,
               'sampled_time': (i-1)*10, 'sha256': 'fixture'} for i in (1, 2)]
    def extract(job_id, source, duration, token):
        (directory / 'frames').mkdir(exist_ok=True)
        for frame in frames:
            (directory / frame['path']).write_bytes(frame['id'].encode())
        (directory / 'frames-manifest.json').write_text(json.dumps({'frames': frames}))
        return frames, ['frames-manifest.json'] + [f['path'] for f in frames]
    monkeypatch.setattr(service, '_extract_frames', extract)
    service.config.update(asr_python='fixture-python', asr_model='fixture-model', ocr_python='fixture-python')
    def process(args, log, token, timeout=3600):
        if args[0] == 'ffprobe':
            Path(log).write_text(json.dumps({'format': {'duration': '20'}, 'streams': [{'codec_type': 'video'}, {'codec_type': 'audio'}]}))
        elif '--mode' in args:
            output = Path(args[args.index('--output') + 1]); mode = args[args.index('--mode') + 1]
            if mode == 'asr':
                data = {'status': 'partial', 'segments': [
                    {'id': 'asr-1', 'start': 0, 'end': 10, 'status': 'failed', 'text': '', 'quality_warnings': []},
                    {'id': 'asr-2', 'start': 10, 'end': 20, 'status': 'completed', 'text': 'clear raw evidence', 'quality_warnings': []}],
                    'coverage': {'complete': False, 'failed_windows': 1}}
            else:
                data = {'status': 'partial', 'frames': [dict(f, status='failed' if i==0 else 'completed', text='' if i==0 else 'clear OCR') for i,f in enumerate(frames)]}
            output.write_text(json.dumps(data))
        else:
            Path(args[-1]).write_bytes(b'controlled media asset')
    monkeypatch.setattr(service, '_process', process)
    def respond(request):
        if request.tag == 'vision-002':
            return {'frames': [{'id': 'frame-2', 'visual_text': 'clear second frame', 'explanation': '', 'uncertainties': []}]}
        if request.tag == 'lesson-002':
            return {'sections': [lesson_section('good', 10, 20, 'asr-2', 'Later usable content')], 'formulas': [], 'uncertainties': []}
        return {'invalid': True}
    original_session_type = models.VideoModelSession
    def factory(instance, job_id, token, **kwargs):
        s = session_at(directory, respond, '.', session_type=original_session_type); s.service = instance; s.token = token; s.job_id = job_id
        return s
    monkeypatch.setattr(models, 'VideoModelSession', factory)
    service._run(job, CancelToken(), (directory/'worker.lock').open('a+b'))
    result = service.get(job)
    assert result['state'] == 'completed', result.get('error')
    assert any('Later usable content' in s['markdown'] for s in result['result']['sections'])
    assert result['result']['review']['state'] == 'deferred'
    assert result['result']['reconciliation']['state'] == 'deferred'
    assert result['result']['deferred_work']
    assert result['result']['coverage']['audio']['complete'] is False
    assert result['result']['production_accepted'] is False
    assert service.artifact(job, 'lesson.md').is_file()
    assert '局部待核' in service.artifact(job, 'lesson.md').read_text()
    assert '根据完整识别来源统一校订的候选讲义' not in service.artifact(job, 'lesson.md').read_text()


def test_model_cannot_invent_host_processing_state(tmp_path):
    def respond(request):
        return {'frames': [{'id': 'frame-1', 'visual_text': 'readable', 'explanation': '', 'uncertainties': [],
                            'processing_status': 'deferred', 'deferred_work': 'invented-host-data'}]}
    s = session_at(tmp_path, respond); (s.directory/'1.jpg').write_bytes(b'frame')
    frames, _ = s.vision([{'id': 'frame-1', 'path': '1.jpg', 'start': 0, 'end': 10, 'text': 'raw OCR'}], {'segments': []}, 10)
    assert 'processing_status' not in frames[0]
    assert 'deferred_work' not in frames[0]
    assert frames[0]['visual_text'] == 'readable'


def test_model_supplied_gap_metadata_is_not_accepted_as_host_evidence(tmp_path):
    answer = {'sections': [lesson_section('s', 0, 10, 'asr-1', 'Valid text')], 'formulas': [], 'uncertainties': [],
              'deferred_work': [{'id': 'invented-gap', 'source_ids': ['invented']}]}
    result, _ = session_at(tmp_path, lambda req: answer).lesson([], {'segments': [{'id': 'asr-1', 'start': 0, 'end': 10, 'text': 'raw'}]}, 10)
    assert result['deferred_work'] == []


def test_a_worse_repair_does_not_erase_the_previous_valid_neighbor(tmp_path):
    good = lesson_section('good', 0, 10, 'asr-1', 'Useful preserved explanation')
    calls = []
    def respond(request):
        calls.append(request.tag)
        if len(calls) == 1:
            return {'sections': [good], 'formulas': [{'latex': 'bad', 'source_ids': ['invented']}], 'uncertainties': []}
        return {'sections': [], 'formulas': [], 'uncertainties': []}
    result, _ = session_at(tmp_path, respond).lesson([], {'segments': [{'id': 'asr-1', 'start': 0, 'end': 10, 'text': 'raw'}]}, 10)
    assert any(s['markdown'] == good['markdown'] for s in result['sections'])
    assert result['formulas'] == []


def test_revision_retains_successful_edit_when_review_is_deferred(tmp_path, monkeypatch):
    import io
    import time
    from aiforstudy.core.video_analysis import VideoAnalysisService, _sha
    from aiforstudy.core import video_analysis_models as models
    service = VideoAnalysisService(tmp_path); monkeypatch.setattr(service, '_launch', lambda job: None)
    source = service.register_upload(io.BytesIO(b'abc'), 3, 'sample.mp4')
    row = service.submit({'source_id': source['id']}); job = row['job_id']; directory = service._jobdir(job)
    lesson = {'sections': [lesson_section('old', 0, 10, 'asr-1', 'Original explanation')], 'formulas': [], 'uncertainties': []}
    base = {'job_id': job, 'title': 'Revision fixture', 'duration': 10, 'source': {'path': 'source.mp4'}, **lesson,
            'frames': [], 'transcript': {'segments': [{'id': 'asr-1', 'start': 0, 'end': 10, 'text': 'raw'}]},
            'provenance': {}, 'review': {'decision': 'issues_found', 'issues': []}}
    (directory/'result.json').write_text(json.dumps(base)); (directory/'lesson.md').write_text('original export')
    (directory/'export.done.json').write_text(json.dumps({'files': {p: _sha(directory/p) for p in ('result.json','lesson.md')}}))
    service._register_artifacts(job, ['result.json','lesson.md']); service._update(job, state='completed')
    original_bytes = (directory/'result.json').read_bytes()
    rewritten = {**lesson, 'sections': [lesson_section('new',0,10,'asr-1','Improved explanation')],
                 'covered_section_ids': ['old'], 'corrections': []}
    def respond(request):
        return rewritten if request.role == 'lesson' else {'decision': 'invalid'}
    session_type = models.VideoModelSession
    def factory(instance, job_id, token, *, directory):
        s = session_at(service._jobdir(job_id), respond, str(directory.relative_to(service._jobdir(job_id))), session_type=session_type)
        s.service = instance; s.job_id = job_id; s.token = token
        return s
    monkeypatch.setattr(models, 'VideoModelSession', factory)
    service.reconcile(job)
    for _ in range(200):
        row = service.get(job)
        if row['state'] not in {'queued','running','cancelling'}:
            break
        time.sleep(.01)
    assert row['state'] == 'completed', row.get('error')
    assert row['result']['sections'][0]['markdown'] == 'Improved explanation [asr-1]'
    assert row['result']['review']['state'] == 'deferred'
    exported = service.artifact(job, row['result']['exports']['markdown']).read_text()
    assert '局部待核与未完成检查' in exported
    assert (directory/'result.json').read_bytes() == original_bytes
