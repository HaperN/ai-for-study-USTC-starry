"""D15: every consumer applies the same resource and SVG boundary."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from aiforstudy.core.digest import digest_file, digest_of
from aiforstudy.errors import ContractError


@pytest.mark.parametrize('text', [
    '<svg xmlns="http://www.w3.org/2000/svg"><animateTransform/></svg>',
    '<!DOCTYPE svg [<!ENTITY label "hello">]><svg/>',
    '<not_svg/>',
    '<svg xmlns:e="urn:test"><g e:onload="alert(1)"/></svg>',
])
@pytest.mark.parametrize('consumer', ['execution', 'validation'])
def test_svg_consumers_reject_the_complete_unsafe_set(tmp_path, text, consumer):
    from importlib import import_module
    path=tmp_path/'bad.svg'; path.write_text(text)
    with pytest.raises(ContractError):
        import_module('aiforstudy.blocks.media.'+consumer)._safe_svg(path)


@pytest.mark.parametrize('damage', ['bytes', 'parent_symlink', 'windows_path'])
def test_every_media_consumer_rejects_invalid_resource_bytes_or_path(tmp_path, damage):
    from aiforstudy.blocks.media.validation import _safe_file
    from aiforstudy.blocks.media.media_manifest import validate_manifest
    from aiforstudy.pipeline.media_stages import _intact_media_resources
    real=tmp_path/'real';real.mkdir();file=real/'frame.png';file.write_bytes(b'frame')
    relative='real/frame.png'
    if damage=='parent_symlink':
        (tmp_path/'alias').symlink_to(real, target_is_directory=True);relative='alias/frame.png'
    elif damage=='windows_path':
        file=tmp_path/'C:\\frame.png';file.write_bytes(b'frame');relative=file.name
    resource=dict(kind='screenshot',relative_path=relative,sha256=digest_file(file),
                  bytes=999 if damage=='bytes' else file.stat().st_size)
    request=dict(request_id='R',kind='source_image')
    requirements=dict(content_hash='C',requests=[request])
    row=dict(request_id='R',kind='source_image',state='fulfilled',request_hash=digest_of(request),
        source_dir=str(tmp_path),resources=[resource],review=dict(image_hash=digest_file(file),
        request_hash=digest_of(request),accepted=True,evidence='current frame',problems=[]))
    manifest=dict(schema_version='MediaManifestV1',content_hash='C',
        requirements_hash=digest_of(requirements),requests=[row])
    assert not _intact_media_resources(row)
    assert not validate_manifest(requirements,manifest)['ok']
    with pytest.raises(ContractError):_safe_file(tmp_path,resource)


def test_runner_and_direct_stage_share_store_fallback(tmp_path):
    from aiforstudy.pipeline.runner import Runner
    from aiforstudy.pipeline.stage_io import _store_root
    workspace=SimpleNamespace(root=tmp_path)
    services=SimpleNamespace(workspace=workspace,store=None)
    runner=object.__new__(Runner);runner.store=None;runner.services=services
    assert runner._store_root()==_store_root(SimpleNamespace(workspace=workspace,services=services))


@pytest.mark.parametrize('path',['C:\\frame.png','asset\\..\\frame.png',' ./frame.png','a//b','a/./b'])
def test_relative_resource_path_has_one_portable_rule(path):
    from aiforstudy.pipeline.resource_binding import _safe_relative as delivery
    from aiforstudy.blocks.visual.result import _safe_relative as animation
    assert not delivery(path) and not animation(path)

@pytest.mark.parametrize('fragment', [
    r'<style>@\69mport "https://example.invalid/remote.css";</style>',
    r'<g style="fill:u\72l(https://example.invalid/p.svg#p)"/>',
    r'<g style="background-image:image-set(&quot;https://example.invalid/p.png&quot; 1x)"/>',
    '<g xml:base="https://example.invalid/p.svg"><use href="#x"/></g>',
])
def test_shared_svg_rejects_browser_interpreted_external_references(fragment):
    from aiforstudy.blocks.media.svg_safety import validate_svg_source
    with pytest.raises(ContractError):
        validate_svg_source('<svg xmlns="http://www.w3.org/2000/svg">'+fragment+'</svg>')


def test_shared_svg_keeps_plain_local_css_fragments():
    from aiforstudy.blocks.media.svg_safety import validate_svg_source
    source=r'<svg xmlns="http://www.w3.org/2000/svg"><g style="fill:url(#local)"/></svg>'
    assert validate_svg_source(source)==source


def test_real_store_does_not_require_workspace_on_inspection_context(tmp_path):
    from types import SimpleNamespace
    from aiforstudy.pipeline.stage_io import _store_root
    from aiforstudy.pipeline.runner import Runner
    store=SimpleNamespace(work=tmp_path/'actual-work')
    context=SimpleNamespace(services=SimpleNamespace(store=store))
    runner=object.__new__(Runner)
    runner.services=SimpleNamespace()
    runner.store=store
    assert _store_root(context)==runner._store_root()==store.work


@pytest.mark.parametrize('value', [
    'url(&quot;/*asset*/#paint&quot;)',
    r'url(\22#paint)',
    'url(/**/#paint)',
])
def test_shared_svg_rejects_ambiguous_css_without_rewriting_url_text(value):
    from aiforstudy.blocks.media.svg_safety import validate_svg_source
    with pytest.raises(ContractError):
        validate_svg_source('<svg xmlns="http://www.w3.org/2000/svg"><g style="fill:'+value+'"/></svg>')
