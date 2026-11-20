"""Real HTML and browser routes for source-only materials; no hosted models."""
from copy import deepcopy
import functools
import hashlib
import http.server
import importlib
import json
from pathlib import Path
import subprocess
import threading
import pytest

from test_source_materials import book, save
from support.content_case import teaching_case
from aiforstudy.core.source_materials import write_source_materials


def test_materials_and_inline_exercises_reach_html_and_browser(book, tmp_path):
    root, _, _ = book
    material_index = write_source_materials(root)
    chapter_rows = [dict(id='CH01', anchor='1', title='向量', sections=[dict(id='CH01-S01',anchor='1.1',title='线性运算')]),
                    dict(id='CH03',anchor='3',title='矩阵')]
    units = [dict(unit_id='CH01-S01',chapter='CH01',section='CH01-S01',title='线性运算',original_text='U.md'),
             dict(unit_id='CH03',chapter='CH03',title='矩阵',original_text='U.md')]
    save(root/'chapters.json', dict(book={'title':'离线目录资料验收'},chapters=chapter_rows,lesson_units=units))
    (root/'U.md').write_text('理论正文。\n习题\n1. 保留原题，不生成解答。\n')
    lesson_dir=root/'lessons'; (lesson_dir/'lessons').mkdir(parents=True)
    contents=[]
    for unit in units:
        content,_=teaching_case()
        content.update(unit_id=unit['unit_id'],title=unit['title'],original_text='U.md')
        raw='习题\n1. 保留原题，不生成解答。 [[NOTE:book-literal]]\n'
        content['exercise_regions']=[dict(id='ER-'+unit['unit_id'],title='原书习题',kind='exercise',
            source_lines=[2,3],raw_markdown=raw,source_sha256='sha256:'+hashlib.sha256(raw.encode()).hexdigest())]
        save(lesson_dir/'lessons'/f"{unit['unit_id']}.json",content)
        contents.append(content)
    out=root/'html'
    from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
    from aiforstudy.core.digest import digest_of
    from aiforstudy.core.jsonio import write_json_atomic
    run=root/'run'
    write_json_atomic(run/'state.json',{'book_id':'offline-book','run_id':'run'})
    save(root/'body.json',{'units':[{'toc_node':'T2','body_node':'CH01-S01'},
                                  {'toc_node':'T3','body_node':'CH03'}]})
    for content in contents:
        write_json_atomic(run/f"work/products/lesson/{content['unit_id']}.json",
            {'payload':{'content':content,'content_hash':digest_of(content)}})
        write_json_atomic(run/f"work/products/media_plan/{content['unit_id']}.json",
            {'payload':{'content_hash':digest_of(content),'requests':[],'decisions':[]}})
    report=build_reader_snapshot(run,unit_ids=[u['unit_id'] for u in units],out_dir=out,parse_root=root)
    out=Path(report['output_dir'])
    assert not report['missing']
    data=json.loads((out/'data/data.js').read_text().split('window.__DATA__ = ',1)[1].rstrip().removesuffix(';'))
    assert [n['title'] for n in data['tree']]==['线性运算','综合习题','矩阵','参考答案']
    assert data['source_materials']['material:X1']['raw_markdown']==material_index['materials'][0]['raw_markdown']
    assert set(data['units'])=={'CH01-S01','CH03'}
    handler=functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(out))
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    repo=Path(__file__).resolve().parents[2]
    runtime=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    try:
        result=subprocess.run([str(runtime/'bin/node'),str(repo/'tests/ui/source_material_browser.cjs'),
            str(runtime/'node_modules/playwright'),str(repo/'.devtools/chrome/chrome/chrome-linux64/chrome'),
            f'http://127.0.0.1:{server.server_port}/index.html'],text=True,capture_output=True,timeout=45)
        assert result.returncode==0,result.stdout+'\n'+result.stderr
        observed=json.loads(result.stdout)
        assert observed['errors']==[]
        assert observed['inline'] and observed['standalone'] and observed['back']
    finally:
        server.shutdown();server.server_close();thread.join()
