#!/usr/bin/env python3
"""Validate, import, attach, project, or extract local video evidence."""
import argparse
import json
from pathlib import Path
from aiforstudy.core.video_evidence import (validate_video_evidence, import_video_evidence,
    project_video_evidence, attach_video_evidence, extract_video_window)

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('command', choices=('validate', 'import', 'attach', 'project', 'extract'))
p.add_argument('input', type=Path)
p.add_argument('output', type=Path, nargs='?')
p.add_argument('--course-id', help='Expected course ID for project; book ID for attach')
p.add_argument('--unit-id', help='Explicit unit ID for attach')
p.add_argument('--start', type=float, help='Source seconds for extract')
p.add_argument('--end', type=float, help='Source seconds for extract')
a = p.parse_args()
if a.command == 'validate': result = validate_video_evidence(a.input)
else:
    if a.output is None: p.error('output is required')
    if a.command == 'import': result = import_video_evidence(a.input, a.output)
    elif a.command == 'project': result = project_video_evidence(a.input, a.output, course_id=a.course_id)
    elif a.command == 'attach':
        if not a.course_id or not a.unit_id: p.error('attach requires --course-id and --unit-id')
        result = attach_video_evidence(a.input, a.output, book_id=a.course_id, unit_id=a.unit_id)
    else:
        if a.start is None or a.end is None: p.error('extract requires --start and --end')
        result = extract_video_window(a.input, a.output, start=a.start, end=a.end)
print(json.dumps(result, ensure_ascii=False, indent=2))
if result.get('ok') is False: raise SystemExit(1)
