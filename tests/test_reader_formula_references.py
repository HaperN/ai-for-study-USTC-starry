"""Misplaced author links remain math and keep their exact popup destination."""
import json
from pathlib import Path
import subprocess
import pytest

from aiforstudy.blocks.content.reader_document import _project_text

ROOT = Path(__file__).resolve().parents[1]
ANCHOR = 'reader-0123456789abcdef0123'


def render(raw, label='交换律'):
    visible, mapping, spans, markdown = _project_text(raw, {('CONCEPT', 'C-0015'): {'label': label, 'anchor': ANCHOR}})
    field = dict(block_id='body', json_pointer='/body_markdown', owner_id='U', group='body_markdown',
        field_label='正文', anchor='reader-1123456789abcdef0123', raw_text=raw,
        visible_text=visible, source_map=mapping, marker_spans=spans, markdown=markdown)
    script = r'''const fs=require('fs'), {JSDOM}=require('jsdom');
const field=JSON.parse(fs.readFileSync(0,'utf8'));
const marked=require('./blocks/render/vendor/marked.min.js'),katex=require('./blocks/render/vendor/katex.min.js');
const parser=require('./blocks/render/assets/reader-markdown.js');
const parsed=parser.parseReaderField(field.markdown,{marked,field});
const core=require('./blocks/render/assets/render-core.js').createRenderer({marked,katex});
const html=core.readerDocumentHtml({schema_version:'ReaderDocumentV1',blocks:[field]},'body');
const d=new JSDOM(html).window.document;
process.stdout.write(JSON.stringify({errors:d.querySelectorAll('.katex-error,.math-error').length,
  formulas:[...d.querySelectorAll('annotation[encoding="application/x-tex"]')].filter(n=>!n.closest('a')).map(n=>n.textContent),
  links:[...d.querySelectorAll('a')].map(a=>({href:a.getAttribute('href'),insideMath:!!a.closest('.katex')})),blocks:parsed.blocks}));'''
    result = subprocess.run(['node', '-e', script], cwd=ROOT, input=json.dumps(field),
        capture_output=True, text=True, check=True)
    return field, json.loads(result.stdout)


def test_real_commutativity_formula_retains_math_and_adjacent_clickable_reference():
    raw = r'😀四则运算：$$z_1+z_2=z_2+z_1,\quad z_1z_2=z_2z_1 \quad（[[CONCEPT:C-0015|交换律]]）;$$'
    field, result = render(raw)
    assert result['errors'] == 0
    assert len(result['formulas']) == 1
    assert r'z_1+z_2=z_2+z_1,\quad z_1z_2=z_2z_1' in result['formulas'][0]
    assert '#reader-' not in result['formulas'][0]
    assert result['links'] == [{'href': '#' + ANCHOR, 'insideMath': False}]
    assert result['blocks'][0]['source_start_cp'] == 0
    assert result['blocks'][0]['source_end_cp'] == len(raw)
    assert field['raw_text'] == raw
    assert len(field['source_map']) == len(field['visible_text'])


def test_linked_math_variable_does_not_disappear_or_merge_with_previous_factor():
    raw = r'乘积 $x[[CONCEPT:C-0015|x]]=x^2$。'
    field, result = render(raw, 'x')
    assert result['errors'] == 0
    assert result['formulas'] == ['xx=x^2']
    assert field['visible_text'] == r'乘积 $xx=x^2$。'
    assert result['links'] == [{'href': '#' + ANCHOR, 'insideMath': False}]


@pytest.mark.parametrize('prefix', ['', '```text\n$not_math\n```\n\n',
    '~~~text\n$not_math\n~~~\n\n', '`$not_math`\n\n', '    $not_math\n\n',
    '> ```text\n> $not_math\n> ```\n\n', '- code\n\n      $not_math\n\n'])
def test_multiline_inline_formula_keeps_both_factors_after_literal_code(prefix):
    raw = prefix + '$x\n[[CONCEPT:C-0015|x]]=x^2$'
    field, result = render(raw, 'x')
    assert result['errors'] == 0
    assert result['formulas'] == ['x\nx=x^2']
    assert field['visible_text'] == prefix + '$x\nx=x^2$'
    assert result['links'] == [{'href': '#' + ANCHOR, 'insideMath': False}]
    assert field['raw_text'] == raw
    assert len(field['source_map']) == len(field['visible_text'])


@pytest.mark.parametrize('prefix', ['`$not_math` ', '```text\n$not_math\n```\n\n'])
def test_code_dollar_does_not_turn_following_prose_into_a_formula(prefix):
    field, result = render(prefix + 'x[[CONCEPT:C-0015|x]]$', 'x')
    assert result['formulas'] == []
    assert field['visible_text'] == prefix + 'x$'


def test_plain_prose_concept_name_merge_and_frozen_projection_stay_compatible():
    raw = '交换律[[CONCEPT:C-0015|交换律]]。'
    field, result = render(raw)
    assert field['visible_text'] == '交换律。'
    assert result['errors'] == 0
    assert result['links'] == [{'href': '#' + ANCHOR, 'insideMath': False}]


@pytest.mark.parametrize('opening,closing', [('$', '$'), ('$$', '$$'), (r'\(', r'\)'), (r'\[', r'\]')])
def test_delimiters_preserve_linked_subscript_and_fraction_labels(opening, closing):
    raw = opening + r'[[CONCEPT:C-0015|\frac{z_1}{z_2}]]=w' + closing
    _, result = render(raw)
    assert result['errors'] == 0
    assert result['formulas'] == [r'\frac{z_1}{z_2}=w']
    assert result['links'] == [{'href': '#' + ANCHOR, 'insideMath': False}]
