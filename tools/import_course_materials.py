"""Validate and import local course PDFs and their original manifests."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from aiforstudy.core.course_library import import_materials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--materials', required=True, type=Path,
                        help='Directory containing ustc-exams/manifest.json and textbook/homework-index.json')
    parser.add_argument('--index', type=Path, help='Optional existing normalized index to verify against both manifests')
    parser.add_argument('--output', required=True, type=Path, help='Portable bundle directory, normally <parse-root>/course-library')
    args = parser.parse_args()
    try:
        report = import_materials(args.materials, args.output, index_path=args.index)
    except (ValueError, OSError) as exc:
        parser.exit(1, 'Course material import failed: ' + str(exc) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
