"""Worker evidence, cache identity and failure accounting contracts."""
import importlib
import json
import wave
from pathlib import Path

import pytest


@pytest.fixture
def worker():
    return importlib.import_module("aiforstudy.core.video_recognition_worker")


def audio(path, seconds=60, value=0):
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(value.to_bytes(2, "little", signed=True) * int(seconds * 16000))
    return path


@pytest.fixture
def inputs(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "model.safetensors").write_bytes(b"unit fixture weights")
    (model / "config.json").write_text('{}')
    return audio(tmp_path / "audio.wav"), tmp_path / "asr.json", model


def install_backend(monkeypatch, worker, callback):
    # Only the slow hardware recognizer is replaced; IO, evidence and resume are real.
    monkeypatch.setattr(worker, "_load_asr_backend", lambda model: callback)


def test_windows_cover_tail_without_extra_fully_overlapped_window(worker):
    assert worker.audio_windows(60 * 16000, 16000, 20, 2) == [
        (0, 320000), (288000, 608000), (576000, 896000), (864000, 960000)
    ]
    assert worker.audio_windows(20 * 16000, 16000, 20, 2) == [(0, 320000)]
    with pytest.raises(ValueError):
        worker.audio_windows(16000, 16000, 20, 20)


@pytest.mark.parametrize("text,tokens,warning", [
    ("   ", 0, "empty_text"), ("零" * 100, 103, "repetition_loop"),
    ("这是重复的一句话。" * 16, 180, "repetition_loop"),
    ("被截断的讲述", 2048, "token_limit"),
])
def test_degraded_text_is_flagged_without_rewriting(worker, text, tokens, warning):
    assert warning in worker.quality_warnings(text, tokens, 2048)
    assert worker.quality_warnings("正电荷和负电荷。", 12, 2048) == []


def test_coverage_does_not_count_failed_windows_as_success(worker):
    result = worker.audio_coverage(60, [
        {"start": 0, "end": 20, "status": "completed"},
        {"start": 18, "end": 38, "status": "failed"},
        {"start": 36, "end": 56, "status": "completed"},
    ], 4)
    assert result["covered_seconds"] == 40
    assert result["gaps"] == [[20, 36], [56, 60]]
    assert result["complete"] is False
    assert result["failed_windows"] == 1
    assert result["missing_windows"] == 1


def test_resume_reuses_evidence_but_rejects_changed_audio_or_model(worker, inputs, monkeypatch):
    wav, output, model = inputs
    install_backend(monkeypatch, worker, lambda *args: {"text": "真实接口测试", "generation_tokens": 8})
    first = worker.run_asr(wav, output, model)
    assert first["coverage"]["complete"] is True
    assert [(s["start"], s["end"]) for s in first["segments"]] == [(0, 20), (18, 38), (36, 56), (54, 60)]
    assert first["segments"][0]["raw_output"]["text"] == "真实接口测试"
    monkeypatch.setattr(worker, "_load_asr_backend", lambda model: pytest.fail("completed cache must not reload model"))
    assert worker.run_asr(wav, output, model)["segments"] == first["segments"]
    audio(wav, value=1)
    with pytest.raises(worker.IdentityMismatch):
        worker.run_asr(wav, output, model)
    audio(wav)
    (model / "model.safetensors").write_bytes(b"different weights")
    with pytest.raises(worker.IdentityMismatch):
        worker.run_asr(wav, output, model)
    assert json.loads(output.read_text())["identity"] == first["identity"]


def test_cancel_keeps_finished_window_and_resume_finishes(worker, inputs, monkeypatch):
    wav, output, model = inputs
    calls = []
    def backend(*args):
        calls.append(1)
        if len(calls) == 2:
            raise worker.WorkerCancelled()
        return {"text": "第一窗", "generation_tokens": 3}
    install_backend(monkeypatch, worker, backend)
    with pytest.raises(worker.WorkerCancelled):
        worker.run_asr(wav, output, model)
    partial = json.loads(output.read_text())
    assert partial["status"] == "cancelled"
    assert len(partial["segments"]) == 1
    assert partial["coverage"]["gaps"] == [[20, 60]]
    install_backend(monkeypatch, worker, lambda *args: {"text": "后续窗", "generation_tokens": 4})
    result = worker.run_asr(wav, output, model)
    assert result["coverage"]["complete"] is True
    assert result["segments"][0] == partial["segments"][0]


def test_failed_window_is_kept_visible_and_success_never_claimed(worker, inputs, monkeypatch):
    wav, output, model = inputs
    def backend(*args):
        raise RuntimeError("recognizer unavailable")
    install_backend(monkeypatch, worker, backend)
    result = worker.run_asr(wav, output, model)
    assert result["status"] == "failed"
    assert len(result["segments"]) == 4
    assert result["coverage"]["covered_seconds"] == 0
    assert result["coverage"]["failed_windows"] == 4


def test_truncated_window_preserves_text_and_marks_quality(worker, inputs, monkeypatch):
    wav, output, model = inputs
    install_backend(monkeypatch, worker, lambda *args: {"text": "零" * 100, "generation_tokens": 2048})
    result = worker.run_asr(wav, output, model)
    assert result["status"] == "completed_with_warnings"
    assert result["coverage"]["complete"] is True
    assert result["coverage"]["quality_warning_windows"] == 4
    assert result["segments"][0]["text"] == "零" * 100


def test_ocr_preserves_source_ids_boxes_and_invalidates_image_cache(worker, tmp_path, monkeypatch):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"fixture image")
    manifest = tmp_path / "frames.json"
    manifest.write_text(json.dumps({"frames": [{"id": "slide/first", "path": "frame.png", "start": 12, "end": 18}]}))
    executable = tmp_path / "vision"
    executable.write_bytes(b"fixture executable")
    monkeypatch.setenv("AIFORSTUDY_VIDEO_OCR_EXECUTABLE", str(executable))
    observation = {"text": "F = kqQ/r2", "confidence": .51, "bbox_top_left_normalized": [.1, .2, .3, .4]}
    monkeypatch.setattr(worker, "_recognize_frame", lambda *args: {"success": True, "observations": [observation], "revision": 3})
    output = tmp_path / "ocr.json"
    result = worker.run_ocr(manifest, output)
    actual = result["frames"][0]
    assert actual["id"] == "slide/first"
    assert actual["start"] == 12
    assert actual["observations"] == [observation]
    assert actual["text"] == "F = kqQ/r2"
    assert actual["formula_status"] == "unverified_candidate"
    frame.write_bytes(b"changed image")
    with pytest.raises(worker.IdentityMismatch):
        worker.run_ocr(manifest, output)


def test_cancel_file_stops_asr_after_completed_evidence_and_can_resume(worker, inputs, tmp_path, monkeypatch):
    wav, output, model = inputs
    cancel = tmp_path / 'cancel.request'
    def backend(*args):
        cancel.touch()
        return {'text': '已完成窗口', 'generation_tokens': 4}
    install_backend(monkeypatch, worker, backend)
    with pytest.raises(worker.WorkerCancelled):
        worker.run_asr(wav, output, model, cancel_file=cancel)
    prior=json.loads(output.read_text());assert prior['status']=='cancelled';assert len(prior['segments'])==1
    cancel.unlink();install_backend(monkeypatch, worker, lambda *args:{'text':'继续识别','generation_tokens':3})
    resumed=worker.run_asr(wav, output, model, cancel_file=cancel)
    assert resumed['coverage']['complete'];assert resumed['segments'][0]==prior['segments'][0]


def test_cancel_file_stops_ocr_between_frames_without_restarting_finished_work(worker, tmp_path, monkeypatch):
    frame=tmp_path/'frame.png';frame.write_bytes(b'image fixture');executable=tmp_path/'vision';executable.write_bytes(b'runner')
    monkeypatch.setenv('AIFORSTUDY_VIDEO_OCR_EXECUTABLE',str(executable))
    manifest=tmp_path/'frames.json';manifest.write_text(json.dumps({'frames':[{'id':str(i),'path':'frame.png','start':i,'end':i+1} for i in range(3)]}))
    cancel=tmp_path/'cancel.request';output=tmp_path/'ocr.json'
    def recognize(*args):
        cancel.touch();return {'success':True,'observations':[{'text':'原文','confidence':1}]}
    monkeypatch.setattr(worker,'_recognize_frame',recognize)
    with pytest.raises(worker.WorkerCancelled):worker.run_ocr(manifest,output,cancel_file=cancel)
    prior=json.loads(output.read_text());assert prior['status']=='cancelled';assert len(prior['frames'])==1
    cancel.unlink();monkeypatch.setattr(worker,'_recognize_frame',lambda *args:{'success':True,'observations':[{'text':'继续','confidence':1}]})
    resumed=worker.run_ocr(manifest,output,cancel_file=cancel)
    assert len(resumed['frames'])==3;assert resumed['frames'][0]==prior['frames'][0]
