"""Rebuild from a moved delivery folder without the original computer's paths."""
from pathlib import Path
import argparse
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.build_practice_delivery import build, read, study_profile
from aiforstudy.practice.catalog import select_edition, validate_review_files


def _source_media_questions(delivery):
    """Recover the reviewed teacher questions actually selected by the saved export."""
    additions = delivery / 'teacher-additions.json'
    paper_catalog = delivery / 'paper-catalog.json'
    views = read(paper_catalog) if paper_catalog.is_file() else []
    if views:
        validate_review_files(views, delivery)
    if not additions.is_file():
        return views
    extra = read(additions)
    if extra:
        validate_review_files(extra, delivery)
    selected = select_edition(read(delivery / 'catalog.json'), 'custom',
                              study_profile(read(delivery / 'teacher-profile.json'), {q['id'] for q in views}), extra)
    extra_ids = {question['id'] for question in extra}
    return views + [question for question in selected if question['id'] in extra_ids]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('delivery', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('profile', 'additions', 'paper-catalog'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--release-date', help='Override saved release date; otherwise preserve the report value.')
    args = parser.parse_args()
    delivery = args.delivery.resolve()
    report = read(delivery / 'build-report.json')
    originals = [{**row, 'path': str(delivery / row['path'])} for row in report['originals']]
    media = delivery / '媒体源/manifest.json'
    paper_catalog = args.paper_catalog or (delivery / 'paper-catalog.json' if (delivery / 'paper-catalog.json').is_file() else None)
    media_options = {}
    if media.is_file():
        media_options = {'media': media, 'source_media_questions': _source_media_questions(delivery)}
    with tempfile.TemporaryDirectory(prefix='aifs-practice-rebuild-') as temp:
        manifest = Path(temp) / 'originals.json'
        manifest.write_text(json.dumps(originals, ensure_ascii=False), encoding='utf-8')
        result = build(delivery / 'catalog.json', delivery / 'papers.json', delivery / '教材', args.out,
                       originals=manifest, profile=args.profile, additions=args.additions,
                       paper_catalog=paper_catalog,
                       release_date=args.release_date if args.release_date is not None else report.get('release_date'), **media_options)
    print(json.dumps(result['editions'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
