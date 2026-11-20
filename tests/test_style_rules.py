"""Editorial regressions use the production policy; length is no longer approval."""
import json

import pytest

from aiforstudy.blocks.content import content_policy as policy
from aiforstudy.blocks.content.prompt import build_content_messages
from test_teaching_policy_v2 import teaching_case


def report(text, source_text=None):
    content, source = teaching_case()
    content['body_markdown'] = text
    if source_text is not None:
        source['source_text'] = source_text
    return policy.check_content(content, source, policy.load_content_policy(source['learner_profile']))


@pytest.mark.parametrize('audience', ['author', 'reviewer', 'repair'])
def test_prompt_contains_every_frozen_rule(audience):
    _, source = teaching_case()
    p = policy.load_content_policy(source['learner_profile'])
    system = build_content_messages(audience, source, p)[0]['content']
    # 规则清单以 JSON 形式注入，引号会被转义；比较时先还原转义再比对原文，
    # 否则任何含引号的 requirement 都会假红（R10/R11 的新判据正是这种）。
    unescaped=system.replace('\\"','"').replace('\\n','\n')
    for rule in p.rules:
        assert rule['rule_id'] in system and rule['requirement'] in unescaped
    assert json.loads(policy.prompt_rules(p, audience))['policy_digest'] == p.digest


@pytest.mark.parametrize('rule,text', [
    ('STYLE-METAPHOR', '第一刀下去把第一列削平，第二刀接着削，第三刀再砍一刀。' * 3),
    ('STYLE-PERSONIFICATION', '这里的 $a$ 管着变量前面的数，b 站在等号右边。'),
    ('STYLE-TEMPLATE', '综上所述，我们来看这个矩阵。'),
    ('STYLE-LABEL', '依据：定义。理由：取值范围。'),
    ('STYLE-SENTENCE', '把条件保留在推理中' * 20 + '。'),
])
def test_style_problems_are_specific_warnings_not_repair_gates(rule, text):
    result = report(text)
    hit = next(row for row in result.warnings if row['rule_id'] == rule)
    assert hit['severity'] == 'warning' and hit['hits']
    assert not any(row['rule_id'].startswith('STYLE-') for row in result.blocking_issues)


def test_all_editorial_rules_including_length_are_soft():
    rows = [row for row in policy.load_content_policy().rules if row['rule_id'].startswith('STYLE-')]
    assert len(rows) == 6
    assert all(row['severity'] == 'warning' for row in rows)
    result = report('n 可以取零。')
    assert any(row['rule_id'] == 'STYLE-COVERAGE' for row in result.warnings)
    assert result.decision == 'needs_review'


def test_clean_text_has_no_false_style_word_hits():
    clean = ('把一个方程乘以非零常数再加到另一个方程上，方程组的解不变。'
             '利用这一点，我们可以把方程组化成阶梯形，再逐个求出未知量。')
    assert not [row for row in report(clean).warnings if row['rule_id'] != 'STYLE-COVERAGE']


@pytest.mark.parametrize('source_length,threshold', [(0, 250), (100, 250), (1000, 600), (416, 250), (417, 250)])
def test_legacy_floor_formula_survives_only_as_editorial_diagnostic(source_length, threshold):
    # Keep the numeric boundary regression, but never promote it to correctness.
    result = report('n 可以取零。', '文' * source_length)
    warning = next(row for row in result.warnings if row['rule_id'] == 'STYLE-COVERAGE')
    assert warning['threshold'] == threshold
    assert warning['severity'] == 'warning'


def test_formula_source_length_uses_visible_prose_for_editorial_floor():
    result = report('说明条件。', '$' + r'\frac{a}{b}' * 1000 + '$')
    warning = next(row for row in result.warnings if row['rule_id'] == 'STYLE-COVERAGE')
    assert warning['threshold'] == 250
