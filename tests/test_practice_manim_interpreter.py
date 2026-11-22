import json,os,sys
from contextlib import nullcontext
from tools import render_practice_media

def test_explicit_venv_interpreter_keeps_its_venv_path(tmp_path,monkeypatch):
    interpreter=tmp_path/'venv/bin/python';interpreter.parent.mkdir(parents=True);interpreter.symlink_to(sys.executable)
    specs=tmp_path/'specs.json';specs.write_text(json.dumps([{'id':'example','program':{},'context':{}}]))
    seen=[]
    monkeypatch.setenv('AIFORSTUDY_MANIM_PYTHON','before-test')
    monkeypatch.setattr(render_practice_media,'review_mode',lambda *a:nullcontext())
    monkeypatch.setattr(render_practice_media,'render_generated_code',lambda *a,**k:(seen.append(os.environ['AIFORSTUDY_MANIM_PYTHON']) or {'status':'needs_review'}))
    monkeypatch.setattr(sys,'argv',['render','--specs',str(specs),'--out',str(tmp_path/'out'),'--manim-python',str(interpreter)])
    render_practice_media.main()
    assert seen==[str(interpreter)]
