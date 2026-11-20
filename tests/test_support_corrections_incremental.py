"""Only the new V4 rules and their verified counterexamples; no full-suite run."""
from copy import deepcopy
import cmath
import json
import math
import os
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.prepare_support_corrections import CANONICAL_CONCEPT_CORRECTIONS,EXACT_BLOCK_CORRECTIONS,correct_support_document,correct_global_concepts
from tools.upgrade_study_reader import load_reader_data

@pytest.fixture(scope='module')
def source():
 path = os.environ.get("AIFORSTUDY_SUPPORT_CORRECTIONS_FIXTURE")
 if not path:
  pytest.skip("Requires the original pre-correction reader fixture; set AIFORSTUDY_SUPPORT_CORRECTIONS_FIXTURE explicitly.")
 return load_reader_data(path)

def changed_concept(source,ident):
 rule=next(r for r in CANONICAL_CONCEPT_CORRECTIONS if r['concept_id']==ident)
 uid=rule['origin_unit_id'];doc=source['units'][uid]['reader_document'];copy,report=correct_support_document(doc,uid)
 assert not report['signature_mismatches']
 entry=next(e for e in copy['reference_catalog']['entries'] if e['id']==ident)
 assert entry['markdown']==rule['after']
 return rule['after']

def changed_note(source,owner,pointer=None):
 rule=next(r for r in EXACT_BLOCK_CORRECTIONS if r['owner_id']==owner and (not pointer or r['json_pointer']==pointer))
 uid=rule['unit_id'];doc=source['units'][uid]['reader_document'];copy,report=correct_support_document(doc,uid)
 assert not report['signature_mismatches']
 old=next(b for b in doc['blocks'] if b['owner_id']==owner and b['json_pointer']==rule['json_pointer'])
 new=next(b for b in copy['blocks'] if b['owner_id']==owner and b['json_pointer']==rule['json_pointer'])
 assert old['raw_text']==rule['before'] and new['raw_text']==rule['after']
 assert old['anchor']==new['anchor'] and old['block_id']==new['block_id']
 assert len(new['source_map'])==len(new['visible_text'])
 return new['raw_text']

def test_integer_power_uses_defined_n_and_numeric_zero(source):
 text=changed_concept(source,'C-0048')
 assert r'e^{\mathrm in\varphi}' in text and '$z^0=1$' in text
 assert cmath.exp(2j*math.pi/2)==pytest.approx(-1)
 assert abs(cmath.exp(1j*math.pi/2)+1)>1

def test_poisson_parameter_scope_is_explicit(source):
 text=changed_concept(source,'C-0288');assert '$0<p<1$' in text
 p=2;old=2*math.pi/(1-p*p)
 assert old<0
 n=4000;h=2*math.pi/n
 integral=h/3*(1/(1-2*p+p*p)*2+sum((4 if k%2 else 2)/(1-2*p*math.cos(k*h)+p*p) for k in range(1,n)))
 assert integral==pytest.approx(2*math.pi/3,abs=1e-10)

def test_rational_formula_excludes_real_poles(source):
 text=changed_concept(source,'C-0580')
 assert '实轴上无极点' in text and r'\deg Q\ge\deg P+2' in text
 # 1/(x²-1) has the degree gap but divergent ordinary integrals at ±1.
 assert abs(-math.atanh(1-1e-6))>abs(-math.atanh(1-1e-3))+3

def test_oscillatory_formula_requires_positive_frequency(source):
 text=changed_concept(source,'C-0788')
 assert '$m>0$' in text and '单极点' in text and r'\deg Q\ge\deg P+1' in text
 m=-1;old_upper=math.pi*math.exp(-m);correct=math.pi*math.exp(-abs(m))
 assert old_upper>correct*7

@pytest.mark.parametrize('ident',['C-0437','C-0438'])
def test_inverse_transform_requires_symmetric_infinite_limit(source,ident):
 text=changed_concept(source,ident);assert r'\lim_{T\to\infty}' in text and r'$\sigma>c$' in text
 # F=1/(p+1), sigma=t=1, finite m=1 is not e^-1.
 N=4000;h=2/N
 def f(w):return (cmath.exp(1+1j*w)/(2+1j*w)).real
 truncated=h/3*(f(-1)+f(1)+sum((4 if i%2 else 2)*f(-1+i*h) for i in range(1,N)))/(2*math.pi)
 assert truncated==pytest.approx(.3982493929,abs=1e-9)
 assert abs(truncated-math.exp(-1))>.03

def test_equal_laurent_radii_allow_common_boundary_points(source):
 text=changed_note(source,'CONVERGENCE-ANNULUS');assert '$R=r$' in text and '同时收敛' in text
 partial=2*sum(1/(n*n) for n in range(1,10001))
 assert partial<math.pi**2/3<partial+2/10000
 # The learning-check says no common OPEN domain, which is correct and must stay.
 doc=source['units']['CH05-S04']['reader_document'];copy,_=correct_support_document(doc,'CH05-S04')
 old=next(b for b in doc['blocks'] if b['owner_id']=='CHECK-ANNULUS-EXPLAIN' and b['json_pointer'].endswith('/answer'))
 new=next(b for b in copy['blocks'] if b['block_id']==old['block_id']);assert old==new

def test_example_duplicate_product_zero_rule_is_fixed(source):
 text=changed_note(source,'E17','/examples/2/pitfalls/3')
 assert '乘积必趋于 $0$' in text
 assert abs(1e-9*3)<1e-8

@pytest.mark.parametrize('owner,pointer',[('NOTE-REAL-DECOMP','/annotations/3/markdown'),('EXP-PARTIAL-FRACTION','/explainables/0/markdown')])
def test_repeated_real_quadratic_inverse_contains_time_polynomials(source,owner,pointer):
 text=changed_note(source,owner,pointer);assert 'P_{s-1}' in text and '次数至多 $s-1$' in text
 # f=(sin t-t cos t)/2 cannot be A cos t+B sin t: f(0)=0 but f(2pi)=-pi.
 assert (.5*(math.sin(2*math.pi)-2*math.pi*math.cos(2*math.pi)))==pytest.approx(-math.pi)

def test_combined_joukowski_radicals_keep_correlated_branch(source):
 z=1+1j;P=4*z**4+17*z**2+4;q=1+9*z*z/(4*(z*z+1)**2)
 naive=cmath.sqrt(P)/(2*(z*z+1));arg=cmath.phase(q)%(2*math.pi)
 composed=abs(q)**.5*cmath.exp(.5j*arg)
 assert naive.imag<-.2 and composed.imag>.2 and composed==pytest.approx(-naive)
 for pointer in ['/examples/0/steps/3/markdown','/examples/0/steps/3/basis','/examples/0/steps/4/markdown','/examples/0/steps/4/basis','/examples/0/conclusion','/examples/0/key_points/2']:
  text=changed_note(source,'EX-ZHUKOVSKY-8',pointer)
  assert any(term in text for term in ['arg','解析支','角支','复合支'])

def test_incremental_canonical_copies_and_global_na_are_precise(source):
 before=deepcopy(source);ids={'C-0048','C-0288','C-0580','C-0788','C-0437','C-0438'};count=0
 for uid,u in source['units'].items():
  copy,report=correct_support_document(u['reader_document'],uid)
  assert not report['signature_mismatches'] and report['anchors_preserved'] and report['owner_ids_preserved']
  for rule in CANONICAL_CONCEPT_CORRECTIONS:
   if rule['concept_id'] in ids:
    e=next(e for e in copy['reference_catalog']['entries'] if e['id']==rule['concept_id']);assert e['markdown']==rule['after'];count+=1
  again,rep=correct_support_document(copy,uid);assert again==copy and rep['changes']==[]
 assert count==76*6 and source==before
 actual=load_reader_data(ROOT.parent/'delivery/复变函数-全书重讲版-20261029/book')['concepts']
 corrected,report=correct_global_concepts(actual);assert corrected==actual=={} and report['changes']==[]
 # Synthetic mirrors use exact observed canonical values and the real global schema.
 given={r['concept_id']:{'id':r['concept_id'],'name':r['label'],'formal':r['before_variants'][0],
                         'occurrences':[{'unit_id':r['origin_unit_id']}], 'source_quotes':[r['before_variants'][0]]}
        for r in CANONICAL_CONCEPT_CORRECTIONS if r['concept_id'] in ids}
 original=deepcopy(given);corrected,report=correct_global_concepts(given)
 assert len(report['changes'])==6 and given==original
 for ident,row in corrected.items():
  assert row['source_quotes']==given[ident]['source_quotes'] and row['occurrences']==given[ident]['occurrences']

def test_delegated_parameter_integral_keeps_outer_i_separate(source):
 text=changed_note(source,'EX2-PITFALL')
 assert '$J_1=2\\pi$' in text and '$I_1=2\\pi i$' in text
 assert 2*math.pi != 2j*math.pi

def test_delegated_slit_and_primitive_conditions_are_scoped(source):
 slit=changed_note(source,'MULTIPLY-CONNECTED-EXAMPLES')
 assert '不一定破坏单连通' in slit and '半带' in slit
 # Selected Log and exp are mutually inverse on the indicated convex strip.
 for u in [-2,-.5,1]:
  for theta in [-2,0,2]:assert cmath.log(cmath.exp(u+1j*theta))==pytest.approx(u+1j*theta)
 text=changed_note(source,'PITFALL-NL-CONDITION')
 assert '充分条件' in text and '不是' in text and '$H(z)=z^2$' in text
 assert (1.5j)**2-1.5**2==pytest.approx(-4.5)
 changed_note(source,'EX-CALC-INTEGRALS','/examples/0/key_points/0')
 changed_note(source,'EX-MULTIVALUED-INTEGRAL','/examples/1/conclusion')
 doc=source['units']['CH03-S03']['reader_document'];copy,report=correct_support_document(doc,'CH03-S03')
 # Correct sufficient-condition canonical references are deliberately unchanged.
 for ident in ['C-0213','C-0537']:
  old=next(e for e in doc['reference_catalog']['entries'] if e['id']==ident)
  new=next(e for e in copy['reference_catalog']['entries'] if e['id']==ident);assert old==new
