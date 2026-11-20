"""Focused counterexamples for the independently verified second-pass errata."""
from copy import deepcopy
import cmath
import json
import math
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools.prepare_support_corrections import (
    CANONICAL_CONCEPT_CORRECTIONS, EXACT_BLOCK_CORRECTIONS,
    correct_global_concepts, correct_support_document,
)
from tools.upgrade_study_reader import load_reader_data

# This file remains the V3 regression slice; later incremental rules have
# their own targeted tests and must not change these four-case counts.
CANONICAL_CONCEPT_CORRECTIONS = [r for r in CANONICAL_CONCEPT_CORRECTIONS
                                 if r['concept_id'] in {'C-0175','C-0522','C-0219','C-0796'}]

@pytest.fixture(scope='module')
def book():
    return load_reader_data(ROOT.parent / 'delivery/复变函数-教材连贯版-20261029/book')

def canonical(book, ident):
    rule = next(r for r in CANONICAL_CONCEPT_CORRECTIONS if r['concept_id'] == ident)
    original = book['units'][rule['origin_unit_id']]['reader_document']
    projected, report = correct_support_document(original, rule['origin_unit_id'])
    assert not report['signature_mismatches']
    entry = next(e for e in projected['reference_catalog']['entries'] if e['id'] == ident)
    block = next(b for b in projected['blocks'] if b.get('owner_id') == ident and b['json_pointer'].endswith('/markdown'))
    assert entry['markdown'] == block['markdown'] == rule['after']
    old = next(b for b in original['blocks'] if b['block_id'] == block['block_id'])
    assert block['anchor'] == old['anchor'] and old['raw_text'] in rule['before_variants']
    return entry['markdown']

def note(book, owner):
    rule = next(r for r in EXACT_BLOCK_CORRECTIONS if r['owner_id'] == owner)
    original = book['units'][rule['unit_id']]['reader_document']
    projected, report = correct_support_document(original, rule['unit_id'])
    assert not report['signature_mismatches']
    block = next(b for b in projected['blocks'] if b.get('owner_id') == owner and b['json_pointer'] == rule['json_pointer'])
    assert block['raw_text'] == block['markdown'] == rule['after'] and block['anchor'] == rule['anchor']
    return block['markdown']

def test_erf_transform_has_square_root_denominator_and_correct_definition(book):
    text = canonical(book, 'C-0796')
    assert r'\frac{\sqrt a}{p\sqrt{p+a}}' in text and r'\int_0^x' in text
    assert '$a>0$' in text and r'\operatorname{Re}p>0' in text
    a, p, end, n = 3.0, 2.0, 6.0, 4000
    h = end/n
    def integrand(s):return 2*s*math.erf(math.sqrt(a)*s)*math.exp(-p*s*s)
    measured=h/3*(integrand(0)+integrand(end)+sum((4 if i%2 else 2)*integrand(i*h) for i in range(1,n)))
    assert measured == pytest.approx(math.sqrt(a)/(p*math.sqrt(p+a)), abs=1e-10)
    assert abs(measured-math.sqrt(a)/(p*(p+a))) > .1

def test_hyperbolic_pole_sets_are_assigned_to_the_correct_denominators(book):
    text=canonical(book,'C-0175')
    assert r'\tanh z=\sinh z/\cosh z' in text and r'z=(n+1/2)\pi\mathrm i' in text
    assert abs(cmath.cosh(.5j*math.pi))<1e-14 and abs(cmath.sinh(1j*math.pi))<1e-14
    assert abs(cmath.sinh(.5j*math.pi))>.9

def test_integer_power_argument_is_multiplied_by_n(book):
    text=canonical(book,'C-0522')
    assert r'e^{\mathrm in\theta}' in text and '$0^n=0$' in text
    assert cmath.exp(2j*math.pi/2)==pytest.approx(-1)
    assert cmath.exp(1j*math.pi/2)==pytest.approx(1j)

def test_cauchy_estimate_needs_disk_not_just_circle(book):
    text=canonical(book,'C-0219')
    assert '整个闭圆盘' in text and r'|\zeta-a|\le R' in text
    # D=C\{0}, f=1/z, a=1, R=3: circle in D but disk contains 0.
    M,R,abs_first_derivative=.5,3.0,1.0
    assert abs_first_derivative>M/R and abs(0-1)<R

def test_poisson_kernel_preserves_constant_boundary_values(book):
    text=note(book,'EXAMPLE15-COEFFICIENT')
    assert r'y\,\mathrm dt' in text and '$y>0$' in text
    y=2.0
    assert (1/math.pi)*(math.pi/y)==.5
    assert (1/math.pi)*(y*math.pi/y)==1

def test_multiplied_ray_angle_is_reduced_modulo_two_pi(book):
    text=note(book,'NOTE-NONZERO-DERIVATIVE')
    assert r'\bmod 2\pi' in text and r'\min(\delta,2\pi-\delta)' in text
    multiplied=3*math.pi;delta=multiplied%(2*math.pi)
    assert min(multiplied,2*math.pi-multiplied)<0
    assert min(delta,2*math.pi-delta)==pytest.approx(math.pi)

def test_absolute_integral_bound_keeps_middle_and_both_tails(book):
    text=note(book,'DERIVE-ABSOLUTE-CONVERGENCE')
    assert r'\int_{-A}^{A}' in text and r'2C\int_A' in text
    # 1/(1+x²) has C=1 on |x|>=1, but its full integral is π>1.
    assert math.pi>1 and math.pi<2*math.atan(1)+2

def test_all_reader_catalog_copies_and_global_formal_mirrors_stay_consistent(book):
    original=deepcopy(book);count=0
    for uid,unit in book['units'].items():
        projected,report=correct_support_document(unit['reader_document'],uid)
        assert not report['signature_mismatches']
        for rule in CANONICAL_CONCEPT_CORRECTIONS:
            entry=next(e for e in projected['reference_catalog']['entries'] if e['id']==rule['concept_id'])
            assert entry['markdown']==rule['after'];count+=1
        twice,again=correct_support_document(projected,uid)
        assert twice==projected and again['changes']==[]
    assert count==76*4 and book==original
    empty,no_changes=correct_global_concepts(book['concepts'])
    assert empty==book['concepts']=={} and no_changes['changes']==[]
    # Actual legacy global shape from parse/concepts.json / CONCEPT_COPY_FIELDS.
    parsed=json.loads((ROOT/'data/parse/complex-analysis-v2/concepts.json').read_text())
    real={r['id']:r for r in parsed['concepts']};given={}
    for rule in CANONICAL_CONCEPT_CORRECTIONS:
        ident=rule['concept_id']
        given[ident]=deepcopy(real.get(ident) or {'id':ident,'name':rule['label'],'type':'formula',
            'formal':rule['before_variants'][0],'occurrences':[{'unit_id':rule['origin_unit_id']}],
            'source_quotes':[rule['before_variants'][0]]})
    given['C-0256']=deepcopy(real['C-0256']);before=deepcopy(given)
    corrected,report=correct_global_concepts(given)
    assert len(report['changes'])==5 and not report['signature_mismatches'] and given==before
    for ident,entry in corrected.items():
        for field in ['id','occurrences','source_quotes']:assert entry[field]==before[ident][field]
        assert entry['formal']!=before[ident]['formal']
    repeated,same=correct_global_concepts(corrected)
    assert repeated==corrected and same['changes']==[]


def test_corrected_global_mirrors_render_in_the_real_legacy_concept_drawer(book):
    import subprocess
    entries={e['id']:e for e in book['units']['CH01']['reader_document']['reference_catalog']['entries']}
    # Match the real global CONCEPT_COPY_FIELDS shape, using exact frozen formal text.
    given={r['concept_id']:{'id':r['concept_id'],'name':r['label'],'type':'formula',
                           'formal':entries[r['concept_id']]['markdown'],
                           'occurrences':[{'unit_id':r['origin_unit_id']}]} for r in CANONICAL_CONCEPT_CORRECTIONS}
    corrected,report=correct_global_concepts(given)
    assert len(report['changes'])==4
    script=r'''
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom');
const concepts=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve('blocks/render');
const dom=new JSDOM(fs.readFileSync(path.join(root,'templates/index.html'),'utf8'),{url:'https://reader.invalid/#/U',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,d=w.document;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=function(){};
const body=Object.values(concepts).map(c=>'[[CONCEPT:'+c.id+'|'+c.name+']]').join('\n\n');
w.__DATA__={book:'global-mirror-test',scope:['U'],units:{U:{title:'全局概念镜像',body_markdown:body}},tree:[{id:'U',title:'全局概念镜像',unit:true}],concepts};
for(const f of ['vendor/marked.min.js','vendor/katex.min.js','assets/reader-markdown.js','assets/render-core.js','assets/reader-layout.js','assets/app.js'])w.eval(fs.readFileSync(path.join(root,f),'utf8'));
for(const ident of Object.keys(concepts)){
 const link=d.querySelector('#content [data-concept-id="'+ident+'"]');assert.ok(link,ident+' global link exists');link.click();
 w.dispatchEvent(new w.HashChangeEvent('hashchange'));
 const drawer=d.getElementById('drawer-body');assert.match(drawer.textContent,new RegExp(concepts[ident].name));
 const math=[...drawer.querySelectorAll('annotation')].map(a=>a.textContent).join('\n');
 if(ident==='C-0796')assert.ok(math.includes('p\\sqrt{p+a}'));
 if(ident==='C-0522')assert.ok(math.includes('n\\theta'));
 if(ident==='C-0219')assert.match(drawer.textContent,/整个闭圆盘/);
 if(ident==='C-0175')assert.ok(math.includes('n+1/2'));
 assert.equal(drawer.querySelectorAll('.katex-error').length,0);
}
dom.window.close();console.log('4 corrected canonical mirrors opened in the real global concept drawer');
'''
    proc=subprocess.run(['node','-e',script],cwd=ROOT,input=json.dumps(corrected,ensure_ascii=False),text=True,capture_output=True)
    assert proc.returncode==0,proc.stdout+'\n'+proc.stderr
