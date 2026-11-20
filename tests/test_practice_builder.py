from pathlib import Path
import json
import pytest
from tools.build_practice_delivery import build
from test_practice_publication_guards import bound

def setup(tmp):
 q=bound(tmp);q['basic']=True;q['anchor_text']='正文锚点'
 c=tmp/'catalog.json';c.write_text(json.dumps([q]))
 p=tmp/'papers.json';p.write_text('[]')
 b=tmp/'reader';b.mkdir();(b/'index.html').write_text('<body></body>');(b/'data').mkdir();(b/'data/data.js').write_text('window.__DATA__={"units":{"CH01-S01-SS04":{}}};');(b/'assets/fonts').mkdir(parents=True)
 for n in ['marked.min.js','katex.min.js','katex.min.css','reader-markdown.js','study-math.js','render-core.js','audible-audio.js','animations.js']:(b/'assets'/n).write_text('')
 return q,c,p,b

def test_output_cannot_be_inside_reader(tmp_path):
 q,c,p,b=setup(tmp_path)
 with pytest.raises(ValueError,match='overlap'):build(c,p,b,b/'child')
 assert not (b/'child').exists()

def test_additions_cannot_bypass_profile_or_review(tmp_path):
 q,c,p,b=setup(tmp_path);a=tmp_path/'extra.json';a.write_text(json.dumps([q]))
 with pytest.raises(ValueError,match='profile'):build(c,p,b,tmp_path/'out',additions=a)

def test_unknown_reader_mount_rejected_before_writing(tmp_path):
 q,c,p,b=setup(tmp_path);q['mount_unit_id']='UNKNOWN';c.write_text(json.dumps([q]))
 with pytest.raises(ValueError,match='reader unit'):build(c,p,b,tmp_path/'out')
 assert not (tmp_path/'out').exists()

def test_real_exam_sources_required(tmp_path):
 q,c,p,b=setup(tmp_path)
 with pytest.raises(ValueError,match='originals'):build(c,p,b,tmp_path/'out')


def test_moved_delivery_rebuilds_without_old_source_path(tmp_path):
 import hashlib
 q,c,p,b=setup(tmp_path)
 source=tmp_path/'local-exam.pdf';source.write_bytes(b'%PDF-1.4 portable source fixture')
 q['source']['pdf_path']='/nonexistent-old-computer/exam.pdf'
 q=bound(tmp_path,{'source':q['source'],'source_file_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'basic':True,'anchor_text':'正文锚点'})
 c.write_text(json.dumps([q]));m=tmp_path/'originals.json';m.write_text(json.dumps([{'path':str(source),'sha256':q['source_file_sha256'],'course':'复变函数A'}]))
 result=build(c,p,b,tmp_path/'out',originals=m)
 assert result['editions']['basic']['questions']==1
 assert (tmp_path/'out/制作说明.html').is_file()
 assert (tmp_path/'out/试卷包/往年原卷/复变函数A-B/local-exam.pdf').read_bytes()==source.read_bytes()
 delivered = json.loads((tmp_path / 'out/catalog.json').read_text())[0]
 assert delivered['public_source_path'] == '试卷包/往年原卷/复变函数A-B/local-exam.pdf'
 assert delivered['public_textbook_path'] == '教材/index.html?practice=full#/CH01-S01-SS04'


def teacher_inputs(tmp, *, independent_names=False):
    """Two reviewed catalogs with independently generated evidence."""
    base, extra = tmp / 'base', tmp / 'extra'
    base.mkdir()
    extra.mkdir()
    shared = {
        'source': {'kind': 'teacher_material', 'course': 'Fixture', 'pdf_path': '/private/fixture.pdf'},
        'anchor_text': '唯一教学锚点',
        'mount_unit_id': 'CH01-S01-SS04',
        'placement': {
            'unit_id': 'CH01-S01-SS04', 'quote': '唯一教学锚点',
            'paragraph_sha256': 'a' * 64, 'method': 'exact phrase',
        },
        'public_source_path': '',
    }
    common = bound(base, {**shared, 'basic': True, 'unit_id': 'CURRICULUM-UNIT'})
    teacher = bound(extra, {**shared, 'id': 'TEACHER-1', 'basic': False, 'anchor_text': '定制练习锚点'})
    if independent_names:
        for row in teacher['review']['evidence_files']:
            name = 'teacher-' + row['path']
            (extra / row['path']).rename(extra / name)
            row['path'] = name
    (base / 'catalog.json').write_text(json.dumps([common]))
    (extra / 'questions.json').write_text(json.dumps([teacher]))
    profile = tmp / 'teacher.json'
    profile.write_text(json.dumps({
        'include_ids': ['TEACHER-1'],
        'priority_overrides': {'A-1': {'priority': '罕见', 'reason': '定制练习优先级'}},
    }))
    _, _, papers, reader = setup(tmp)
    return base / 'catalog.json', papers, reader, profile, extra / 'questions.json'


def page_questions(path):
    data = path.read_text().split('window.PRACTICE_DATA=', 1)[1].split(';</script>', 1)[0]
    return {row['id']: row for row in json.loads(data)['questions']}


def test_custom_priority_stays_in_custom_exports_and_common_rebuild(tmp_path):
    catalog, papers, reader, profile, additions = teacher_inputs(tmp_path, independent_names=True)
    out = tmp_path / 'out'
    build(catalog, papers, reader, out, profile=profile, additions=additions)
    common = json.loads((out / 'catalog.json').read_text())
    assert [(q['id'], q['priority']) for q in common] == [('A-1', '必会')]
    assert page_questions(out / '完整版-习题集.html')['A-1']['priority'] == '必会'
    assert page_questions(out / '定制版-习题集.html')['A-1']['priority'] == '罕见'

    rebuilt = tmp_path / 'rebuilt'
    build(out / 'catalog.json', out / 'papers.json', out / '教材', rebuilt)
    assert set(page_questions(rebuilt / '完整版-习题集.html')) == {'A-1'}
    assert page_questions(rebuilt / '完整版-习题集.html')['A-1']['priority'] == '必会'

    custom_rebuild = tmp_path / 'custom-rebuilt'
    build(out / 'catalog.json', out / 'papers.json', out / '教材', custom_rebuild,
          profile=out / 'teacher-profile.json', additions=out / 'teacher-additions.json')
    assert page_questions(custom_rebuild / '定制版-习题集.html') == page_questions(out / '定制版-习题集.html')


def test_equal_evidence_basenames_with_different_hashes_survive_build(tmp_path):
    from aiforstudy.practice.catalog import validate_review_files

    catalog, papers, reader, profile, additions = teacher_inputs(tmp_path)
    out = tmp_path / 'out'
    build(catalog, papers, reader, out, profile=profile, additions=additions)
    common = json.loads((out / 'catalog.json').read_text())
    assert validate_review_files(common, out)
    teacher = json.loads((out / 'teacher-additions.json').read_text())
    assert validate_review_files(teacher, out)
    paths = [{row['path'] for row in q['review']['evidence_files']} for q in common + teacher]
    assert paths[0].isdisjoint(paths[1])


def test_reviewed_media_survives_publication_with_profile_and_private_rebuild_inputs(tmp_path):
    import hashlib
    from test_practice_media import animation, bind_media_evidence
    from aiforstudy.practice.catalog import validate_review_files

    catalog, papers, reader, profile, additions = teacher_inputs(tmp_path)
    q = json.loads(catalog.read_text())[0]
    media_root = tmp_path / 'media'
    media_root.mkdir()
    _, manifest = animation(media_root)
    item = manifest['items'][0]
    item['question_content_sha256'] = q['review']['content_sha256']
    item['review']['question_content_sha256'] = item['question_content_sha256']
    item['private_program_path'] = '/private/program.json'
    item['steps'][0].update(display_title='Visible step', spoken_review={'path': '/private/review.json'})
    item['playback']['audio']['private_source'] = '/private/narration.wav'
    program = media_root / 'scene.py'
    program.write_text('source fixture')
    item['files']['source'] = {'path': 'scene.py', 'sha256': hashlib.sha256(program.read_bytes()).hexdigest()}
    bind_media_evidence(media_root, item)
    media = media_root / 'manifest.json'
    media.write_text(json.dumps(manifest))

    reader_assets = reader / 'assets'
    stale = reader_assets / 'practice-media/legacy'
    stale.mkdir(parents=True)
    (stale / 'review.json').write_text('stale private review')
    (reader_assets / 'practice-media-data.js').write_text('window.PRACTICE_MEDIA=[{"private":"stale"}];')
    out = tmp_path / 'out'
    build(catalog, papers, reader, out, profile=profile, additions=additions, media=media)

    for assets in (out / 'assets', out / '教材/assets'):
        copied = assets / 'practice-media'
        assert set(p.relative_to(copied).as_posix() for p in copied.rglob('*') if p.is_file()) == {
            'scene/video.mp4', 'scene/picture.mp4', 'scene/voice.mp3', 'scene/poster.png',
            'scene/captions.vtt', 'scene/transcript.txt', 'scene/scene.svg',
        }
        script = (assets / 'practice-media-data.js').read_text()
        [row] = json.loads(script.removeprefix('window.PRACTICE_MEDIA=').rstrip().removesuffix(';'))
        assert '/private/' not in script and 'review_evidence' not in row
        assert row['question_id'] == 'A-1'
        assert row['steps'] == [{'step_id': 'S1', 'start': 0, 'end': 1, 'display_title': 'Visible step'}]
        assert row['playback']['audio']['src'] == 'practice-media/scene/voice.mp3'
        assert row['resources']['svg']['relative_path'] == 'scene.svg'
    full = page_questions(out / '完整版-习题集.html')['A-1']
    custom = page_questions(out / '定制版-习题集.html')['A-1']
    assert full['priority'] == '必会' and custom['priority'] == '罕见'
    assert full['placement']['quote'] == '唯一教学锚点'
    assert 'review' not in full and 'pdf_path' not in full['source']
    common = json.loads((out / 'catalog.json').read_text())
    teacher = json.loads((out / 'teacher-additions.json').read_text())
    assert validate_review_files(common, out) and validate_review_files(teacher, out)
    assert {row['path'] for row in common[0]['review']['evidence_files']}.isdisjoint(
        {row['path'] for row in teacher[0]['review']['evidence_files']})
    assert (stale / 'review.json').is_file()
    for name in ('scene.py', 'render.json', 'review.json', 'manifest.json'):
        assert (out / '媒体源' / name).read_bytes() == (media_root / name).read_bytes()

    rebuilt = tmp_path / 'rebuild-without-media'
    build(out / 'catalog.json', out / 'papers.json', out / '教材', rebuilt)
    for assets in (rebuilt / 'assets', rebuilt / '教材/assets'):
        assert not (assets / 'practice-media').exists()
        assert (assets / 'practice-media-data.js').read_text() == 'window.PRACTICE_MEDIA=[];\n'


def test_rebuild_selects_verified_media_from_custom_export(tmp_path):
    import subprocess
    import sys

    from test_practice_media import sample, bind_media_evidence

    catalog, papers, reader, profile, additions = teacher_inputs(tmp_path)
    source = tmp_path / 'media'
    items = []
    for path in (catalog, additions):
        [question] = json.loads(path.read_text())
        ident = question['id']
        folder = source / ident
        folder.mkdir(parents=True)
        _, manifest = sample(folder)
        [item] = manifest['items']
        item.update(id=ident, question_id=ident, question_content_sha256=question['review']['content_sha256'])
        item['review']['question_content_sha256'] = item['question_content_sha256']
        name = 'teacher-scene.svg' if ident == 'TEACHER-1' else 'scene.svg'
        (folder / 'scene.svg').rename(folder / name)
        item['files']['svg']['path'] = f'{ident}/{name}'
        if ident == 'TEACHER-1':
            item['static_existing_in_solution'] = True
        bind_media_evidence(folder, item)
        item['review_evidence']['path'] = f'{ident}/review.json'
        items.append(item)
    media = source / 'manifest.json'
    media.write_text(json.dumps({'schema_version':'PracticeMediaV1','items':items}))
    export = tmp_path / 'export'
    build(catalog, papers, reader, export, profile=profile, additions=additions, media=media)

    def rebuild(name, *options):
        out = tmp_path / name
        command = [sys.executable, 'tools/rebuild_practice_delivery.py',
                   '--delivery', str(export), '--out', str(out), *map(str, options)]
        return out, subprocess.run(command, capture_output=True, text=True)

    def assert_media(out, ids):
        private = json.loads((out / '媒体源/manifest.json').read_text())
        assert {item['question_id'] for item in private['items']} == ids
        for assets in (out / 'assets', out / '教材/assets'):
            raw = (assets / 'practice-media-data.js').read_text()
            rows = json.loads(raw.removeprefix('window.PRACTICE_MEDIA=').rstrip().removesuffix(';'))
            assert {row['question_id'] for row in rows} == ids
            assert {p.name for p in (assets / 'practice-media').iterdir()} == ids

    common, run = rebuild('common')
    assert run.returncode == 0, run.stderr
    assert_media(common, {'A-1'})
    assert page_questions(common / '完整版-习题集.html')['A-1']['priority'] == '必会'
    assert not (common / 'teacher-additions.json').exists()
    assert not (common / '教材/assets/teacher-scene.svg').exists()

    custom, run = rebuild('custom', '--profile', export / 'teacher-profile.json',
                         '--additions', export / 'teacher-additions.json')
    assert run.returncode == 0, run.stderr
    assert_media(custom, {'A-1', 'TEACHER-1'})
    for assets in (custom / 'assets', custom / '教材/assets'):
        assert (assets / 'teacher-scene.svg').is_file()
    assert page_questions(custom / '定制版-习题集.html')['A-1']['priority'] == '罕见'
    assert page_questions(custom / '完整版-习题集.html')['A-1']['priority'] == '必会'

    saved_media = export / '媒体源/manifest.json'
    original = json.loads(saved_media.read_text())
    for field, value in [('question_id', 'UNKNOWN'), ('question_content_sha256', 'a'*64)]:
        corrupted = json.loads(json.dumps(original))
        corrupted['items'][1][field] = value
        saved_media.write_text(json.dumps(corrupted))
        rejected, run = rebuild('bad-' + field)
        assert run.returncode != 0 and 'media question binding mismatch' in run.stderr
        assert not rejected.exists()
