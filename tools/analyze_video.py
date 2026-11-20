#!/usr/bin/env python3
"""Analyze an explicit local video using the same durable service as the workbench."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from aiforstudy.core.video_analysis import VideoAnalysisService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--sample', action='store_true')
    parser.add_argument('--resume')
    parser.add_argument('--reconcile', metavar='JOB_ID')
    parser.add_argument('--retry-revision', metavar='REVISION_ID')
    parser.add_argument('--review-feedback', type=Path, action='append', default=[])
    parser.add_argument('--title')
    parser.add_argument('--course-id', default='video-course')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--capabilities', action='store_true')
    args = parser.parse_args()
    service = VideoAnalysisService(args.workspace)
    if args.capabilities:
        print(json.dumps(service.capabilities(), ensure_ascii=False, indent=2)); return 0
    if args.reconcile:
        feedback = {'documents': [{'filename': path.name, 'text': path.read_text(encoding='utf-8')} for path in args.review_feedback]} if args.review_feedback else None
        job = service.reconcile(args.reconcile, retry_revision_id=args.retry_revision, review_feedback=feedback)
    elif args.resume:
        job = service.resume(args.resume)
    else:
        if not args.source and not args.sample:
            parser.error('provide --source, --sample or --resume')
        source = service.sample() if args.sample else service.import_source(args.source)
        job = service.submit({'source_id': source['id'], 'title': args.title, 'course_id': args.course_id, 'force': args.force})
    job_id = job['job_id']; print(json.dumps({'job_id': job_id}, ensure_ascii=False), flush=True)
    previous = None
    try:
        while True:
            row = service.get(job_id, include_result=False)
            status = (row['state'], row['stage'], round(row['progress'], 3), row.get('error'))
            if status != previous:
                print(json.dumps({'job_id': job_id, 'state': status[0], 'stage': status[1], 'progress': status[2], 'error': status[3]}, ensure_ascii=False), flush=True)
                previous = status
            if row['state'] not in {'queued', 'running', 'cancelling'}:
                if row['state'] == 'completed':
                    print(str(service.artifact(job_id, row.get('active_result', 'result.json'))))
                    return 0
                return 1
            time.sleep(1)
    except KeyboardInterrupt:
        service.cancel(job_id)
        while service.get(job_id, False)['state'] in {'queued', 'running', 'cancelling'}:
            time.sleep(.1)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
