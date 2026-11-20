"""Independent source formatting never reauthors a lesson or certifies answers."""
import importlib
import importlib.util
import json
from types import SimpleNamespace

import pytest

from aiforstudy.core.digest import digest_bytes
from aiforstudy.core.jsonio import write_json_atomic, read_json
from aiforstudy.errors import Cancelled
from aiforstudy.pipeline.graph import Node, build_graph
from test_content_generation_v3 import context
from contracts.test_m2_graph import make_plan
from aiforstudy.contracts.enums import Goal


def stage_module():
    assert importlib.util.find_spec('aiforstudy.pipeline.source_format_stage'), 'missing formatting stage'
    return importlib.import_module('aiforstudy.pipeline.source_format_stage')


def material(identity='material:A', raw='# 习题参考答案\n1. $z=1$。\n'):
    return {'id': identity, 'title': '习题参考答案', 'kind': 'answers', 'state': 'recorded',
            'raw_markdown': raw, 'source_sha256': digest_bytes(raw.encode()), 'source_lines': [10,12]}


def setup(tmp_path, materials):
    ctx=context(tmp_path)
    ctx.node=Node('source_format','source_format','book',requires=('body',),required=False)
    write_json_atomic(ctx.context.parse/'source_materials.json', {'materials':materials})
    return ctx


class Client:
    def __init__(self, fail=None): self.calls=[]; self.fail=fail
    def chat_json(self, messages, **kwargs):
        self.calls.append((messages,kwargs))
        if self.fail: raise self.fail
        return {'data':{'display_markdown':'# 习题参考答案\n\n1. $z = 1$。','notes':[]}}


def test_book_formatting_can_run_beside_lessons_and_all_renders_wait_for_it():
    graph=build_graph(make_plan(('CH01-S01','CH02-S01'))).by_id()
    assert 'source_format' in graph
    assert graph['source_format'].requires == ('body',)
    assert 'source_format' not in graph['lesson:CH01-S01'].requires
    assert all('source_format' in graph[key].requires for key in ('render:CH01','render:CH02'))
    assert 'source_format' not in build_graph(make_plan(('CH01-S01',),goal=Goal.PREPARE)).by_id()


def test_format_is_saved_once_and_original_source_stays_unchanged(tmp_path,monkeypatch):
    stage=stage_module(); row=material();ctx=setup(tmp_path,[row]); client=Client()
    monkeypatch.setattr(stage,'_gateway_client',lambda *_a,**_k:(client,'offline'))
    source=ctx.context.parse/'source_materials.json'; before=source.read_bytes()
    first=stage.source_format_stage(ctx)
    assert first.execution_state.value=='succeeded'
    assert first.quality_state.value=='not_checked' and not first.repair_requests
    output=read_json(tmp_path/'work/products/source_format/book.json')['payload']['materials'][row['id']]
    assert output['status']=='formatted' and 'z = 1' in output['display_markdown']
    assert output['source_sha256']==row['source_sha256'] and source.read_bytes()==before
    stage.source_format_stage(ctx)
    assert len(client.calls)==1
    assert client.scope_kind=='run' and client.resource_id==row['id']


def test_material_already_in_a_lesson_is_not_sent_again(tmp_path,monkeypatch):
    stage=stage_module();row=material();ctx=setup(tmp_path,[row])
    with (ctx.context.parse/'source.md').open('a') as f:f.write('\n'+row['raw_markdown'])
    client=Client();monkeypatch.setattr(stage,'_gateway_client',lambda *_a,**_k:(client,'offline'))
    stage.source_format_stage(ctx)
    output=read_json(tmp_path/'work/products/source_format/book.json')['payload']['materials'][row['id']]
    assert client.calls==[] and output['status']=='original'
    assert output['reason']=='already_in_lesson'


def test_missing_ranges_and_empty_materials_do_not_invent_content(tmp_path,monkeypatch):
    stage=stage_module();ctx=setup(tmp_path,[{**material(),'state':'unresolved','raw_markdown':''}])
    client=Client();monkeypatch.setattr(stage,'_gateway_client',lambda *_a,**_k:(client,'offline'))
    result=stage.source_format_stage(ctx)
    assert result.execution_state.value=='succeeded' and client.calls==[]
    assert read_json(tmp_path/'work/products/source_format/book.json')['payload']['materials']['material:A']['status']=='original'


def test_model_failure_keeps_original_and_does_not_repeat_or_create_review(tmp_path,monkeypatch):
    stage=stage_module();ctx=setup(tmp_path,[material()]);client=Client(RuntimeError('transport stopped'))
    monkeypatch.setattr(stage,'_gateway_client',lambda *_a,**_k:(client,'offline'))
    result=stage.source_format_stage(ctx)
    assert result.execution_state.value=='succeeded' and result.quality_state.value=='not_checked'
    output=read_json(tmp_path/'work/products/source_format/book.json')['payload']['materials']['material:A']
    assert output['status']=='original' and 'transport stopped' in output['reason']
    assert not result.repair_requests
    stage.source_format_stage(ctx)
    assert len(client.calls)==1


def test_cancel_is_not_reported_as_completed_formatting(tmp_path,monkeypatch):
    stage=stage_module();ctx=setup(tmp_path,[material()]);client=Client(Cancelled('stop'))
    monkeypatch.setattr(stage,'_gateway_client',lambda *_a,**_k:(client,'offline'))
    with pytest.raises(Cancelled):stage.source_format_stage(ctx)


def test_changed_source_gets_a_new_format_instead_of_previous_text(tmp_path,monkeypatch):
    stage=stage_module();ctx=setup(tmp_path,[material()]);client=Client()
    monkeypatch.setattr(stage,'_gateway_client',lambda *_a,**_k:(client,'offline'))
    stage.source_format_stage(ctx)
    row=material(raw='# 参考答案\n2. $z=2$。\n')
    write_json_atomic(ctx.context.parse/'source_materials.json',{'materials':[row]})
    stage.source_format_stage(ctx)
    assert len(client.calls)==2
    assert read_json(tmp_path/'work/products/source_format/book.json')['payload']['materials'][row['id']]['source_sha256']==row['source_sha256']
