"""Rebuild from a moved delivery folder without the original computer's paths."""
from pathlib import Path
import argparse
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.build_practice_delivery import build, read
from aiforstudy.practice.catalog import select_edition, validate_review_files


def _source_media_questions(delivery):
    """Recover the reviewed teacher questions actually selected by the saved export."""
    additions = delivery / 'teacher-additions.json'
    if not additions.is_file():
        return ()
    extra = read(additions)
    if extra:
        validate_review_files(extra, delivery)
    selected = select_edition(read(delivery / 'catalog.json'), 'custom',
                              read(delivery / 'teacher-profile.json'), extra)
    extra_ids = {question['id'] for question in extra}
    return [question for question in selected if question['id'] in extra_ids]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('delivery', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('profile', 'additions'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args()
    delivery = args.delivery.resolve()
    report = read(delivery / 'build-report.json')
    originals = [{**row, 'path': str(delivery / row['path'])} for row in report['originals']]
    media = delivery / '媒体源/manifest.json'
    media_options = {}
    if media.is_file():
        media_options = {'media': media, 'source_media_questions': _source_media_questions(delivery)}
    with tempfile.TemporaryDirectory(prefix='aifs-practice-rebuild-') as temp:
        manifest = Path(temp) / 'originals.json'
        manifest.write_text(json.dumps(originals, ensure_ascii=False), encoding='utf-8')
        result = build(delivery / 'catalog.json', delivery / 'papers.json', delivery / '教材', args.out,
                       originals=manifest, profile=args.profile, additions=args.additions, **media_options)
    print(json.dumps(result['editions'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
