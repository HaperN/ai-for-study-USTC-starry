from pathlib import Path
import json
from tools.publish_expanded_student import learning_files

def test_student_file_selection_keeps_units_and_media_but_excludes_production(tmp_path):
    (tmp_path/'assets/fonts').mkdir(parents=True)
    (tmp_path/'教材/assets/fonts').mkdir(parents=True)
    (tmp_path/'教材/assets/practice-media-data.js').write_text('window.PRACTICE_MEDIA=[];')
    (tmp_path/'assets/catalog.json').write_text('PRIVATE')
    data={'units':{'CH01':{}},'animations':{}}
    names=learning_files(tmp_path,data)
    assert '章节练习/CH01.html' in names['course']
    assert '三维图解.html' in names['course']
    assert 'assets/practice-spatial-attachments.js' in names['course']
    for private in ('catalog.json','evidence/author.json','assets/catalog.json','媒体源/manifest.json'):
        assert private not in names['course']

def test_only_declared_answer_svg_alias_is_copied(tmp_path):
    (tmp_path/'教材/assets').mkdir(parents=True)
    media=[{'id':'sc','base':'practice-media/sc/','static_existing_in_solution':True,
            'resources':{'svg':{'relative_path':'practice-TB-FIG-CH08-Q12.svg'},'author':{'relative_path':'private.json'}}},
           {'id':'other','base':'practice-media/other/','resources':{'svg':{'relative_path':'not-an-answer.svg'}}}]
    (tmp_path/'教材/assets/practice-media-data.js').write_text('window.PRACTICE_MEDIA='+json.dumps(media)+';')
    names=learning_files(tmp_path,{'units':{},'animations':{}})
    for scope in ('course','reader'):
        assert 'assets/practice-TB-FIG-CH08-Q12.svg' in names[scope]
        assert 'assets/not-an-answer.svg' not in names[scope]
        assert 'assets/practice-media/sc/private.json' not in names[scope]
