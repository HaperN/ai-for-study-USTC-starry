from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from .path_safety import assert_no_link_components, is_link_like
from .unified_beginner_auditor import DiagramObservation, DiagramObserver


_OBSERVATION_SCHEMA = "hbep-diagram-observation-v1"
_MAX_OUTPUT_BYTES = 1024 * 1024


def build_subprocess_diagram_observer(
    program: str | Path,
    *,
    work_root: str | Path,
    timeout_seconds: int = 120,
) -> DiagramObserver:
    program_path = assert_no_link_components(
        program, label="diagram observer program"
    ).resolve()
    if is_link_like(program_path) or not program_path.is_file():
        raise ValueError("diagram observer program must be a regular file")
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 600
    ):
        raise ValueError("diagram observer timeout must be between 1 and 600 seconds")
    root = assert_no_link_components(
        work_root, label="diagram observer work root"
    ).resolve()

    def observe(image_path: str, image_bytes: bytes) -> DiagramObservation:
        if not isinstance(image_path, str) or not image_path:
            raise ValueError("image_path must be non-empty")
        if not isinstance(image_bytes, bytes) or not image_bytes:
            raise ValueError("image_bytes must be non-empty")
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="diagram-observer-", dir=root) as temporary:
            frozen_image = Path(temporary) / "image.bin"
            with frozen_image.open("xb") as handle:
                handle.write(image_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            command = (
                [sys.executable, str(program_path)]
                if program_path.suffix.casefold() == ".py"
                else [str(program_path)]
            )
            completed = subprocess.run(
                [
                    *command,
                    "--image",
                    str(frozen_image),
                    "--logical-path",
                    image_path,
                ],
                cwd=temporary,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
        if completed.returncode != 0:
            raise RuntimeError("diagram observer process failed")
        if not completed.stdout or len(completed.stdout) > _MAX_OUTPUT_BYTES:
            raise ValueError("diagram observer output is unavailable or too large")
        try:
            value = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("diagram observer output is not canonical JSON data") from exc
        return _parse_observation(value)

    return observe


def _parse_observation(value: object) -> DiagramObservation:
    expected = {
        "schema_version",
        "observer_id",
        "image_sha256",
        "ocr_labels",
        "relations",
        "complete",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("diagram observer output has unknown or missing fields")
    labels = value.get("ocr_labels")
    relations = value.get("relations")
    if not isinstance(labels, list) or not isinstance(relations, list):
        raise ValueError("diagram observer labels and relations must be arrays")
    if value.get("schema_version") != _OBSERVATION_SCHEMA:
        raise ValueError("diagram observer output has the wrong schema")
    return DiagramObservation(
        observer_id=_required_string(value.get("observer_id"), "observer_id"),
        image_sha256=_required_string(value.get("image_sha256"), "image_sha256"),
        ocr_labels=tuple(_required_string(item, "ocr_label") for item in labels),
        relations=tuple(_required_string(item, "relation") for item in relations),
        complete=value.get("complete") is True,
    )


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"diagram observer {field} must be a non-empty string")
    return value


__all__ = ["build_subprocess_diagram_observer"]
