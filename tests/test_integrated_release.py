import sqlite3
from pathlib import Path

import pytest
from tools.prepare_integrated_release import snapshot_notes, inventory, verify_inventory, check_roots, publish


def create_db(path, sql):
    with sqlite3.connect(path) as conn:
        conn.executescript(sql)


def test_snapshots_preserve_owners_images_and_reject_unmapped_alternate(tmp_path):
    active, alternate = tmp_path/'A', tmp_path/'B'
    create_db(active, "CREATE TABLE annotations(owner, body); INSERT INTO annotations VALUES ('owner-1','note'); CREATE TABLE annotation_images(note, data); INSERT INTO annotation_images VALUES (1,x'ff00');")
    create_db(alternate, "CREATE TABLE actors(actor); INSERT INTO actors VALUES ('other'); CREATE TABLE notes(author);")
    before = inventory(tmp_path)
    first, second = tmp_path/'one', tmp_path/'two'
    for dest in (first, second):
        report = snapshot_notes(active, alternate, dest)
        assert report['A'] == {'annotations': 1, 'annotation_images': 1}
        with sqlite3.connect(dest/'notes.sqlite3') as conn:
            assert conn.execute('SELECT * FROM annotations').fetchall() == [('owner-1','note')]
            assert conn.execute('SELECT * FROM annotation_images').fetchall() == [(1,b'\xff\x00')]
        assert (dest/'notes.sqlite3').read_bytes() == (dest/'A.sqlite3').read_bytes()
    assert inventory(first) == inventory(second)
    assert {k:v for k,v in inventory(tmp_path).items() if k in before} == before
    create_db(alternate, "INSERT INTO notes VALUES ('other');")
    with pytest.raises(ValueError, match='迁移'):
        snapshot_notes(active, alternate, tmp_path/'refused')
    assert not (tmp_path/'refused/notes.sqlite3').exists()


def test_disjoint_inputs_and_output(tmp_path):
    src = tmp_path/'input'
    for targets in ([src], [src/'sub'], [tmp_path], [tmp_path/'out',tmp_path/'out/student']):
        with pytest.raises(ValueError):
            check_roots([src], targets)


def test_repeat_verified_publish_and_tamper(tmp_path):
    staged = tmp_path/'stage'
    staged.mkdir()
    (staged/'book').write_text('original')
    out = tmp_path/'release'
    expected = inventory(staged)
    publish(staged,out)
    again = tmp_path/'again'
    again.mkdir()
    (again/'book').write_text('original')
    publish(again,out)
    verify_inventory(out,expected)
    (out/'book').write_text('user changed')
    with pytest.raises(ValueError):
        publish(again,out)
    assert (out/'book').read_text() == 'user changed'


def test_snapshot_reads_committed_wal_without_touching_live_shared_memory(tmp_path):
    from contextlib import closing
    from tools.prepare_integrated_release import snapshot

    db = tmp_path/'live.sqlite'
    with closing(sqlite3.connect(db)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.executescript('CREATE TABLE entries(id); INSERT INTO entries VALUES(1);')
        files = list(tmp_path.glob('live.sqlite*'))
        before = {p:(p.read_bytes(),p.stat().st_mtime_ns) for p in files}
        assert snapshot(db,tmp_path/'copy') == {'entries':1}
        assert before == {p:(p.read_bytes(),p.stat().st_mtime_ns) for p in files}


def test_reader_projection_preserves_public_citations_and_filters_nested_private_fields():
    from tools.prepare_integrated_release import project_book

    record = {'units':{'U':{'study_lesson':{'source_metadata':{'source_path':'local.md','conclusions':['x']}},
                             'source_review_outcome':{'upstream':['hidden']}, 'automatic_processing':{'history':[]}}},
              'animations':{}, 'media_purposes':{'items':[]},
              'course_library':{'sources':[{'provenance':[{'answer_note':'公开出处','evidence_png':'proof.png'}]}]},
              'theorem_index':{'by_unit':{'U':{'id':{'statement':'x','review_outcome':{'reasons':['hidden']}}}}}}
    public = project_book(record)
    unit = public['units']['U']
    assert unit == {'study_lesson':{'source_metadata':{'conclusions':['x']}}}
    assert public['course_library']['sources'][0]['provenance']==[{'answer_note':'公开出处'}]
    assert public['theorem_index']['by_unit']['U']['id']=={'statement':'x'}


def test_hot_rollback_journal_requires_retry(tmp_path):
    from tools.prepare_integrated_release import snapshot
    path = tmp_path/'active'
    create_db(path, 'CREATE TABLE entries(id)')
    (tmp_path/'active-journal').write_bytes(b'busy')
    with pytest.raises(ValueError,match='回滚日志'):
        snapshot(path,tmp_path/'copy')
    assert not (tmp_path/'copy').exists()


def test_public_book_excludes_nested_theorem_reviews_and_animation_production_records():
    from tools.prepare_integrated_release import project_book

    question = {'source_label':'原文未给出此条件','core_blocks':[{'markdown':'须满足 $z≠0$'}]}
    identity = {'relative_path':'narrated.mp4','sha256':'a'*64}
    playback = {'video_src':'assets/video.mp4','audio':{'sha256':'b'*64}}
    step = {'step_id':'S1','observation':'观察原点','expected_change':'辐角增加','caption_tex':'$z≠0$',
            'display_markdown':'原文未核实','narration_text':'先检查条件。','start':0,'end':2,
            'timeline':{'start_seconds':0,'end_seconds':2}}
    payload = {'production_accepted':False,'units':{},'course_library':{'sources':[]},
               'media_purposes':{'items':[]},'unavailable_units':['U'],'problem_summary':[{'label':'未核实'}],
               'theorem_index':{'by_unit':{'U':{'C':{**question,'review_outcome':{},
                    'source_review_outcome':{'status':'known_error','reasons':['内部维修指令']}}}}},
               'animations':{'U':[{'production_accepted':True,'resources':{'mp4':identity},'playback':playback,
                    'steps':[{**step,'script_sha256':'c'*64,'direction_status':'not_applied',
                              'script_revision':{'before':'old','after':'new'},'input_motion_duration':2}]}]}}
    public = project_book(payload)
    assert 'production_accepted' not in public
    assert public['problem_summary']==[{'label':'未核实'}]
    assert public['theorem_index']['by_unit']['U']['C']==question
    assert public['animations']['U']==[{'resources':{'mp4':identity},'playback':playback,'steps':[step]}]
