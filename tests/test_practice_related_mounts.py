import json,re
from aiforstudy.practice.render import public_question,build_page
from test_practice_catalog import approved

def test_related_projection_keeps_learning_focus_and_drops_review_material():
    q=approved()
    q['additional_mounts']=[{'unit_id':'CH09-S02-SS01','anchor_text':'导数的变换',
        'focus_note':'本节重点：第（2）问的初值项。','private_review':'SECRET',
        'placement':{'unit_id':'CH09-S02-SS01','quote':'导数的变换','paragraph_sha256':'a'*64,'method':'exact'}}]
    p=public_question(q)
    assert p['additional_mounts'][0]['focus_note']==q['additional_mounts'][0]['focus_note']
    assert 'SECRET' not in json.dumps(p)
    pure=build_page('卷',[q],question_only=True)
    assert 'additional_mounts' not in pure and '初值项' not in pure

def test_daily_hub_counts_shared_question_only_once(tmp_path):
    from tools.build_study_hubs import build_hubs
    q=approved();q['unit_id']='CH01';q['additional_mounts']=[{'unit_id':'CH01-S01','focus_note':'本节重点：第（2）问。'}]
    units=[{'unit_id':u,'chapter_id':'CH01','chapter_title':'复数','title':u} for u in ['CH01','CH01-S01']]
    report=build_hubs(tmp_path,units,[q],[])
    assert report['questions']==1 and report['empty_units']==0
    assert '2 个单元 · 1 题' in (tmp_path/'日常巩固.html').read_text()
    page=(tmp_path/'章节练习/CH01-S01.html').read_text()
    data=json.loads(re.search(r'window.PRACTICE_DATA=(.*?);</script>',page).group(1))
    assert data['questions'][0]['id']==q['id']
    assert data['questions'][0]['focus_note']=='本节重点：第（2）问。'
