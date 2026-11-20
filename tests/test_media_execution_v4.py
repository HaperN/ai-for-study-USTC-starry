from pathlib import Path


import pytest


from aiforstudy.core.digest import digest_of,digest_file


from aiforstudy.errors import ContractError,CapabilityError


def test_manifest_rejects_extra_or_corrupt_resource(tmp_path):
    from aiforstudy.blocks.media.media_manifest import validate_manifest
    source=tmp_path/'a.svg'; source.write_text('<svg/>')
    plan={'content_hash':'C','requests':[{'request_id':'R','kind':'svg'}]}
    manifest={'schema_version':'MediaManifestV1','content_hash':'C','requirements_hash':digest_of(plan),'requests':[{'request_id':'R','kind':'svg','state':'fulfilled','resources':[{'relative_path':'a.svg','sha256':digest_file(source)}],'source_dir':str(tmp_path),'review':{'accepted':True,'image_hash':'fake'}}]}
    assert not validate_manifest(plan,manifest)['ok']
    manifest['requests'].append({'request_id':'extra','state':'fulfilled'})
    assert not validate_manifest(plan,manifest)['ok']


def test_composition_namespaces_identical_backend_filenames(tmp_path):
    from aiforstudy.pipeline.media_stages import _copy_static_resources
    copied=[]
    for unit,rid,value in [('U1','R1','<svg>one</svg>'),('U2','R2','<svg>two</svg>')]:
        base=tmp_path/unit;base.mkdir();p=base/'figure.svg';p.write_text(value)
        row={'request_id':rid,'source_dir':str(base),'svg':'figure.svg','entry':{'id':'FIG-'+rid,'svg':'figures/figure.svg'},'resources':[{'relative_path':'figure.svg','sha256':digest_file(p)}]}
        entry,paths=_copy_static_resources(row,unit,tmp_path/'combined/figures')
        target=Path(paths['figure.svg']);copied.append(target)
        assert target.read_text()==value and entry['svg']=='figures/'+unit+'/'+rid+'/figure.svg'
    assert copied[0]!=copied[1]



def test_retired_media_or_writer_protocol_cannot_be_registered():
    import pytest
    from aiforstudy.errors import ContractError
    from aiforstudy.pipeline.registry import release_registry
    for version in ('3.0','4.0'):
        with pytest.raises(ContractError,match='只读'):release_registry(graph_version=version)
