"""Content regressions: preserve the actual rules, not just a result count."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_calculus_rules_still_include_operations_composition_and_the_local_inverse():
    d=json.loads((ROOT/'data/study-revision-20261029/conclusions-CH01-03.json').read_text())
    result=next(c for c in d['units']['CH02-S03']['conclusions'] if c['id']=='CH02-S03-C005')
    text=result['statement_markdown']
    for rule in [r'[f\pm g]',r'(fg)',r'\frac fg',r'f(g(z))',r'\varphi']:
        assert rule in text, f'The derivative result lost {rule}'
    assert '局部' in text and '反支' in text


def test_disk_fraction_uses_the_conjugate_confirmed_in_the_scanned_textbook():
    d=json.loads((ROOT/'data/study-revision-20261029/conclusions-CH01-03.json').read_text())
    c=next(c for c in d['units']['CH01-S02-SS02']['supplementary_conclusions'] if c['id']=='CH01-S02-SS02-C007')
    assert r'1-\bar a z' in c['statement_markdown']
    assert r'a\in\mathbb C' in c['statement_markdown']
    assert '分母' in c['statement_markdown']
    assert '待核' not in c['title']
