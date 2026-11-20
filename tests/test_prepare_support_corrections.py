"""Actual-reader regression checks for the narrowly scoped C-0256 display erratum."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools.prepare_support_corrections import (
    BAD_STATEMENT, CORRECT_STATEMENT, TARGET_ANCHOR, EXACT_BLOCK_CORRECTIONS,
    correct_support_document,
)
from tools.upgrade_study_reader import load_reader_data

READER = ROOT.parent / 'delivery/复变函数-教材连贯版-20261029/book'


def test_factorial_counterexample_corrects_real_support_and_popup_without_touching_valid_reciprocals():
    data = load_reader_data(READER)
    documents = {}
    for uid in ('CH05-S02', 'CH05-S04', 'CH07-S01'):
        old = data['units'][uid]['reader_document']
        projected, report = correct_support_document(old, uid)
        assert not report['signature_mismatches']
        entry = next(e for e in projected['reference_catalog']['entries'] if e['id'] == 'C-0256')
        assert entry['markdown'] == CORRECT_STATEMENT
        reference = next(b for b in projected['blocks'] if b.get('owner_id') == 'C-0256' and b['json_pointer'].endswith('/markdown'))
        assert reference['markdown'] == CORRECT_STATEMENT
        # a_n=1/n! gives inverse ratio n→infinity, so this branch must give
        # infinite radius rather than the old erroneous zero radius.
        assert r'\frac{a_{n-1}}{a_n}' in reference['markdown']
        assert r'\Longrightarrow\quad R=r' in reference['markdown']
        assert '$r=+\\infty$ 时 $R=+\\infty$' in reference['markdown']
        assert BAD_STATEMENT not in reference['markdown']
        assert r'R=\frac1L' in reference['markdown'], 'root criterion must still use the reciprocal'
        for b in projected['blocks']:
            if b['json_pointer'] in ('/annotations/3/markdown', '/learning_checks/0/answer'):
                prior = next(x for x in old['blocks'] if x['json_pointer'] == b['json_pointer'])
                assert b == prior, 'already-correct n! explanation / valid R=1/r stays exact'
        documents[uid] = projected
    for uid in ('CH05-S05', 'CH06-S03'):
        original = data['units'][uid]['reader_document']
        projected, report = correct_support_document(original, uid)
        assert not report['signature_mismatches']
        for rule in EXACT_BLOCK_CORRECTIONS:
            if rule['unit_id'] != uid:
                continue
            old = next(b for b in original['blocks'] if b.get('owner_id') == rule['owner_id'] and
                       b.get('json_pointer') == rule['json_pointer'])
            new = next(b for b in projected['blocks'] if b.get('owner_id') == rule['owner_id'] and
                       b.get('json_pointer') == rule['json_pointer'])
            assert old['raw_text'] == rule['before']
            assert new['raw_text'] == rule['after']
            assert new['anchor'] == old['anchor'] == rule['anchor']
            assert new['block_id'] == old['block_id']
            assert len(new['source_map']) == len(new['visible_text'])
        again, second = correct_support_document(projected, uid)
        assert again == projected and not second['changes']
        documents[uid] = projected
    # Real renderer and app popup code in a detached DOM: opens no browser/UI.
    script = r'''
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const documents=JSON.parse(fs.readFileSync(0,'utf8'));
const root=path.resolve('blocks/render');
for(const [uid,reader_document] of Object.entries(documents)){
 const dom=new JSDOM(fs.readFileSync(path.join(root,'templates/index.html'),'utf8'),{url:'https://reader.invalid/#/'+uid,runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,errors=[];w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=function(){};
 w.addEventListener('error',e=>errors.push(e.message));
 w.__DATA__={book:'support-test',scope:[uid],units:{[uid]:{title:'比值公式核对',reader_document,reader_media:[]}},tree:[{id:uid,title:'比值公式核对',unit:true}],concepts:{}};
 for(const file of ['vendor/marked.min.js','vendor/katex.min.js','assets/reader-markdown.js','assets/render-core.js','assets/reader-layout.js','assets/app.js'])w.eval(fs.readFileSync(path.join(root,file),'utf8'));
 const checks=uid==='CH05-S05'?[['reader-883a9fe4d23b5a962319',/仍可能因/,/极限都有限/],['reader-38ce81abd87cce98b33f',/正整数/,/绝对值/]]:
   uid==='CH06-S03'?[['reader-825f87b76bd66d6b4b08',/开放路径/,/不必是/]]:
   [['reader-b2d1feb0c361060b4b8d',/R=r/,/达朗贝尔/]];
 for(const [anchor,a,b] of checks){
  const source=d.getElementById(anchor);assert.ok(source,uid+' target anchor exists');
  assert.equal(source.open,false);assert.match(source.textContent,a);assert.match(source.textContent,b);
  source.querySelector('summary a').click();
  const popup=d.querySelector('.reader-popup[data-reader-target="'+anchor+'"]');assert.ok(popup,uid+' actual popup opened');
  assert.match(popup.textContent,a);assert.match(popup.textContent,b);
  assert.ok(popup.querySelector('.katex'),'corrected formula is real KaTeX output');
  assert.equal(popup.querySelectorAll('.katex-error').length,0);
 }
 assert.deepEqual(errors,[]);dom.window.close();
}
console.log('6 actual folded references and popup clones show corrected mathematics across 5 units');
'''
    result = subprocess.run(['node', '-e', script], cwd=ROOT,
                            input=json.dumps(documents, ensure_ascii=False),
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr


def test_original_snapshot_all_anchors_identity_and_text_offsets_remain_sound():
    source_bytes = (READER / 'data/data.js').read_bytes()
    data = load_reader_data(READER)
    before = deepcopy(data)
    unit = data['units']['CH05-S02']
    frozen = unit['study_source_document']
    original_text = unit['study_textbook_original']
    projected, report = correct_support_document(unit['reader_document'], 'CH05-S02')
    assert data == before
    assert unit['study_source_document'] == frozen == before['units']['CH05-S02']['study_source_document']
    assert unit['study_textbook_original'] == original_text
    assert (READER / 'data/data.js').read_bytes() == source_bytes
    for field in ('anchor', 'block_id', 'owner_id', 'json_pointer'):
        assert [b.get(field) for b in projected['blocks']] == [b.get(field) for b in unit['reader_document']['blocks']]
    assert projected['required_block_ids'] == unit['reader_document']['required_block_ids']
    assert projected['content_hash'] != unit['reader_document']['content_hash']
    assert projected['reference_catalog_hash'] != unit['reader_document']['reference_catalog_hash']
    assert all(report[k] for k in ('anchors_preserved', 'block_ids_preserved', 'owner_ids_preserved', 'input_unchanged'))
    assert next(b for b in projected['blocks'] if b['owner_id'] == 'C-0256')['anchor'] == TARGET_ANCHOR
    original_by_pointer = {b['json_pointer']: b for b in unit['reader_document']['blocks']}
    touched = {c['json_pointer'] for c in report['changes'] if c['kind'] == 'reader_block'}
    assert touched == {'/reference_catalog/entries/126/markdown', '/corrections/0/original'}
    for block in projected['blocks']:
        if block['json_pointer'] not in touched:
            assert block == original_by_pointer[block['json_pointer']]
        else:
            assert len(block['source_map']) == len(block['visible_text'])
            assert all(0 <= offset < len(block['raw_text']) for offset in block['source_map'])
            assert block['marker_spans'] == []
    again, second = correct_support_document(projected, 'CH05-S02')
    assert again == projected
    assert second['status'] == 'already_corrected'
    assert second['changes'] == []
    altered = deepcopy(unit['reader_document'])
    entry = next(e for e in altered['reference_catalog']['entries'] if e['id'] == 'C-0256')
    entry['markdown'] = BAD_STATEMENT + ' 未核对的新文字'
    _, declined = correct_support_document(altered, 'CH05-S02')
    assert declined['signature_mismatches']


if __name__ == '__main__':
    suite = unittest.TestSuite(unittest.FunctionTestCase(fn) for fn in (
        test_factorial_counterexample_corrects_real_support_and_popup_without_touching_valid_reciprocals,
        test_original_snapshot_all_anchors_identity_and_text_offsets_remain_sound,
    ))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
