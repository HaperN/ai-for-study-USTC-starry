from types import SimpleNamespace
from test_source_inventory import context, response
import pytest
from aiforstudy.errors import ContractError


def test_source_cache_revalidates_implementation_and_rejects_changed_frozen_source(tmp_path,monkeypatch):
    from aiforstudy.pipeline import content_stages as stages
    source=context()
    monkeypatch.setattr(stages,'_structural_context',lambda ctx: source)
    monkeypatch.setattr(stages,'_root',lambda ctx:tmp_path)
    ctx=SimpleNamespace(node=SimpleNamespace(scope_key='U'))
    class Client:
        calls=0
        def chat_json(self,*args,**kwargs):
            self.calls+=1
            return {'data':response(source,extra=False)}
    client=Client()
    refined=stages._prepare_source_context(ctx,client)
    assert client.calls==1 and refined['obligation_extraction_status']=='semantic_verified'
    assert stages._frozen_context(ctx)==refined
    assert stages._prepare_source_context(ctx,client)==refined and client.calls==1
    monkeypatch.setattr(stages,'_inventory_identity',lambda:{'current':'changed-prompt-or-extractor'})
    # D16 修复：实现指纹漂移**不等于**已有语义盘点作废。原实现直接退回 structural_only，
    # 后果是该单元既存正文的 source_context_hash 与当前 frozen 上下文不一致，
    # media_plan 报"内容来源已变化"并连带 blocked 一串节点（实测 run-20000101-000000-5d7b83）。
    # 现在判据不变（仍逐条重放校验），处置改成"能重放就照旧用语义版"。
    assert stages._frozen_context(ctx)==refined, '实现变更后仍应重放冻结响应，而不是丢掉语义'
    assert stages._frozen_context(ctx)['obligation_extraction_status']=='semantic_verified'
    stages._prepare_source_context(ctx,client)
    assert client.calls==1, 'implementation changes revalidate saved responses before any new call'
    source=context('新来源。')
    assert stages._frozen_context(ctx)['obligation_extraction_status']=='structural_only'
    with pytest.raises(ContractError, match='frozen input'):
        stages._prepare_source_context(ctx,client)
    assert client.calls==1, 'changed source needs a new frozen run, not a reset correction budget'


def test_invalid_inventory_response_survives_without_becoming_current_cache(tmp_path,monkeypatch):
    from aiforstudy.pipeline import content_stages as stages
    monkeypatch.setattr(stages,'_structural_context',lambda ctx:context())
    monkeypatch.setattr(stages,'_root',lambda ctx:tmp_path)
    ctx=SimpleNamespace(node=SimpleNamespace(scope_key='U'))
    class Client:
        def chat_json(self,*a,**k):return {'data':{'accepted':True}}
    with pytest.raises(ContractError):stages._prepare_source_context(ctx,Client())
    assert list(tmp_path.glob('source_context/U/responses/*.json'))
    assert stages._frozen_context(ctx)['obligation_extraction_status']=='structural_only'
