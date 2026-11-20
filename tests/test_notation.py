"""Retained parser inventory false-positive regressions; no obsolete author gate."""
import pytest

from blocks.parse.concepts import notation_inventory as notation
from aiforstudy.blocks.content.content_policy import load_content_policy, applicable_items
from test_teaching_policy_v2 import teaching_case


def keys(text, rows=()):
    return [row['key'] for row in notation.notation_gaps(text, list(rows))]


@pytest.mark.parametrize('word', ['足迹', '奇迹', '轨迹', '痕迹', '事迹', '遗迹', '迹象', '绝迹', '墨迹', '笔迹',
                                  '复数域', '实数域', '有理数域', '整数域', '函数域', '可逆矩阵'])
def test_no_substring_false_positives_on_real_words(word):
    assert keys('下面讨论' + word + '。') == []


@pytest.mark.parametrize('text,expected', [
    ('矩阵的迹等于它的特征值之和。', ['迹']),
    ('下一步要把 $A^{-1}$ 算出来（逆矩阵这一步最容易符号搞反）。', ['逆矩阵']),
    ('我们固定一个数域 $F$。', ['数域']),
    ('元素的代数余子式。', ['代数余子式']),
    (r'计算 $\det(A)$。', ['行列式']),
])
def test_actual_inventory_gap_is_not_suppressed(text, expected):
    assert keys(text) == expected


def test_known_inventory_entry_suppresses_only_its_own_gap():
    text = r'计算 $\det(A)$ 与 $\operatorname{Tr}(A)$。'
    assert keys(text, [{'name': '迹', 'formal': '主对角线元素相加'}]) == ['行列式']
    assert keys(text) == ['行列式', '迹']


def test_parser_watchlist_is_not_a_cross_discipline_quality_gate():
    c, source = teaching_case()
    p = load_content_policy(source['learner_profile'])
    assert not any(row['rule_id'].startswith('NOTATION-') for row in p.rules)
    items = applicable_items(c, source, p)
    assert any(row['rule_id'] == 'PEDAGOGY-CONVENTION' for row in items)
    assert any(row['rule_id'] == 'READER-COHERENCE' for row in items)
    # A textual phrase is not a proof of an early, usable definition. Only the
    # frozen pedagogical reviewer can assess that former heuristic's concern.
    assert all(row['method'] == 'reviewer' for row in items)
