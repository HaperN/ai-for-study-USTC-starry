"""Deterministic, current-edition retrospective study cards; never exam prediction.

Historical ordering follows the existing research metadata protocol: three earlier
same-course regular finals, strict academic-year embargo, no inferred timestamps.
Only training item records enter selection. Evaluation happens after receipt freeze.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path

ANNOTATIONS = Path(__file__).resolve().parents[3] / 'data/parse/complex-analysis-v2/course-library/exam-annotations.json'
POLICY = {'id': 'atomic-six-v1', 'warmup': 3, 'cards': 6,
          'estimated_minutes_per_card': 20, 'foundation_templates': ['conjugate','taylor'], 'recency_decay': 0.75,
          'fixed_order': ['conjugate','taylor','laurent','cauchy','rouche','laplace_ode',
                          'contour_poles','real_trig','primitive','power','log']}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def load_annotations(path=None):
    return json.loads(Path(path or ANNOTATIONS).read_text(encoding='utf-8'))


def validate_annotations(catalog, annotations):
    sessions = {s['id']: s for s in catalog['exam_sessions']}
    seen = set()
    for q in annotations['questions']:
        s = sessions[q['session_id']]
        pages = s['origin']['question_pages']
        if (q['id'] in seen or q['source_id'] != s['source_id']
                or q['source_sha256'] != s['source_id'].removeprefix('sha256:')
                or q['course'] != s['course'] or s['course'] not in ('复变函数A','复变函数B')
                or not pages[0] <= q['pdf_page'] <= pages[1]
                or q['annotation']['template_id'] not in annotations['templates']):
            raise ValueError('EXAM_ANNOTATION_SOURCE_MISMATCH: ' + q['id'])
        seen.add(q['id'])
    for link in annotations.get('textbook_links', []):
        ch = next(c for c in catalog['textbook_pool']['chapters'] if c['chapter']==link['chapter'])
        if (link['source_id'] != catalog['textbook_pool']['source_id']
                or link['source_sha256'] != link['source_id'].removeprefix('sha256:')
                or link['template_id'] not in annotations['templates']
                or not any(link['pdf_page']==p['pdf_page'] and link['number'] in
                           p['new_question_numbers']+p['continued_question_numbers'] for p in ch['pages'])):
            raise ValueError('TEXTBOOK_LINK_SOURCE_MISMATCH')
    expected = {s['id'] for s in sessions.values() if s['course'] in ('复变函数A','复变函数B')}
    if {q['session_id'] for q in annotations['questions']} != expected:
        raise ValueError('Missing A/B session annotations')


def select(annotations, sessions, train_ids, method):
    """Selection receives only declared training IDs; ignores all other labels."""
    rows = {q['id']: q for q in annotations['questions'] if q['session_id'] in train_ids
            and q['annotation']['status'] != 'pending'}
    train = sorted(train_ids, key=lambda sid: (sessions[sid]['academic_year'], sid))
    occurrences = {}
    for q in rows.values():
        occurrences.setdefault(q['annotation']['template_id'], set()).add(q['session_id'])
    score = {}
    for key, ids in occurrences.items():
        if method == 'frequency': score[key] = len(ids)
        elif method == 'recency':
            score[key] = sum(POLICY['recency_decay'] ** (len(train)-1-train.index(s)) for s in ids)
        elif method == 'fixed':
            order = POLICY['fixed_order']
            score[key] = len(order)-order.index(key) if key in order else 0
        else: raise ValueError('Unknown method')
    foundation = [k for k in POLICY['foundation_templates'] if k in score]
    keys = foundation + [k for k in sorted(score, key=lambda k: (-score[k], k)) if k not in foundation][:POLICY['cards']-len(foundation)]
    cards = []
    for k in keys:
        # Oldest representative is independent of target and minimizes later edits.
        q = min((q for q in rows.values() if q['annotation']['template_id']==k),
                key=lambda q: (sessions[q['session_id']]['academic_year'], q['id']))
        cards.append({'template_id': k, 'question_id': q['id'], 'source_id': q['source_id'],
                      'pdf_page': q['pdf_page'], 'printed_label': q['printed_label'],
                      'session_id': q['session_id'], 'frequency_sessions': len(occurrences[k]),
                      'rank_score': score[k], 'estimated_minutes': POLICY['estimated_minutes_per_card'],
                      'answer_status': '未验算；原题无成套参考答案'})
    # No global annotation hash: target mutations must not change this receipt.
    frozen = {'policy': POLICY, 'method': method, 'train_session_ids': train,
              'training_items': sorted(rows.values(), key=lambda q:q['id']), 'cards': cards}
    return {'cards': cards, 'training_hash': digest(frozen), 'prediction_hash': digest(cards),
            'selected_count': len(cards), 'estimated_minutes': sum(c['estimated_minutes'] for c in cards)}


def evaluate(annotations, target_id, train_ids, prediction):
    target = [q for q in annotations['questions'] if q['session_id']==target_id]
    eligible = [q for q in target if q['annotation']['status']!='pending']
    keys = {c['template_id'] for c in prediction['cards']}
    topics = {annotations['templates'][k]['topic'] for k in keys}
    covered = [q for q in eligible if q['annotation']['template_id'] in keys]
    train_families = {q['family_id'] for q in annotations['questions'] if q['session_id'] in train_ids}
    repeat = [q for q in eligible if q['family_review']=='verified_repeat' and q['family_id'] in train_families]
    distinct = [q for q in eligible if q not in repeat]
    ratio = lambda n,d: n/d if d else None
    return {'eligible_items':len(eligible),'all_items':len(target),'excluded_pending':len(target)-len(eligible),
            'template_covered_items':len(covered),'template_coverage':ratio(len(covered),len(eligible)),
            'topic_coverage':ratio(sum(annotations['templates'][q['annotation']['template_id']]['topic'] in topics for q in eligible),len(eligible)),
            'score_coverage':None, 'score_coverage_reason':'独立小问分值不全，不平均分配总分',
            'new_family_coverage':None,'new_family_reason':'未匹配到已核重复题族不等于新题族；其余题族待核',
            'verified_repeat_items':len(repeat),
            'coverage_excluding_verified_repeats':ratio(sum(q in covered for q in distinct),len(distinct)),
            'item_denominator_note':'按原子任务计数；不能解释为考试分数或学习效果'}


def analyze(catalog, annotations=None):
    annotations = annotations or load_annotations()
    validate_annotations(catalog, annotations)
    sessions = {s['id']:s for s in catalog['exam_sessions']}
    result = {'schema_version':'ExamStudyV1','mode':'retrospective_ordinal',
              'status':'historical_audit_exposed','is_synthetic':False,'policy':POLICY,
              'annotations_hash':digest(annotations),'templates':annotations['templates'],
              'questions':annotations['questions'],'textbook_links':annotations.get('textbook_links',[]),'folds':[],'courses':[],
              'limitations':annotations['limitations']+[
                  '当前整理版已经审阅；这是历史顺序回顾，非盲测。历史可得时间未知。',
                  '固定每次6道原子任务，20分钟/题仅为设计估算；未测量学习收益。',
                  '频次按出现的场次计；A/B分开，不含补考和专业复分析。',
                  '没有未来命中率、考试分数或教师个人出题模式的结论。'],
              'strict_asof':{'status':'blocked_historical_availability_unknown','metrics':None,
                             'reason':'无历史版本可得证据、准确考试时间与预测截止点'}}
    for course in ('复变函数A','复变函数B'):
        series = sorted((s for s in sessions.values() if s['course']==course and not s['makeup']
                         and s['exam_type']=='期末' and s['question_locator_id']),
                        key=lambda s:(s['academic_year'],s['id']))
        for target in series:
            train = {s['id'] for s in series if s['academic_year'][:4]<target['academic_year'][:4]}
            if len(train)<POLICY['warmup']: continue
            predictions = {m:select(annotations,sessions,train,m) for m in ('frequency','recency','fixed')}
            # Immutable receipts are constructed before reading target labels for scoring.
            result['folds'].append({'target_session_id':target['id'],'course':course,
                'target_ordinal':int(target['academic_year'][:4]),'train_session_ids':sorted(train),
                'predictions':predictions,'metrics':{m:evaluate(annotations,target['id'],train,p) for m,p in predictions.items()}})
        train = {s['id'] for s in series}
        result['courses'].append({'course':course,'session_count':len(train),
                                 'recommendation':select(annotations,sessions,train,'frequency')})
    result['textbook_pool'] = [
        {'chapter':ch['chapter'],'number':n,'pdf_page':page['pdf_page'],
         'source_id':catalog['textbook_pool']['source_id'],'assignment_status':'unknown',
         'template_id':None,'mapping_status':'pending_item_review'}
        for ch in catalog['textbook_pool']['chapters'] for page in ch['pages'] for n in page['new_question_numbers']]
    return result
