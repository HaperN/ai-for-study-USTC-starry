"""The actual Runner identity includes routing/bridge source bytes."""
import pytest
from aiforstudy.pipeline.runner import Runner
from support.runtime_tree import copy_runtime


@pytest.mark.parametrize('component',['factory.py','routing.py','local_compatible.py','dsh_account.py','dsh_bridge.cjs'])
def test_transport_edit_invalidates_author_media_and_delivery_stages(tmp_path,monkeypatch,component):
    package=tmp_path/'package';copy_runtime(package)
    runner=object.__new__(Runner)
    monkeypatch.setattr(runner,'_runtime_package_root',lambda:package)
    stages=('lesson','text_audit','figures','audit','render','delivery')
    before={stage:runner._stage_runtime_identity(stage) for stage in stages}
    path=package/'providers'/component
    path.write_bytes(path.read_bytes()+b'\n// offline identity probe\n')
    assert all(runner._stage_runtime_identity(stage)!=before[stage] for stage in stages)
