"""Offline transport for the real default graph-4 content/repair boundary."""
import copy
import json

from support.book_factory import BOOK, UNIT, ScriptedTransport, P0_FINDING


class ContentTransport(ScriptedTransport):
    def __init__(self, *, closure_status='closed', supplement=False,
                 missing_supplement_id=False, rejection='冻结标准尚未满足', new_p0=False):
        super().__init__(closure_status=closure_status)
        self.supplement = supplement
        self.missing_supplement_id = missing_supplement_id
        self.rejection = rejection
        self.new_p0 = new_p0
        self.patches = []
        self.supplements = []
        self.reviews = []
        self.author_calls = 0

    @staticmethod
    def _finding(block, source_text):
        return {'rule_id': 'CONTENT-MATH', 'issue_key': 'condition-a',
                'severity': 'P0', 'block_id': block['block_id'],
                'json_pointer': block['json_pointer'], 'quote': block['visible_text'],
                'source_quote': source_text.strip(),
                'problem': '甲句遗漏适用条件A', 'learner_obstacle': '读者会把条件命题当作恒真',
                'repair_instructions': copy.deepcopy(P0_FINDING['repair_instructions']),
                'criteria': copy.deepcopy(P0_FINDING['criteria'])}

    def _answer(self, system, user):
        data = json.loads(user)
        if system.startswith('你是教材内容作者'):
            self.author_calls += 1
            return json.dumps({'_meta': {'schema_version': '3.0'}, 'book_id': BOOK,
                'unit_id': UNIT, 'title': '甲与乙的条件', 'body_markdown': '甲句。乙句。',
                'annotations': [], 'explainables': [], 'examples': [],
                'concepts': [{'id': 'C-0001', 'name': '甲定义', 'type': 'definition',
                              'role': 'introduced', 'explained_where': 'body'}],
                'teaching_plan': {'claims': [], 'steps': []}}, ensure_ascii=False)
        if system.startswith('只输出 ContentPatchV1'):
            self.patches.append(copy.deepcopy(data))
            contract = data['patch_contract']
            return json.dumps({**contract, 'operations': [{'op': 'replace',
                'path': '/body_markdown',
                'value': f'甲句仅在条件A成立时适用（第{len(self.patches)}次核对）。乙句适用条件B。'}]}, ensure_ascii=False)
        if '仅补齐给定工单' in system:
            self.supplements.append(copy.deepcopy(data))
            block = next(b for b in data['review_document']['blocks'] if b['json_pointer'] == '/body_markdown')
            findings = []
            for ticket in data['tickets']:
                row = self._finding(block, data['source_text'])
                if not self.missing_supplement_id:
                    row['ticket_id'] = ticket['ticket_id']
                findings.append(row)
            return json.dumps({'findings': findings}, ensure_ascii=False)
        if '只审核内容' in system:
            self.reviews.append(copy.deepcopy(data))
            block = next(b for b in data['blocks'] if b['json_pointer'] == '/body_markdown')
            findings = []
            if not self.patches:
                row = self._finding(block, data['source_text'])
                if self.supplement:
                    row.pop('source_quote')
                findings.append(row)
            elif self.new_p0 and data['mode'] == 'verify_closure':
                row = self._finding(block, data['source_text'])
                row.update(issue_key='new-severe-condition', problem='新发现的严重数学条件错误')
                findings.append(row)
            verdicts = [{'ticket_id': t['ticket_id'], 'criterion_id': c['criterion_id'],
                         'criteria_version': c['version'],
                         'status': 'pass' if self.closure_status == 'closed' else 'fail',
                         'json_pointer': '/body_markdown', 'quote': block['visible_text'],
                         'why': '原冻结条件A标准已满足' if self.closure_status == 'closed' else self.rejection}
                        for t in data.get('tickets', []) for c in t['criteria']]
            return json.dumps({'findings': findings, 'verdicts': verdicts,
                'no_finding_reason': '' if findings else '逐块核对教材条件及回归范围',
                'coverage': {'block_ids': [b['block_id'] for b in data['blocks']],
                             'rule_ids': ['CONTENT-MATH', 'CONTENT-BEGINNER'],
                             'cross_block_checked': True}}, ensure_ascii=False)
        if 'candidates' in data:
            return json.dumps({'decisions': [{'target_id': row['target_id'],
                'requirement': 'none', 'reason': '本节仅比较两个文字条件，无需媒体说明',
                'request_ids': [], 'handled_by': 'media'} for row in data['candidates']],
                'requests': []}, ensure_ascii=False)
        raise AssertionError(f'v4 fixture does not recognize {system[:80]!r}')
