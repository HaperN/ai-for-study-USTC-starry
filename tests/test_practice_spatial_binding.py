import json,re
from aiforstudy.practice.render import public_question,build_page
from test_practice_catalog import approved

def test_spatial_controls_are_explicit_safe_and_answer_only():
    q=approved();q['spatial_scenes']=['sphere','../../private','log','sphere']
    assert public_question(q)['spatial_scenes']==['sphere','log']
    answer=build_page('练习',[q])
    question=build_page('题册',[q],question_only=True)
    assert 'practice-spatial-attachments.js' in answer
    assert 'practice-spatial-attachments.js' not in question
    match=re.search(r'window.PRACTICE_DATA=(.*?);</script>',question)
    assert 'spatial_scenes' not in json.loads(match.group(1))['questions'][0]
