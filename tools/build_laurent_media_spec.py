"""Bind the Laurent animation to a genuinely reviewed practice question.

An optional independent speech review authorizes the canonical spoken text.
Review receipts are input artifacts; this builder never approves any content.
"""
from pathlib import Path
import argparse
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from aiforstudy.practice.catalog import content_digest, validate_catalog, validate_review_files


def build(catalog, output, speech_review=None):
    catalog = Path(catalog)
    questions = json.loads(catalog.read_text(encoding='utf-8'))
    validate_catalog(questions)
    validate_review_files(questions, catalog.parent)
    question = next(q for q in questions if q['id'] == 'MED-LAURENT-001')
    steps = json.loads((ROOT/'examples/practice_media/laurent_steps.json').read_text(encoding='utf-8'))
    review = None
    if speech_review:
        review_file = Path(speech_review)
        review = json.loads(review_file.read_text(encoding='utf-8'))
        if review.get('status') != 'approved' or review.get('issues'):
            raise ValueError('independent speech review is not approved')
        decisions = {row['step_id']: row for row in review.get('steps', [])}
        if set(decisions) != {row['step_id'] for row in steps}:
            raise ValueError('speech review must cover all five authored steps')
        evidence_hash = hashlib.sha256(review_file.read_bytes()).hexdigest()
        for step in steps:
            decision = decisions[step['step_id']]
            text_hash = hashlib.sha256(step['narration_text'].encode()).hexdigest()
            if (decision.get('status') != 'approved' or decision.get('issues')
                    or decision.get('text_sha256') != text_hash):
                raise ValueError('speech review does not approve current text: '+step['step_id'])
            step['canonical_spoken_text'] = step['narration_text']
            step['spoken_review'] = {
                'status': 'approved',
                'scope': 'script mathematics and language, not human listening',
                'evidence_sha256': evidence_hash,
                'text_sha256': text_hash,
            }
    digest = content_digest(question)
    request = {'request_id': 'practice-laurent-v1', 'kind': 'animation',
               'object_refs': [], 'animation': {'steps': [row['display_title'] for row in steps]}}
    program_steps = [{k: v for k, v in row.items() if k != 'display_title'} for row in steps]
    for row in program_steps:
        row['display_markdown'] = row['caption']
    spec = {
        'id': 'laurent', 'question_id': question['id'], 'question_content_sha256': digest,
        'title': '半径跨过奇点时，洛朗展开怎样改变',
        'purpose_markdown': '连续追踪非实射线上的同一个点，核对三个展开域、几何公比与外域低阶项相消。',
        'steps': steps, 'request': request,
        'program': {'schema_version': 'GeneratedMediaCodeV1', 'request_id': request['request_id'],
                    'backend': 'manim', 'object_bindings': [], 'steps': program_steps,
                    'code': (ROOT/'examples/practice_media/laurent_scene.py').read_text(encoding='utf-8')},
        'context': {'request': request, 'approved_content': {
            'question_id': question['id'], 'content_sha256': digest,
            'stem': question['stem'], 'answer': question['answer'], 'solution': question['solution']},
            'approved_models': {}},
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps([spec], ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return {'question_id': question['id'], 'content_sha256': digest,
            'spoken_review': 'approved' if review else 'pending', 'spec': str(output)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--speech-review', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.catalog, args.out, args.speech_review), ensure_ascii=False))
