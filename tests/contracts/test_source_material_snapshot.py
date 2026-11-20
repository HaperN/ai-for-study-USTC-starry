"""Production snapshot carries literal materials and source image dependencies."""
import json
from pathlib import Path
import shutil

import pytest

from test_source_materials import book, save
from test_diagnostic_preview_boundary import run_fixture
from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
from aiforstudy.core.digest import digest_file, digest_of
from aiforstudy.core.source_materials import write_source_materials


def data_at(report):
    return json.loads((Path(report['output_dir'])/'data/data.js').read_text().split('window.__DATA__ = ', 1)[1].rstrip().removesuffix(';'))


@pytest.mark.parametrize('external', [False, True])
def test_actual_snapshot_packages_source_materials_and_invalidates_image_changes(book, tmp_path, external):
    original, _, _ = book
    run = run_fixture(tmp_path)
    parse = tmp_path/'shared-parse' if external else run/'work/prepared-parse'
    parse.mkdir(parents=True)
    for p in list(original.iterdir()):
        if p.is_file():
            shutil.copyfile(p, parse/p.name)
    image = parse/'images/source.png'; image.parent.mkdir()
    from PIL import Image
    Image.new('RGB', (2,2), 'red').save(image)
    raw = (parse/'document.md').read_bytes().decode().replace('资料题目原文。', '资料题目原文。 ![](images/source.png)')
    (parse/'document.md').write_bytes(raw.encode())
    write_source_materials(parse)
    save(parse/'body.json', {'units':[{'toc_node':'T2','body_node':'U'}]})
    body = run/'work/products/body/body.json'; body.parent.mkdir()
    save(body, {'payload':{'source_materials':{'source_dir':str(parse)}}})
    before = {str(p.relative_to(run)):digest_file(p) for p in run.rglob('*') if p.is_file()}
    first = build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'preview')
    data = data_at(first)
    assert data['source_materials']['material:X1']['kind'] == 'exercise'
    assert [n['id'] for n in data['tree']] == ['U','material:X1','material:A1']
    packaged = Path(first['output_dir'])/data['source_assets']['images/source.png']
    assert packaged.read_bytes() == image.read_bytes()
    key = str(image) if external else str(image.relative_to(run))
    assert first['source_hashes'][key] == digest_file(image)
    assert before == {str(p.relative_to(run)):digest_file(p) for p in run.rglob('*') if p.is_file()}
    Image.new('RGB', (2,2), 'blue').save(image)
    second = build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'preview')
    assert second['snapshot_hash'] != first['snapshot_hash']
    assert packaged.read_bytes() != image.read_bytes()
    assert data_at(second)['source_assets'] != data['source_assets']


def test_snapshot_source_only_region_images_work_without_material_chapters(book, tmp_path):
    root, _, _ = book
    run = run_fixture(tmp_path)
    parse = run/'work/prepared-parse'; parse.mkdir()
    from PIL import Image
    Image.new('RGB',(2,2),'green').save(parse/'question.png')
    path=run/'work/products/lesson/U.json'; lesson=json.loads(path.read_text())
    raw='习题\n![](question.png)\n'
    lesson['payload']['content']['exercise_regions']=[dict(id='ER',title='习题',kind='exercise',
        source_lines=[1,2],raw_markdown=raw,source_sha256=digest_of(raw))]
    lesson['payload']['content_hash']=digest_of(lesson['payload']['content'])
    save(path,lesson)
    # Old media is deliberately unavailable after content changes; source images
    # are independent of generated-media approval and remain readable.
    report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'preview')
    data=data_at(report)
    assert 'question.png' in data['source_assets']
    assert (Path(report['output_dir'])/data['source_assets']['question.png']).is_file()


def test_optional_format_product_changes_snapshot_without_changing_review_or_source(book, tmp_path):
    parse, _, _ = book
    manifest = write_source_materials(parse)
    source = next(row for row in manifest['materials'] if row['id'] == 'material:X1')
    run = run_fixture(tmp_path)
    relative = 'work/products/source_format/book.json'
    product = run / relative
    first = build_reader_snapshot(run, unit_ids=['U'], out_dir=tmp_path/'preview', parse_root=parse)
    assert first['source_hashes'][relative] == 'missing'
    assert data_at(first)['source_materials'][source['id']] == source
    product.parent.mkdir()
    save(product, {'_meta': {}, 'payload': {'materials': {source['id']: {
        'source_sha256': source['source_sha256'], 'status': 'formatted',
        'display_markdown': '## 整理后的综合习题\n\n2. 资料题目原文。', 'notes': []}}}})
    second = build_reader_snapshot(run, unit_ids=['U'], out_dir=tmp_path/'preview', parse_root=parse)
    assert data_at(second)['source_materials'][source['id']]['display_markdown'].startswith('## 整理后的')
    assert second['source_hashes'][relative] == digest_file(product)
    assert second['snapshot_hash'] != first['snapshot_hash']
    assert second['review_outcome'] == first['review_outcome']
    assert second['missing'] == first['missing']
    assert second['problems'] == first['problems']

    # Invalid optional bytes still invalidate the old snapshot, but never hide
    # source material or turn formatting into a production blocker.
    product.write_text('{broken formatting')
    third = build_reader_snapshot(run, unit_ids=['U'], out_dir=tmp_path/'preview', parse_root=parse)
    assert third['snapshot_hash'] not in {first['snapshot_hash'], second['snapshot_hash']}
    assert third['source_hashes'][relative] == digest_file(product)
    assert data_at(third)['source_materials'][source['id']] == source
    assert third['review_outcome'] == first['review_outcome']
    assert third['missing'] == first['missing']
    assert third['problems'] == first['problems']
    assert data_at(second)['source_materials'][source['id']]['raw_markdown'] == source['raw_markdown']


def test_external_snapshot_accepts_image_reference_repaired_by_current_formatting(book, tmp_path):
    from PIL import Image
    from aiforstudy.blocks.render.snapshot_sources import validate_snapshot_sources
    parse, _, _ = book
    image = parse/'images/fixed.png'
    image.parent.mkdir()
    Image.new('RGB', (2, 2), 'green').save(image)
    document = parse/'document.md'
    document.write_text(document.read_text().replace('资料题目原文。',
        '资料题目原文。 ! [题图](images/fixed.png)'))
    manifest = write_source_materials(parse)
    source = next(row for row in manifest['materials'] if row['id'] == 'material:X1')
    run = run_fixture(tmp_path)
    product = run/'work/products/source_format/book.json'
    product.parent.mkdir()
    save(product, {'_meta': {}, 'payload': {'materials': {source['id']: {
        'source_sha256': source['source_sha256'], 'status': 'formatted',
        'display_markdown': source['raw_markdown'].replace('! [题图]', '![题图]'), 'notes': []}}}})

    snapshot = build_reader_snapshot(run, unit_ids=['U'], out_dir=tmp_path/'preview', parse_root=parse)

    data = data_at(snapshot)
    assert data['source_materials'][source['id']]['raw_markdown'] == source['raw_markdown']
    assert snapshot['source_hashes'][str(image)] == digest_file(image)
    assert (Path(snapshot['output_dir'])/data['source_assets']['images/fixed.png']).read_bytes() == image.read_bytes()
    assert validate_snapshot_sources(snapshot, run, parse)
