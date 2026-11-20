"""Validate reader inputs against the run and its trusted preparation root."""
from __future__ import annotations

import json
from pathlib import Path

from ...core.digest import digest_file
from ...core.source_materials import INPUTS
from ...errors import ContractError
from .source_materials import source_asset_paths


def _formatted_materials(snapshot: dict, run: Path) -> dict:
    """Read only the current run's safely located, snapshot-bound format product."""
    relative = 'work/products/source_format/book.json'
    path = run/relative
    try:
        if (not path.resolve().is_relative_to(run) or not path.is_file()
                or any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(run))
                or snapshot.get('source_hashes', {}).get(relative) != digest_file(path)):
            return {}
        product = json.loads(path.read_text(encoding='utf-8'))
        materials = product.get('payload', {}).get('materials', {})
        return materials if isinstance(materials, dict) else {}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _material_sources(snapshot: dict, parse_root: Path, run: Path) -> set[Path]:
    """Only source images actually referenced by immutable reader source blocks."""
    try:
        text = (Path(snapshot['output_dir'])/'data/data.js').read_text(encoding='utf-8')
        data = json.loads(text.split('window.__DATA__ = ', 1)[1].rsplit(';', 1)[0])
        texts = [row.get('raw_markdown', '') for row in data.get('source_materials', {}).values()]
        formatted = _formatted_materials(snapshot, run)
        for material_id, row in data.get('source_materials', {}).items():
            result = formatted.get(material_id)
            markdown = row.get('display_markdown')
            if (isinstance(result, dict) and result.get('status') == 'formatted'
                    and row.get('source_sha256') and result.get('source_sha256') == row['source_sha256']
                    and isinstance(markdown, str) and markdown.strip()
                    and result.get('display_markdown') == markdown
                    and (row.get('formatting') or {}).get('status') == 'formatted'):
                texts.append(markdown)
        texts += [block['raw_text'] for unit in data.get('units', {}).values()
                  for block in (unit.get('reader_document') or {}).get('blocks', [])
                  if block.get('group') == 'exercise_regions']
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ContractError('reader data envelope invalid') from exc
    from ...core.course_library import library_input_files
    try:
        course_inputs = set(library_input_files(parse_root/'course-library'))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ContractError('course library source metadata invalid') from exc
    from ...core.video_evidence import video_input_files
    try:
        video_inputs = set(video_input_files(parse_root, book_id=data['book']))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ContractError('video evidence source metadata invalid') from exc
    return ({parse_root/name for name in (*INPUTS, 'source_materials.json', 'body.json')}
            | set(source_asset_paths(texts, parse_root).values()) | course_inputs | video_inputs)


def validate_snapshot_sources(snapshot: dict, run: Path, parse_root: Path) -> bool:
    """Check source bytes without broadening access to arbitrary absolute paths.

    The caller verifies the immutable snapshot first. ``parse_root`` must come
    from the current StageContext, never from the snapshot or a mutable pointer.
    """
    run = Path(run).resolve()
    parse_root = Path(parse_root).resolve()
    sources = snapshot.get('source_hashes', {})
    allowed = _material_sources(snapshot, parse_root, run) if any(Path(name).is_absolute() for name in sources) else set()
    for name, expected in sources.items():
        reference = Path(name)
        root = parse_root if reference.is_absolute() else run
        path = reference if reference.is_absolute() else run/reference
        if (not path.is_relative_to(root) or not path.resolve().is_relative_to(root)
                or any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(root))
                or (reference.is_absolute() and path not in allowed)):
            raise ContractError('reader source path is invalid')
        actual = digest_file(path) if path.is_file() else 'missing'
        if actual != expected:
            raise ContractError('reader source changed: ' + name)
    return True
