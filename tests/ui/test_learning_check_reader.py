from support.reader_site import write_reader_fixture
"""Real Chromium interaction: attempt → progressive hints → folded solution."""
import importlib.util
import json
import subprocess
from pathlib import Path
import pytest
from test_learning_checks import check_fixture
from test_content_v4_reader_contract import candidate, catalog


@pytest.fixture(scope='module')
def reader_results(tmp_path_factory):
    work=tmp_path_factory.mktemp('q05-reader')
    root=Path(__file__).resolve().parents[2]
    spec=importlib.util.spec_from_file_location('q05_render_build',root/'blocks/render/build.py')
    build=importlib.util.module_from_spec(spec); spec.loader.exec_module(build)
    parse=work/'parse'; lessons=work/'lesson'/'lessons'; parse.mkdir(); lessons.mkdir(parents=True)
    (parse/'chapters.json').write_text(json.dumps({'book':{'title':'自测演示'},'chapters':[{'id':'U','title':'标题'}],'lesson_units':[{'unit_id':'U','title':'标题','original_text':'U.md'}]}))
    (parse/'concepts.json').write_text(json.dumps({'concepts':[{'id':'C','name':'冻结概念名','type':'definition'}]}))
    (parse/'U.md').write_text('原文')
    c=candidate(); c['original_text']='U.md'; c['learning_checks']=[]
    for kind in ('choice','boolean','numeric','key_step','self_explanation'):
        row=check_fixture(kind); row['id']=kind
        if kind=='numeric': row['scoring']['absolute_tolerance']=0.25
        if kind=='key_step': row['scoring']={'accepted_inputs':['divide by 2','fl','strasse','σ'],'normalization':'casefold'}
        if row['test_cases']: row['test_cases'][0]['id']='PRIVATE-REGRESSION-SECRET'
        c['learning_checks'].append(row)
    (lessons/'U.json').write_text(json.dumps(c)); out=work/'out'
    write_reader_fixture([c],catalog(),out)
    assert 'PRIVATE-REGRESSION-SECRET' not in (out/'data/data.js').read_text()
    chrome=root/'.devtools/chrome/chrome/chrome-linux64/chrome'
    assert chrome.is_file(), 'actual Chromium required'
    runtime=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    proc=subprocess.run([str(runtime/'bin/node'),str(root/'tests/ui/learning_check_reader_runner.js'),
        str(runtime/'node_modules/playwright'),str(chrome),(out/'index.html').as_uri(),str(work)],
        capture_output=True,text=True,timeout=60)
    assert proc.returncode==0, proc.stderr[-4000:]
    results={int(width):value for width,value in json.loads(proc.stdout).items()}
    return results


@pytest.mark.parametrize('width', [1280,390])
@pytest.mark.parametrize('kind', ['choice','boolean','numeric','key_step','self_explanation'])
def test_actual_clicks_progressive_hint_folded_solution_and_feedback(reader_results,width,kind):
    result=reader_results[width][kind]
    assert 'missing' not in result, result
    assert result['viewport']==width
    for key in ('initialHidden','initialHintDisabled','blankInvalid','blankDidNotUnlock','wrongSpecific','afterAttemptFolded','firstHint','secondHint','solutionVisible','oldFeedbackHidden','noOverflow'):
        assert result[key], (width,kind,key,result)
    assert result['status']==('manual' if kind=='self_explanation' else 'incorrect')
    assert result['finalStatus']==('manual' if kind=='self_explanation' else 'correct')


@pytest.mark.parametrize('width',[1280,390])
def test_browser_scoring_numeric_boundaries_and_unicode_match_host(reader_results,width):
    result=reader_results[width]['grading']
    assert result['numeric']==['correct','correct','incorrect','invalid','invalid','invalid','invalid']
    assert result['key_step']==['correct','correct','correct','correct']
