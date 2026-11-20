"""Reader order and media anchor identities share one traversal."""
from aiforstudy.blocks.content.reader_fields import _leaves, _registry_tree
from aiforstudy.blocks.media.media_requirements import visible_fields, goal_reader_context
from aiforstudy.core.digest import digest_of


def test_media_fields_keep_reader_order_owners_steps_and_exclude_original_exercises():
    content = {'unit_id':'U', 'body_markdown':'root', 'teaching_plan':{'secret':'hidden'},
        'examples':[
            {'id':'REUSED', 'statement':'first', 'steps':[
                {'id':'S1','markdown':'one','basis':'because one'},
                {'id':'S2','markdown':'two','basis':'because two'}], 'conclusion':'done'},
            {'id':'REUSED', 'statement':'second', 'steps':[{'id':'S1','markdown':'other'}]}],
        'exercise_regions':[{'id':'EX','title':'Original exercises','raw_markdown':'Do not make a landing site'}]}
    fields = visible_fields(content)
    assert [row['pointer'] for row in fields] == [
        '/body_markdown', '/examples/0/statement', '/examples/0/steps/0/markdown',
        '/examples/0/steps/0/basis', '/examples/0/steps/1/markdown',
        '/examples/0/steps/1/basis', '/examples/0/conclusion',
        '/examples/1/statement', '/examples/1/steps/0/markdown']
    leaves = list(_leaves(content, _registry_tree()))
    assert [r['pointer'] for r in fields] == [p for p, *_ in leaves if not p.startswith('/exercise_regions/')]
    for row in fields:
        assert row['hash'] == digest_of(row['text'])
        assert row['owner_id'] == ('U' if row['pointer']=='/body_markdown' else 'REUSED')
    assert [row.get('step_id') for row in fields] == [None,None,'S1','S1','S2','S2',None,None,'S1']
    context = goal_reader_context(content, {'reader_evidence':[fields[1]]})
    assert context['reader_text'] == 'first\none\nbecause one\ntwo\nbecause two\ndone'
    assert all(not row['pointer'].startswith('/examples/1/') for row in context['supplementary_reader_context'])
