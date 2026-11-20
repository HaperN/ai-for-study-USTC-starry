#!/usr/bin/env python3
"""Generate this sample's authored Chinese narration with the existing Edge backend.

Run from any directory with the project's Python environment:
    ../../..//.venv-linux/bin/python generate_voice.py
    python generate_voice.py --verify-only

Only ``text`` is sent to synthesis. Display Markdown/TeX is never converted into
speech. Existing WAVs are reused only when text, source configuration, file hash,
and freshly decoded waveform checks all match. Failures leave a partial manifest
and an explicit report; the lesson and animation remain usable without audio.
"""
from __future__ import annotations

import argparse
from array import array
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import wave


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aiforstudy.blocks.media.narration import (  # noqa: E402
    EdgeNeuralBackend,
    SAMPLE_RATE,
    resolve_ffmpeg,
)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def save_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def utterances(lesson: dict) -> list[dict]:
    """Extract authored text verbatim and reject speech containing formula markup."""
    groups = [(section["id"], section.get("narration", [])) for section in lesson["sections"]]
    groups.append(("conclusion", lesson.get("conclusion_narration", [])))
    result, seen = [], set()
    for section_id, entries in groups:
        for entry in entries:
            identifier, text = entry.get("id"), entry.get("text")
            if not isinstance(identifier, str) or not identifier or identifier in seen:
                raise ValueError(f"Narration id is empty or duplicated: {identifier!r}")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"Narration {identifier} has no authored spoken text")
            if re.search(r"[\\${}^_`]", text):
                raise ValueError(f"Narration {identifier} contains formula/Markdown markup; author must fix text")
            seen.add(identifier)
            result.append({"id": identifier, "text": text, "section_id": section_id,
                           "phase": entry.get("phase", "explain")})
    if not result:
        raise ValueError("Lesson has no authored narration")
    return result


def verify_wav(path: Path) -> dict:
    """Measure the complete decoded PCM, rejecting empty or silent audio."""
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, SAMPLE_RATE, "NONE"):
            raise ValueError("Expected mono 16-bit PCM WAV at 24000 Hz")
        sample_count = wav.getnframes()
        pcm = wav.readframes(sample_count)
        if len(pcm) != sample_count * 2:
            raise ValueError("Truncated WAV data")
    if not sample_count:
        raise ValueError("Empty waveform")
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max(abs(value) for value in samples) / 32768
    rms = math.sqrt(sum(value * value for value in samples) / sample_count) / 32768
    active_fraction = sum(abs(value) >= 128 for value in samples) / sample_count
    duration = sample_count / SAMPLE_RATE
    if duration < 0.1 or peak < 0.005 or rms < 0.0005 or active_fraction < 0.001:
        raise ValueError(f"Silent or implausible waveform: duration={duration}, rms={rms}, peak={peak}")
    return {"duration": duration, "sample_rate": SAMPLE_RATE, "sample_count": sample_count,
            "channels": 1, "sample_width": 2, "peak": peak, "rms": rms,
            "active_fraction": active_fraction, "non_silent": True,
            "sha256": digest(path.read_bytes())}


def audio_filename(identifier: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", identifier).strip("-")[:90]
    # Hashing also prevents two differently punctuated ids mapping to one path.
    return f"{slug or 'narration'}-{digest(identifier.encode())[:8]}.wav"


def generate(lesson_path: Path, *, verify_only: bool = False, force: bool = False) -> int:
    output = HERE / "audio"
    manifest_path, report_path = HERE / "audio-manifest.json", HERE / "narration-report.json"
    lesson_bytes = lesson_path.read_bytes()
    entries = utterances(json.loads(lesson_bytes))
    config = json.loads((ROOT / "config/narration.json").read_text(encoding="utf-8"))
    expected = {"backend": "edge-tts", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-5%", "pitch": "+0Hz"}
    if any(config.get(key) != value for key, value in expected.items()):
        raise ValueError("This sample requires the configured Edge Xiaoxiao Neural voice at -5%; no fallback is permitted")
    backend = EdgeNeuralBackend(voice=expected["voice"], rate=expected["rate"], pitch=expected["pitch"])
    source = backend.source
    previous = {}
    if manifest_path.exists():
        previous = {record["id"]: record for record in json.loads(manifest_path.read_text(encoding="utf-8"))}
    output.mkdir(parents=True, exist_ok=True)
    records, events = [], []
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "lesson": lesson_path.name,
              "lesson_sha256": digest(lesson_bytes), "source": source, "verify_only": verify_only,
              "spoken_text_policy": "Only authored narration[].text; never display_markdown or formula conversion",
              "verification": "Complete decoded PCM duration plus RMS, peak and active sample fraction; no speech recognition",
              "status": "running", "expected_count": len(entries), "events": events}

    def checkpoint(status: str) -> None:
        report.update(status=status, verified_count=len(records),
                      generated_count=sum(event["status"] == "generated" for event in events),
                      reused_count=sum(event["status"] == "reused" for event in events),
                      total_duration=sum(record["duration"] for record in records),
                      missing_ids=[entry["id"] for entry in entries if entry["id"] not in {r["id"] for r in records}])
        save_json(manifest_path, records)
        save_json(report_path, report)

    ffmpeg = None if verify_only else resolve_ffmpeg()
    for entry in entries:
        filename = audio_filename(entry["id"])
        path = output / filename
        signature = digest(json.dumps({"text": entry["text"], "source": source}, sort_keys=True, ensure_ascii=False).encode())
        cached = previous.get(entry["id"], {})
        statistics = None
        cache_problem = "no matching previously verified audio"
        if not force and cached.get("synthesis_sha256") == signature and path.is_file():
            try:
                statistics = verify_wav(path)
                if statistics["sha256"] != cached.get("sha256"):
                    raise ValueError("Cached audio hash changed")
            except Exception as exc:
                statistics = None
                cache_problem = f"{type(exc).__name__}: {exc}"
        stage = "verify" if verify_only else "synthesis"
        try:
            if statistics is None:
                if verify_only:
                    raise ValueError(f"Cannot verify {entry['id']}: {cache_problem}")
                with tempfile.TemporaryDirectory(prefix=".voice-", dir=output) as scratch:
                    raw, decoded = Path(scratch) / "edge.mp3", Path(scratch) / "decoded.wav"
                    backend.synthesize(entry["text"], raw)
                    stage = "decode"
                    process = subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(raw),
                                              "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(decoded)],
                                             capture_output=True, timeout=180)
                    if process.returncode:
                        raise RuntimeError(process.stderr.decode("utf-8", "replace")[-1800:])
                    stage = "verify"
                    statistics = verify_wav(decoded)
                    decoded.replace(path)
                status = "generated"
            else:
                status = "reused"
            record = {**entry, "file": "audio/" + filename, **statistics, "source": source,
                      "synthesis_sha256": signature}
            records.append(record)
            events.append({"id": entry["id"], "status": status, "duration": statistics["duration"]})
            checkpoint("running")
            print(f"{status}: {entry['id']} {statistics['duration']:.3f}s", flush=True)
        except Exception as exc:
            events.append({"id": entry["id"], "status": "failed", "stage": stage,
                           "error_type": type(exc).__name__, "error": str(exc)[-1800:],
                           "note": "No system-TTS fallback. Retry this command when the service/network is available."})
            checkpoint("failed")
            print(f"FAILED [{stage}] {entry['id']}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            return 1
    checkpoint("complete")
    print(f"Verified {len(records)} WAVs, {report['total_duration']:.3f}s decoded PCM total.", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lesson", type=Path, default=HERE / "lesson.json")
    parser.add_argument("--verify-only", action="store_true", help="Check cached hashes and decode evidence without network access")
    parser.add_argument("--force", action="store_true", help="Regenerate all narration with the same authorized backend")
    arguments = parser.parse_args()
    if arguments.verify_only and arguments.force:
        parser.error("--verify-only and --force cannot be combined")
    try:
        return generate(arguments.lesson, verify_only=arguments.verify_only, force=arguments.force)
    except Exception as exc:
        # This includes missing lesson/config/decoder dependencies, before a loop begins.
        save_json(HERE / "narration-report.json", {"status": "failed", "stage": "preflight",
                  "generated_at": datetime.now(timezone.utc).isoformat(),
                  "error_type": type(exc).__name__, "error": str(exc)[-1800:],
                  "note": "No alternate voice or synthetic placeholder was produced."})
        print(f"FAILED [preflight]: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
