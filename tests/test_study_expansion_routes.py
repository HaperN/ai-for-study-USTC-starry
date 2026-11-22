from urllib.parse import quote
from test_integrated_study import prepare
from test_shared_annotations_http import call
from aiforstudy.integrated.config import load_config
from aiforstudy.integrated.server import running

def test_three_learning_entries_and_only_declared_unit_pages_are_served(tmp_path):
    config=prepare(tmp_path)
    for name in ('日常巩固.html','押题.html'):(tmp_path/'practice'/name).write_text('<main>公开学习页</main>')
    folder=tmp_path/'practice/章节练习';folder.mkdir()
    (folder/'CH01.html').write_text('<main>单元练习</main>')
    (folder/'private.html').write_text('PRIVATE')
    with running(load_config(config)) as server:
        text=call(server,path='/start')[2].decode()
        for label in ('教材解析','日常巩固','押题'):assert label in text
        for name in ('日常巩固.html','押题.html','章节练习/CH01.html'):
            assert call(server,path=quote('/practice/'+name))[0]==200
        assert call(server,path=quote('/practice/章节练习/private.html'))[0]==404
        assert call(server,path=quote('/practice/章节练习/CH99.html'))[0]==404
