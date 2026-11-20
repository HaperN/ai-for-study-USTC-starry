"""Only provider I/O is replaced; all production stages build a real reader."""
import json
import pytest
from pathlib import Path

from aiforstudy.core.jsonio import write_json_atomic, read_json
from aiforstudy.pipeline.engine import build_engine, execute
from aiforstudy.pipeline.services import Providers
from support.runner_v5 import setup_book, _request, TeachingTransport


@pytest.mark.parametrize('invalid_first,with_media', [(False,False), (True,False), (False,True)])
def test_legal_json_with_invalid_math_reaches_real_html_without_a_judge(tmp_path, invalid_first, with_media, monkeypatch):
    workspace=setup_book(tmp_path)
    if with_media:
        from aiforstudy.providers.capabilities import resolve_manim_interpreter
        root=Path(__file__).resolve().parents[2]
        monkeypatch.setenv('AIFORSTUDY_MANIM_PYTHON',resolve_manim_interpreter(root))
        caps=read_json(workspace.root/'config/capabilities.json')
        caps['render_environment']['worker_dir']=str(root/'tools/manim_worker')
        write_json_atomic(workspace.root/'config/capabilities.json',caps)
    write_json_atomic(workspace.root/'config/review_mode.json',{'mode':'json_only'})
    class Transport(TeachingTransport):
        def _answer(self, system, user):
            data=json.loads(user)
            if data.get('role_task')=='media_code_generation':
                return json.dumps({'backend':'matplotlib','request_id':data['request']['request_id'],
                    'code':'import matplotlib.pyplot as plt\ndef build_figure(context):\n    fig, ax = plt.subplots()\n    ax.plot([0,1],[0,1])\n    return fig\n',
                    'object_bindings':[], 'steps':[]})
            assert data.get('role_task')=='author', 'no inventory or review model is allowed'
            self.author_calls+=1
            if invalid_first and self.author_calls==1:
                return '{"content":}'
            return json.dumps({'content':{'title':'冻结模式测试', 'book_id':'wrong-book', 'unit_id':'wrong-unit',
                'body_markdown':'这是未经审核的正文，保留 [[CONCEPT:unknown|未知引用]] 后文。',
                'math_models':[{'bad_model':'unreviewed'}], 'timestamp':'optional'},
                'media_requirements':{'decisions':[], 'requests':[{'request_key':'picture', 'kind':'image',
                    'caption':'实际生成的测试配图', 'placement':{'target_pointer':'/body_markdown'}}] if with_media else []}},ensure_ascii=False)
    transport=Transport()
    result,store=execute(build_engine(_request(),workspace,providers=Providers(llm=transport)))
    assert result.status=='succeeded', (result.as_dict(),read_json(store.state_path))
    assert transport.author_calls==(2 if invalid_first else 1) and transport.review_calls==0
    assert not result.scope_accepted and not result.release_ready
    chapters=list((store.work/'legacy/render').glob('*/index.html'))
    assert chapters and chapters[0].stat().st_size>100
    data=(chapters[0].parent/'data/data.js').read_text()
    assert '这是未经审核的正文' in data and '后文。' in data
    if with_media:
        reader=json.loads(data.split('window.__DATA__ = ',1)[1].rsplit(';',1)[0])
        assert any('<svg' in row.get('svg','') and '<path' in row['svg']
                   for row in reader['figures'].values()), 'real worker SVG must be embedded in the reader'
        assert '实际生成的测试配图' in data
    state=store.load_state()
    if not invalid_first:
        assert not state.get('repair_states'), 'non-JSON review must not schedule a rewrite'
    else:
        assert state.get('repair_states'), 'JSON syntax still uses the existing repair loop'
