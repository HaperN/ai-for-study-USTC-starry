#!/usr/bin/env python3
"""Shared teaching fixture and browser probe for current code-media acceptance.

Import these helpers from verify_code_media_e2e, the sole executable harness.
This module has no command-line entry point. Its controlled responses and local
browser probe do not assess independent model quality or import historical media.
"""
from __future__ import annotations

from copy import deepcopy
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]

from aiforstudy.core.digest import digest_file, digest_of
from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.blocks.math import parse_math_model
from aiforstudy.blocks.media.validation import display_runtime
from aiforstudy.blocks.visual.manim_adapter import default_services
from aiforstudy.pipeline.engine import run_request
from aiforstudy.pipeline.services import Providers
from support.runner_v5 import TeachingTransport, setup_book
from support.book_factory import _request, UNIT
from support.content_case import teaching_case, authoring

BODY = ('本节约定二维实向量按对应坐标相加。设 a=(2,1)，b=(1,2)，则 '
        'a+b=(2+1,1+2)=(3,3)=c。第一坐标 2+1=3。第二坐标 1+2=3。'
        '图中把 b 平移到 a 的终点，方向与长度不变；'
        '合向量从原点指向 c=(3,3)。判断把两个向量的长度直接相加是否等于合向量的长度，并说明理由。')
CONDITIONS = ['a、b、c 为同一二维直角坐标系中的实向量']
CONCLUSION = '对应坐标相加得到 a+b=(3,3)=c'


def const(value):
    return {'op': 'const', 'value': value}


def vector_model(obligation_ids):
    vectors = [('a', [2, 1]), ('b', [1, 2]), ('c', [3, 3])]
    return parse_math_model({'schema_version': '1.0', 'model_id': 'M', 'symbols': [],
        'objects': [{'math_object_id': ident, 'kind': 'vector', 'entries': [const(n) for n in values]}
                    for ident, values in vectors] + [{'math_object_id': 'sum', 'kind': 'vector',
            'entries': [{'op': 'add', 'left': const(a), 'right': const(b)} for a, b in [(2, 1), (1, 2)]]}] +
            [{'math_object_id': key, 'kind': 'scalar', 'expr': expression} for key, expression in [
                ('xsum', {'op': 'add', 'left': const(2), 'right': const(1)}),
                ('ysum', {'op': 'add', 'left': const(1), 'right': const(2)}),
                ('xresult', const(3)), ('yresult', const(3))]],
        'conditions': [], 'claims': [{'claim_id': 'K', 'kind': 'identity', 'math_object_id': 'sum',
            'lhs_object_id': 'sum', 'rhs_object_id': 'c'}] +
            [{'claim_id': axis.upper(), 'kind': 'identity', 'math_object_id': axis + 'sum',
                'lhs_object_id': axis + 'sum', 'rhs_object_id': axis + 'result'} for axis in ['x', 'y']],
        'source_refs': obligation_ids})


def author_envelope(frozen):
    c, _ = teaching_case()
    obligation = next(row for row in frozen['source_obligations'] if row['conclusion'] == CONCLUSION)
    model = vector_model([row['id'] for row in frozen['source_obligations']])
    c.update(book_id=frozen['book_id'], unit_id=frozen['unit_id'], title='二维向量相加',
        reader_intro='把对应坐标相加，并比较几何合成与坐标计算。', body_markdown=frozen['source_text'],
        summary=CONCLUSION, math_models=[model.as_dict()])
    c['teaching_plan']['goals'][0].update(observable_task='计算 a+b 并指出平移后的 b 与合向量 c',
        visual_requirement={'required': True, 'reason': '比较向量首尾相接与逐坐标相加的同一结果。'})
    c['teaching_plan']['claims'][0].update(kind=obligation['kind'], conditions=obligation['conditions'],
        conclusion=obligation['conclusion'], symbols=['a', 'b', 'c'], math_model_refs=['M'],
        source_refs=[{'kind': 'textbook', 'obligation_id': obligation['id'], 'quote': obligation['source_excerpt']}])
    for ident, conclusion in [('X', '第一坐标 2+1=3'), ('Y', '第二坐标 1+2=3')]:
        coordinate = next(row for row in frozen['source_obligations'] if row['conclusion'] == conclusion)
        c['teaching_plan']['claims'].append({**deepcopy(c['teaching_plan']['claims'][0]),
            'id': ident, 'conclusion': conclusion,
            'source_refs': [{'kind': 'textbook', 'obligation_id': coordinate['id'], 'quote': coordinate['source_excerpt']}]})
    c['teaching_plan']['goals'][0]['claim_refs'] = ['K', 'X', 'Y']
    for evidence in c['teaching_evidence']:
        evidence['claim_refs'] = ['K', 'X', 'Y']
    requests = []
    for key, kind, objects in [('vector-picture', 'geometry', ['a', 'b', 'c']),
                               ('coordinate-change', 'animation', ['xsum', 'xresult', 'ysum', 'yresult'])]:
        animation = kind == 'animation'
        r = {'request_key': key, 'kind': kind, 'goal_refs': ['G'], 'claim_ids': ['X', 'Y'] if animation else ['K'],
            'math_model_refs': [{'model_id': 'M', 'digest': model.digest}],
            'object_refs': [{'model_id': 'M', 'object_id': x} for x in objects],
            'learning_goal': '把向量首尾相接与逐坐标求和联系起来',
            'observe': '观察两个坐标分别从加法表达式变为三' if animation else '从原点沿 a 再沿平移后的 b 到达 c=(3,3)',
            'misconception': '不能把向量相加误读为长度直接相加',
            'placement': {'target_pointer': '/body_markdown', 'owner_id': c['unit_id'],
                'anchor_text': c['body_markdown'], 'position': 'after', 'occurrence': 1},
            'conditions': CONDITIONS,
            'acceptance_criteria': [{'criterion_id': key + '-relation', 'version': '1', 'kind': 'mathematical',
                'statement': '批准的向量逐坐标相加等于 (3,3)，画面保持对象身份与对应关系。',
                'evidence_required': ['object_bindings', 'rendered_frame']}],
            'caption': '逐坐标相加得到 c=(3,3)' if animation else '向量首尾相接：a+b=c',
            'alt_text': '二维向量 (2,1) 与 (1,2) 相加得到 (3,3)'}
        if animation:
            r['animation'] = {'initial_state': '显示第一坐标的二加一',
                'steps': ['第一坐标二加一变为三', '第二坐标一加二变为三'],
                'key_pauses': ['每次计算完成后停顿核对对应坐标'], 'final_state': '两个坐标分别显示三'}
        requests.append(r)
    decision = {'target_id': 'G', 'goal_id': 'G', 'requirement': 'required',
        'reason': '把首尾相接的空间关系与对应坐标的计算过程配对。',
        'benefit': '读者能逐坐标核对同一合向量。', 'cost': '阅读一张图及八秒动画。',
        'text_alternative': '正文给出准确坐标；图与动画分别呈现几何合成和计算变化。',
        'request_ids': [r['request_key'] for r in requests], 'handled_by': 'media'}
    return authoring(c, {'decisions': [decision], 'requests': requests})




class AcceptanceTransport(TeachingTransport):
    def __init__(self):
        super().__init__()
        self.exchanges = []

    def _answer(self, system, user):
        data = json.loads(user)
        task = data.get('role_task')
        if task == 'source_inventory':
            result = json.loads(super()._answer(system, user))
            result['obligations'] = [{'id': ident, 'kind': 'convention', 'level': 'core', 'concept_id': '',
                'conditions': CONDITIONS, 'conclusion': conclusion, 'source_excerpt': excerpt}
                for ident, conclusion, excerpt in [
                    ('NEW-sum', CONCLUSION, data['source_text'].split('第一坐标')[0]),
                    ('NEW-x', '第一坐标 2+1=3', '第一坐标 2+1=3。'),
                    ('NEW-y', '第二坐标 1+2=3', '第二坐标 1+2=3。')]]
            for row in result['coverage']:
                row['obligation_ids'] = [item['id'] for item in result['obligations']]
                row['reason'] = '本段给出二维向量逐坐标相加的约定及数值例。'
        elif task == 'author':
            result = author_envelope(data['frozen_context'])
        elif 'review_items' in data:
            result = {'verdicts': [{'criterion_id': row['criterion_id'], 'version': row['version'],
                'status': 'pass', 'why': '受控数学例的目标、绑定数据和每一真实画面由本次证据给出，供集成契约验收。',
                'evidence_ids': list(data['evidence_catalog'])} for row in data['review_items']], 'problems': []}
        else:
            result = json.loads(super()._answer(system, user))
            if task == 'reviewer':
                for row in result['verdicts']:
                    row['reason'] = '固定向量例保留二维条件，对应坐标相加得到三，正文证据与绑定模型一致。'
        self.exchanges.append({'input': data, 'response': deepcopy(result)})
        return json.dumps(result, ensure_ascii=False)


def _payload(store, stage):
    raw = read_json(store.work / 'products' / stage / (UNIT + '.json'))
    return raw.get('payload', raw)


def run_acceptance(output):
    # Historical command follows the current code-generation engine.
    from verify_code_media_e2e import run_acceptance as current_acceptance
    return current_acceptance(output)




def browser_check(html, output, browser, node, playwright, request_ids):
    from aiforstudy.checks.offline import _range_handler
    # Use the production acceptance server's MP4 Range handling; Python's plain
    # SimpleHTTPRequestHandler cannot support the player's seek contract.
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _range_handler(str(html.parent)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        command = [str(node), str(ROOT / 'tools/verify_author_media_fresh_e2e.cjs'), str(playwright), str(browser),
            f'http://127.0.0.1:{server.server_port}/index.html', str(output), json.dumps(request_ids)]
        proc = subprocess.run(command, capture_output=True, text=True, timeout=90)
        (output / 'browser-command.json').write_text(json.dumps(command, ensure_ascii=False, indent=2))
        (output / 'browser-stderr.txt').write_text(proc.stderr)
        assert proc.returncode == 0, proc.stderr
        report = json.loads(proc.stdout)
        write_json_atomic(output / 'browser.json', report)
        return report
    finally:
        server.shutdown()
        server.server_close()
