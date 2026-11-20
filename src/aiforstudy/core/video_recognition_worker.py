"""Offline video recognition subprocess with identity-bound, atomic evidence.

Run with a Python environment containing mlx-audio for ASR. OCR uses macOS
Vision through a tiny locally compiled runner (or an explicit executable).
Importing this module requires only the standard library. Time values denote
input windows, never word alignment; overlapping text is intentionally retained.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.metadata
import itertools
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import wave


class IdentityMismatch(ValueError):
    """A checkpoint belongs to different content, configuration or software."""


class WorkerCancelled(BaseException):
    """Cancellation must escape recognizer error handling."""


def _check_cancel_file(path):
    if path is not None and Path(path).exists():
        raise WorkerCancelled()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _runtime(mode):
    versions = {}
    for package in (["mlx", "mlx-audio", "numpy", "transformers", "tokenizers"] if mode == "asr" else []):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not_installed"
    return {"python": platform.python_version(), "executable": sys.executable,
            "platform": platform.platform(), "machine": platform.machine(), "packages": versions}


def _identity(mode, input_path, config, model):
    body = {"schema_version": 1, "mode": mode,
            "worker_sha256": file_sha256(__file__), "input_sha256": file_sha256(input_path),
            "config": config, "model": model, "runtime": _runtime(mode)}
    return {**body, "sha256": _digest(body)}


def _model_identity(model):
    model = Path(model).resolve(strict=True)
    if not model.is_dir():
        raise ValueError("--model must be a local model directory")
    relevant = {".safetensors", ".json", ".txt", ".model"}
    files = [{"path": str(path.relative_to(model)), "sha256": file_sha256(path),
              "bytes": path.stat().st_size}
             for path in sorted(model.rglob("*"))
             if path.is_file() and path.suffix in relevant and not any(
                 part.startswith(".") for part in path.relative_to(model).parts)]
    if not any(item["path"].endswith(".safetensors") for item in files):
        raise ValueError("local ASR model has no safetensors weights")
    return {"engine": "mlx-audio Qwen3-ASR", "path": str(model),
            "files": files, "sha256": _digest(files)}


def _checkpoint_dir(output):
    return Path(output).with_name(Path(output).name + ".checkpoints")


def _prepare(output, identity):
    directory = _checkpoint_dir(output)
    marker = directory / "identity.json"
    for existing in (marker, Path(output)):
        if existing.exists():
            old = json.loads(existing.read_text(encoding="utf-8"))
            old = old if existing == marker else old.get("identity")
            if old != identity:
                raise IdentityMismatch("content/configuration/runtime changed; use a new output path")
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_json(marker, identity)
    return directory


def _checkpoint_path(directory, identifier):
    return directory / ("item-" + _digest(identifier) + ".json")


def _cached(directory, identity, expected):
    path = _checkpoint_path(directory, expected["id"])
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    result = record.get("result", {})
    if (record.get("identity_sha256") != identity["sha256"] or
            record.get("result_sha256") != _digest(result) or
            any(result.get(key) != value for key, value in expected.items())):
        raise IdentityMismatch("window checkpoint identity or evidence checksum mismatch")
    return result if result.get("status") == "completed" else None


def _save_item(directory, identity, result):
    record = {"identity_sha256": identity["sha256"], "result_sha256": _digest(result), "result": result}
    _atomic_json(_checkpoint_path(directory, result["id"]), record)
    if result["status"] == "failed":
        _atomic_json(directory / ("failed-" + str(time.time_ns()) + ".json"), record)


def audio_windows(samples, sample_rate, chunk_seconds=20, overlap_seconds=2):
    if not (math.isfinite(chunk_seconds) and math.isfinite(overlap_seconds) and
            0 <= overlap_seconds < chunk_seconds and samples > 0 and sample_rate > 0):
        raise ValueError("require nonempty audio and 0 <= overlap < chunk")
    chunk = round(chunk_seconds * sample_rate)
    step = round((chunk_seconds - overlap_seconds) * sample_rate)
    if chunk < 1 or step < 1:
        raise ValueError("window and stride must be at least one sample")
    windows = []
    for start in range(0, samples, step):
        end = min(samples, start + chunk)
        windows.append((start, end))
        if end == samples:
            break
    return windows


def quality_warnings(text, generation_tokens=None, max_tokens=2048):
    warnings = []
    if not text.strip():
        warnings.append("empty_text")
    compact = re.sub(r"\s+", "", text)
    longest = max((sum(1 for _ in group) for _, group in itertools.groupby(compact)), default=0)
    if longest >= 32 or re.search(r"(.{2,80}?)\1{7,}", compact):
        warnings.append("repetition_loop")
    if generation_tokens is not None and generation_tokens >= max_tokens:
        warnings.append("token_limit")
    return warnings


def audio_coverage(duration, segments, expected_windows):
    successful = [s for s in segments if s.get("status") == "completed"]
    merged = []
    for segment in sorted(successful, key=lambda s: s["start"]):
        start, end = max(0, segment["start"]), min(duration, segment["end"])
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        elif end > start:
            merged.append([start, end])
    gaps, cursor = [], 0
    for start, end in merged:
        if start > cursor:
            gaps.append([cursor, start])
        cursor = end
    if cursor < duration:
        gaps.append([cursor, duration])
    return {"duration_seconds": duration, "covered_seconds": sum(b - a for a, b in merged),
            "intervals": merged, "gaps": gaps, "expected_windows": expected_windows,
            "completed_windows": len(successful),
            "failed_windows": sum(s.get("status") == "failed" for s in segments),
            "missing_windows": max(0, expected_windows - len(segments)),
            "quality_warning_windows": sum(bool(s.get("quality_warnings")) for s in successful),
            "complete": not gaps and len(successful) == expected_windows,
            "meaning": "successful window processing; not verified semantic completeness"}


def _load_asr_backend(model_path):
    # Never resolve a remote model or read stored Hugging Face credentials.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    import mlx.core as mx
    import numpy as np
    from mlx_audio.stt.utils import load_model
    model = load_model(Path(model_path), strict=True)

    def recognize(pcm, chunk_seconds, max_tokens):
        waveform = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        try:
            result = model.generate(audio=mx.array(waveform), language="Chinese",
                                    max_tokens=max_tokens, chunk_duration=chunk_seconds, verbose=False)
            return dataclasses.asdict(result)
        finally:
            mx.clear_cache()
    return recognize


def _status(items):
    if any(item["status"] == "failed" for item in items):
        return "partial" if any(item["status"] == "completed" for item in items) else "failed"
    return "completed_with_warnings" if any(item.get("quality_warnings") for item in items) else "completed"


def run_asr(input_path, output_path, model_path, chunk_seconds=20, overlap_seconds=2, max_tokens=2048, *, cancel_file=None):
    _check_cancel_file(cancel_file)
    input_path, output_path = Path(input_path), Path(output_path)
    with wave.open(str(input_path), "rb") as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getcomptype()) != (16000, 1, 2, "NONE"):
            raise ValueError("ASR requires 16000 Hz mono signed 16-bit PCM WAV")
        samples, rate = audio.getnframes(), audio.getframerate()
        windows = audio_windows(samples, rate, chunk_seconds, overlap_seconds)
        config = {"chunk_seconds": chunk_seconds, "overlap_seconds": overlap_seconds,
                  "max_tokens": max_tokens, "language": "Chinese", "sample_rate": rate,
                  "samples": samples, "timestamps": "input_window_bounds_not_word_alignment"}
        identity = _identity("asr", input_path, config, _model_identity(model_path))
        directory = _prepare(output_path, identity)
        document = {"identity": identity, "status": "running", "segments": [],
                    "timestamps": config["timestamps"], "overlap_policy": "raw_windows_retained_without_deduplication"}

        def save():
            document["coverage"] = audio_coverage(samples / rate, document["segments"], len(windows))
            _atomic_json(output_path, document)

        backend = None
        save()
        try:
            for begin, end in windows:
                _check_cancel_file(cancel_file)
                expected = {"id": f"asr-{begin:012d}-{end:012d}", "start": begin / rate, "end": end / rate}
                cached = _cached(directory, identity, expected)
                if cached is not None:
                    document["segments"].append(cached)
                    save()
                    continue
                if backend is None:
                    backend = _load_asr_backend(model_path)
                started = time.monotonic()
                item = {**expected, "status": "failed", "text": "", "quality_warnings": []}
                # Source integrity is a global contract, not a recognizer's local doubt.
                audio.setpos(begin)
                pcm = audio.readframes(end - begin)
                if len(pcm) != (end - begin) * 2:
                    raise ValueError("truncated WAV data")
                try:
                    raw = backend(pcm, chunk_seconds, max_tokens)
                    if not isinstance(raw.get("text"), str):
                        raise ValueError("ASR output has no text string")
                    item.update(status="completed", text=raw["text"], raw_output=raw,
                                generation_tokens=raw.get("generation_tokens"), max_tokens=max_tokens,
                                quality_warnings=quality_warnings(raw["text"], raw.get("generation_tokens"), max_tokens))
                except (OSError, MemoryError, IdentityMismatch):
                    raise
                except Exception as error:
                    item["error"] = f"{type(error).__name__}: {error}"
                item["elapsed_seconds"] = time.monotonic() - started
                item["identity_sha256"] = identity["sha256"]
                _save_item(directory, identity, item)
                document["segments"].append(item)
                save()
                print(json.dumps({"mode": "asr", "completed_windows": len(document["segments"]),
                                  "total_windows": len(windows), "end": end / rate, "status": item["status"]}), flush=True)
                _check_cancel_file(cancel_file)
            document["status"] = _status(document["segments"])
        except (WorkerCancelled, KeyboardInterrupt):
            document["status"] = "cancelled"
            raise
        except Exception as error:
            document.update(status="failed", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            save()
    return document


_VISION_SOURCE = r'''
#import <Foundation/Foundation.h>
#import <Vision/Vision.h>
int main(int argc, const char * argv[]) {
 @autoreleasepool {
  if (argc != 2) return 2;
  NSString *path = [NSString stringWithUTF8String:argv[1]];
  VNRecognizeTextRequest *request = [[VNRecognizeTextRequest alloc] init];
  request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
  request.recognitionLanguages = @[@"zh-Hans", @"en-US"];
  request.usesLanguageCorrection = NO;
  VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithURL:[NSURL fileURLWithPath:path] options:@{}];
  NSError *error = nil;
  BOOL success = [handler performRequests:@[request] error:&error];
  NSMutableArray *items = [NSMutableArray array];
  if (success) for (VNRecognizedTextObservation *obs in request.results) {
   VNRecognizedText *candidate = [[obs topCandidates:1] firstObject];
   if (!candidate) continue;
   CGRect b = obs.boundingBox;
   [items addObject:@{@"text":candidate.string, @"confidence":@(candidate.confidence),
     @"bbox_top_left_normalized":@[@(b.origin.x),@(1-b.origin.y-b.size.height),@(b.size.width),@(b.size.height)]}];
  }
  NSDictionary *result = @{@"engine":@"Apple Vision VNRecognizeTextRequest", @"revision":@(request.revision),
    @"observations":items, @"success":@(success), @"error":error.localizedDescription ?: @""};
  NSData *data = [NSJSONSerialization dataWithJSONObject:result options:NSJSONWritingSortedKeys error:nil];
  puts([[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding].UTF8String);
  return success ? 0 : 1;
 }
}
'''


def _subprocess(arguments, timeout=120):
    process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        if process.returncode:
            raise RuntimeError(f"process exited {process.returncode}: {stderr.strip()} {stdout.strip()}")
        return stdout
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def _vision_executable(output):
    explicit = os.environ.get("AIFORSTUDY_VIDEO_OCR_EXECUTABLE")
    if explicit:
        return Path(explicit).resolve(strict=True)
    if sys.platform != "darwin":
        raise RuntimeError("Apple Vision OCR requires macOS")
    clang = shutil.which("clang")
    if not clang:
        raise RuntimeError("Apple Vision OCR requires clang (Xcode command line tools)")
    directory = _checkpoint_dir(output)
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / ("apple-vision-" + hashlib.sha256(_VISION_SOURCE.encode()).hexdigest()[:16])
    if not executable.exists():
        source = executable.with_suffix(".m")
        source.write_text(_VISION_SOURCE, encoding="utf-8")
        temporary = str(executable) + f".{os.getpid()}.tmp"
        try:
            _subprocess([clang, "-O2", "-fobjc-arc", "-framework", "Foundation", "-framework", "Vision", str(source), "-o", temporary])
            os.replace(temporary, executable)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return executable


def _recognize_frame(executable, path):
    return json.loads(_subprocess([str(executable), str(path)]))


def run_ocr(input_path, output_path, *, cancel_file=None):
    _check_cancel_file(cancel_file)
    input_path, output_path = Path(input_path), Path(output_path)
    frames = json.loads(input_path.read_text(encoding="utf-8")).get("frames")
    if not isinstance(frames, list) or not frames:
        raise ValueError("OCR manifest must contain a nonempty frames list")
    seen, sources = set(), []
    for frame in frames:
        identifier, start, end = frame["id"], frame["start"], frame["end"]
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("frame IDs must be unique nonempty strings")
        if not all(isinstance(t, (int, float)) and math.isfinite(t) for t in (start, end)) or not 0 <= start <= end:
            raise ValueError("invalid frame time bounds")
        seen.add(identifier)
        path = Path(frame["path"])
        path = (input_path.parent / path).resolve(strict=True) if not path.is_absolute() else path.resolve(strict=True)
        sources.append({"id": identifier, "start": start, "end": end, "path": str(path), "sha256": file_sha256(path)})
    executable = _vision_executable(output_path)
    model = {"engine": "Apple Vision VNRecognizeTextRequest", "executable_sha256": file_sha256(executable)}
    config = {"frames": sources, "languages": ["zh-Hans", "en-US"], "level": "accurate", "language_correction": False}
    identity = _identity("ocr", input_path, config, model)
    directory = _prepare(output_path, identity)
    document = {"identity": identity, "frames": [], "status": "running",
                "formula_note": "OCR text is an unverified candidate; no mathematical repair or inference applied"}

    def save():
        items = document["frames"]
        document["coverage"] = {"expected_frames": len(frames), "completed_frames": sum(s["status"] == "completed" for s in items),
                                "failed_frames": sum(s["status"] == "failed" for s in items),
                                "meaning": "sampled images only; not every video frame"}
        _atomic_json(output_path, document)

    save()
    try:
        for source in sources:
            _check_cancel_file(cancel_file)
            if file_sha256(source['path']) != source['sha256']:
                raise IdentityMismatch('source frame integrity changed during OCR')
            expected = {key: source[key] for key in ("id", "start", "end")}
            cached = _cached(directory, identity, expected)
            if cached is not None:
                document["frames"].append(cached)
                save()
                continue
            started = time.monotonic()
            item = {**expected, "path": source["path"], "image_sha256": source["sha256"],
                    "status": "failed", "text": "", "observations": [], "quality_warnings": [],
                    "formula_status": "unverified_candidate", "identity_sha256": identity["sha256"]}
            try:
                raw = _recognize_frame(executable, source["path"])
                item["raw_output"] = raw
                if raw.get("success") is False or raw.get("error"):
                    raise RuntimeError(raw.get("error") or "Vision recognition failed")
                observations = raw["observations"]
                item.update(status="completed", observations=observations,
                            text="\n".join(row["text"] for row in observations))
                if not item["text"].strip():
                    item["quality_warnings"].append("empty_text")
                if any(row.get("confidence", 0) < 0.6 for row in observations):
                    item["quality_warnings"].append("low_confidence_text")
            except (OSError, MemoryError, IdentityMismatch):
                raise
            except Exception as error:
                item.update(status="failed", error=f"{type(error).__name__}: {error}")
            item["elapsed_seconds"] = time.monotonic() - started
            _save_item(directory, identity, item)
            document["frames"].append(item)
            save()
            print(json.dumps({"mode": "ocr", "completed_frames": len(document["frames"]), "total_frames": len(frames), "id": source["id"]}), flush=True)
            _check_cancel_file(cancel_file)
        document["status"] = _status(document["frames"])
    except (WorkerCancelled, KeyboardInterrupt):
        document["status"] = "cancelled"
        raise
    except Exception as error:
        document.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        save()
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["asr", "ocr"], required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=os.environ.get("AIFORSTUDY_VIDEO_ASR_MODEL"))
    parser.add_argument("--chunk-seconds", type=float, default=20)
    parser.add_argument("--overlap-seconds", type=float, default=2)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--cancel-file", type=Path, help="Cooperative cancellation marker; checked between recognition items")
    args = parser.parse_args(argv)
    if args.mode == "asr" and not args.model:
        parser.error("ASR requires --model or AIFORSTUDY_VIDEO_ASR_MODEL")
    if args.max_tokens < 1:
        parser.error("--max-tokens must be positive")

    def cancel(signum, frame):
        raise WorkerCancelled()
    previous = signal.signal(signal.SIGTERM, cancel)
    try:
        result = (run_asr(args.input, args.output, args.model, args.chunk_seconds, args.overlap_seconds, args.max_tokens, cancel_file=args.cancel_file)
                  if args.mode == "asr" else run_ocr(args.input, args.output, cancel_file=args.cancel_file))
        print(json.dumps({"status": result["status"], "output": str(args.output)}), flush=True)
        return 0 if result["status"] in ("completed", "completed_with_warnings", "partial") else 1
    except (WorkerCancelled, KeyboardInterrupt):
        return 130
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
