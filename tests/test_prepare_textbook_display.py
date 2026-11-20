"""Verified PDF display projection: frozen sources, exact targets, real renderer."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools.upgrade_study_reader import load_reader_data

BOOK = ROOT.parent / 'delivery/复变函数-全书重讲版-20261029/book'
CORRECTIONS = ROOT / 'data/study-revision-20261029/wholebook/textbook-display-corrections.json'


@pytest.fixture(scope='module')
def projector():
    # Removing this module or returning unchanged projections must fail here or
    # on the real mathematics checks below, not silently skip the regression.
    spec = importlib.util.find_spec('tools.prepare_textbook_display')
    assert spec is not None, 'The verified textbook display projection API is not implemented'
    from tools import prepare_textbook_display
    return prepare_textbook_display


@pytest.fixture(scope='module')
def source():
    return load_reader_data(BOOK)


def test_pdf_verified_symbols_and_diagnostic_ocr_quotes_are_separate(projector, source):
    before = deepcopy(source)
    raw = source['units']['CH02-S05-SS02']['study_textbook_original']
    display, report = projector.project_textbook_display(raw, 'CH02-S05-SS02')
    assert report['status'] == 'corrected' and not report['signature_mismatches']
    assert r'\arg b=\arg a+\alpha' in display
    assert r'\alpha=\arg b-\arg a' in display
    assert r'\Delta_l\arg z=2k\pi' in display
    region, _ = projector.project_textbook_display(
        source['units']['CH01-S02-SS02']['study_textbook_original'], 'CH01-S02-SS02')
    assert r'0<\arg z<\pi' in region and r'r<|z-a|<R' in region
    for uid in ('CH02-S05-SS02', 'CH06-S03'):
        original = source['units'][uid]['study_source_document']
        projected, result = projector.correct_display_document(original, uid)
        assert not result['signature_mismatches']
        refs = [b for b in projected['blocks'] if b.get('owner_id') == 'C-0124'
                and b['json_pointer'].startswith('/reference_catalog/')
                and b['json_pointer'].endswith('/markdown')]
        assert refs and all(r'\arg a+\alpha' in b['markdown'] for b in refs)
        for b in projected['blocks']:
            if b.get('owner_id') == 'C-0124' and b['json_pointer'] in (
                    '/corrections/0/original', '/corrections/0/reason'):
                assert r'\arg a+a' in b['markdown']
                assert r'\arg a+\alpha' not in b['markdown']
    assert source == before


def test_all_catalog_copies_and_source_positions_are_projected_without_anchor_changes(projector, source):
    original_bytes = (BOOK / 'data/data.js').read_bytes()
    changed = 0
    for uid, unit in source['units'].items():
        original = unit['study_source_document']
        frozen = deepcopy(original)
        projected, report = projector.correct_display_document(original, uid)
        assert original == frozen and report['input_unchanged']
        assert not report['signature_mismatches']
        for key in ('anchor', 'block_id', 'owner_id', 'json_pointer'):
            assert [b.get(key) for b in projected['blocks']] == [b.get(key) for b in original['blocks']]
        assert projected['required_block_ids'] == original['required_block_ids']
        entry = next(e for e in projected['reference_catalog']['entries'] if e['id'] == 'C-0124')
        assert r'\arg a+\alpha' in entry['markdown']
        changed += 1
        originals = {b['block_id']: b for b in original['blocks']}
        for b in projected['blocks']:
            if b != originals[b['block_id']]:
                assert len(b['source_map']) == len(b['visible_text'])
                assert all(0 <= x < len(b['raw_text']) for x in b['source_map'])
        again, repeated = projector.correct_display_document(projected, uid)
        assert again == projected and not repeated['changes'] and not repeated['signature_mismatches']
    assert changed == 76
    assert (BOOK / 'data/data.js').read_bytes() == original_bytes


def test_unknown_signatures_are_declined_without_partial_guessing(projector, source):
    raw = source['units']['CH01-S02-SS02']['study_textbook_original']
    old = json.loads(CORRECTIONS.read_text())['units']['CH01-S02-SS02'][0]['old']
    for altered in (raw.replace(old, old + ' 未核OCR改字'), raw + '\n' + old):
        # For the first case replace a character *inside* the registered span.
        if altered.count(old) == 1:
            altered = altered.replace('0<argz<n)', '0<argz<q)')
        result, report = projector.project_textbook_display(altered, 'CH01-S02-SS02')
        assert result == altered and report['signature_mismatches']
    document = deepcopy(source['units']['CH02-S05-SS02']['study_source_document'])
    block = next(b for b in document['blocks'] if b.get('owner_id') == 'C-0124'
                 and b['json_pointer'] == '/corrections/0/reason')
    block['raw_text'] += ' 作者新改字'
    block['markdown'] += ' 作者新改字'
    saved = deepcopy(document)
    projected, report = projector.correct_display_document(document, 'CH02-S05-SS02')
    assert projected == saved and document == saved and report['signature_mismatches']
    with pytest.raises(ValueError, match='unit'):
        projector.correct_display_document(document, 'CH06-S03')


@pytest.mark.parametrize('identity_field,value', [
    ('owner_id', 'C-UNKNOWN'),
    ('json_pointer', '/corrections/99/reason'),
    ('block_id', 'sha256:unverified-new-field'),
])
def test_changed_support_target_identity_is_not_guessed(projector, source, identity_field, value):
    document = deepcopy(source['units']['CH02-S05-SS02']['study_source_document'])
    block = next(b for b in document['blocks'] if b.get('owner_id') == 'C-0124'
                 and b['json_pointer'] == '/corrections/0/reason')
    block[identity_field] = value
    frozen = deepcopy(document)
    projected, report = projector.correct_display_document(document, 'CH02-S05-SS02')
    assert report['signature_mismatches'] and report['changes'] == []
    assert projected == frozen and document == frozen


def test_global_definition_updates_exact_formal_but_keeps_source_quotes(projector):
    parsed = json.loads((ROOT / 'data/parse/complex-analysis-v2/concepts.json').read_text())
    item = next(x for x in parsed['concepts'] if x['id'] == 'C-0124')
    concepts = {'C-0124': item, 'unrelated': {'formal': 'argb = arga + a'}}
    before = deepcopy(concepts)
    projected, report = projector.correct_display_concepts(concepts)
    assert not report['signature_mismatches'] and report['changes']
    assert r'\arg a+\alpha' in projected['C-0124']['formal']
    assert projected['C-0124']['source_quotes'] == item['source_quotes']
    assert projected['C-0124']['occurrences'] == item['occurrences']
    assert projected['unrelated'] == concepts['unrelated'] and concepts == before
    repeated, second = projector.correct_display_concepts(projected)
    assert repeated == projected and not second['changes']
    malformed = deepcopy(concepts)
    malformed['C-0124']['formal'] += ' 未核内容'
    result, report = projector.correct_display_concepts(malformed)
    assert result == malformed and report['signature_mismatches']


def test_real_reader_reprojects_and_removes_the_eight_bare_math_findings(projector, source):
    projected = {}
    for uid, u in source['units'].items():
        textbook, tr = projector.project_textbook_display(u['study_textbook_original'], uid)
        support, sr = projector.correct_display_document(u['study_source_document'], uid)
        assert not tr['signature_mismatches'] and not sr['signature_mismatches']
        projected[uid] = {'study_lesson': u['study_lesson'], 'study_textbook_original': textbook,
                          'study_source_document': support}
    script = r'''
const fs=require('fs'),assert=require('assert'),{JSDOM}=require('jsdom');
const d=JSON.parse(fs.readFileSync(0,'utf8'));
const core=require('./blocks/render/assets/render-core.js');
const renderer=core.createRenderer({marked:require('./blocks/render/vendor/marked.min.js'),katex:require('./blocks/render/vendor/katex.min.js'),concepts:{},figures:{},models:{}});
const dom=new JSDOM('<main></main>'),host=dom.window.document.querySelector('main');let errors=0,bare=0;
for(const [uid,u]of Object.entries(d)){
 renderer.beginUnit(u);
 const chunks=[renderer.renderMarkdown(u.study_lesson.body_markdown),renderer.readerDocumentHtml(u.study_source_document,'all'),renderer.renderOriginal(u.study_textbook_original)];
 for(const html of chunks){
  host.innerHTML=html;errors+=host.querySelectorAll('.math-error,.katex-error,.source-formula-unrendered').length;
  if(uid==='CH09-S03-SS03' && html===chunks[2]){
   const cases=[...host.querySelectorAll('.katex annotation')].filter(x=>x.textContent.includes('sqrt2-1')&&x.textContent.includes('sqrt5-2'));
   assert.equal(cases.length,1,'printed page 220 alternatives render as one two-brace display');
   assert.equal((cases[0].textContent.match(/\\left\\\{/g)||[]).length,2);
  }
  host.querySelectorAll('.katex,code,script,style').forEach(x=>x.remove());
  const walker=dom.window.document.createTreeWalker(host,4);let n;while(n=walker.nextNode()){
   const s=n.nodeValue;if(/^\s*\[\[(?:TEACHING|TEXTBOOK)_/.test(s))continue;
   if(/\\[a-zA-Z]+|[_^][{a-zA-Z0-9]|[∑∏∫∮∂≤≥≠∞]|[a-zA-Z]\s*[=<>]\s*[-a-zA-Z0-9]/.test(s))bare++;
  }
 }
}
assert.equal(errors,0,'all real KaTeX displays render');assert.equal(bare,0,'all eight real bare math candidates are resolved');dom.window.close();console.log('76 units; zero projection mismatches, KaTeX errors or bare math candidates');
'''
    run = subprocess.run(['node', '-e', script], cwd=ROOT, input=json.dumps(projected, ensure_ascii=False),
                         text=True, capture_output=True, check=False)
    assert run.returncode == 0, run.stdout + '\n' + run.stderr
