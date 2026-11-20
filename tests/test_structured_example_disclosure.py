"""Real generated V4 fields through production projection and DOM layout."""
import json
import subprocess
from pathlib import Path
import pytest
from aiforstudy.blocks.content.content_contracts import parse_content
from aiforstudy.core.review_mode import review_mode
from aiforstudy.blocks.content.reader_document import project_reader
from aiforstudy.blocks.content.teaching_projection import compile_teaching_presentation
from aiforstudy.blocks.render.layout import layout_from_reader_media

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('explicit', [True, False])
def test_generated_structured_examples_have_one_disclosure(explicit):
    content = json.loads((ROOT/'tests/fixtures/reader/structured-example-roles.json').read_text())
    if not explicit:
        for row in content['teaching_evidence']:
            row.pop('reader_role', None)
    with review_mode("balanced"):
        content = parse_content(content).as_dict()
        reader = project_reader(content, {'entries': []}).as_dict()
    presentation = compile_teaching_presentation(content, reader)
    unit = {'unit_id': content['unit_id'], 'title': content['title'],
            'reader_document': reader, 'reader_layout': layout_from_reader_media(reader, []),
            'teaching_presentation': presentation, 'example_count': 4}
    result = subprocess.run(['node', str(ROOT/'tests/ui/structured_example_roundtrip.cjs')],
                            input=json.dumps(unit), text=True, capture_output=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_strict_v4_structured_and_independent_body_roles():
    from support.content_case import teaching_case
    from aiforstudy.blocks.content.content_contracts import LessonContentV4
    c, _ = teaching_case()
    c['body_markdown'] = '必要条件和主线结论。\n\n独立例题题干。\n\n独立证明推导。'
    c['examples'] = [{'id': 'E', 'kind': 'source', 'label': '边界练习',
                      'statement': '检验零。', 'approach': '代入定义。',
                      'steps': [{'id': 'S', 'markdown': '零是非负整数。', 'basis': '定义。'}]}]
    c['teaching_evidence'] = [
        {'goal_id': 'G', 'purpose': 'definition', 'reader_pointer': pointer,
         'claim_refs': ['K'], 'quote': quote, 'reader_role': role}
        for pointer, quote, role in [
            ('/body_markdown', '必要条件和主线结论。', 'main'),
            ('/body_markdown', '独立例题题干。', 'worked_example'),
            ('/body_markdown', '独立证明推导。', 'proof'),
            ('/examples/0/statement', '检验零。', 'worked_example'),
            ('/examples/0/steps/0/markdown', '零是非负整数。', 'worked_example')]]
    content = LessonContentV4.model_validate(c).as_dict()
    reader = project_reader(content, {'entries': []}).as_dict()
    unit = {'unit_id': 'U', 'title': c['title'], 'reader_document': reader,
            'reader_layout': layout_from_reader_media(reader, []),
            'teaching_presentation': compile_teaching_presentation(content, reader),
            'example_count': 1, 'check_body': True}
    result = subprocess.run(['node', str(ROOT/'tests/ui/structured_example_roundtrip.cjs')],
                            input=json.dumps(unit), text=True, capture_output=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr
