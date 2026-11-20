"""Review scope includes one visible object, without inventing coverage."""
from copy import deepcopy

import pytest

from aiforstudy.blocks.content.review import build_evidence_catalog, validate_verdicts
from aiforstudy.errors import ContractError
from test_teaching_review_evidence import item, source_bound_inputs, verdict


def owner_case():
    c, doc, source = source_bound_inputs()
    c['examples'] = [
        {'id':'SAME', 'statement':'Prove cancellation.', 'approach':'Use the inverse.',
         'steps':[{'id':'S1', 'markdown':'Multiply by the inverse.', 'basis':'x is nonzero.'}],
         'conclusion':'The product is one.', 'book_solution':'Original argument.'},
        {'id':'SAME', 'statement':'A different example.',
         'steps':[{'id':'S1', 'markdown':'Unrelated step.', 'basis':'Other premise.'}]}]
    c['annotations'] = [{'id':'SAME','markdown':'An unrelated annotation.'}]
    c['teaching_evidence'] = [{'goal_id':'G','claim_refs':['K'],
        'reader_pointer':'/examples/0/statement','purpose':'example'}]
    for index, example in enumerate(c['examples']):
        for field in ('statement','approach','conclusion','book_solution'):
            if field in example:
                doc['blocks'].append({'block_id':f'e{index}-{field}',
                    'json_pointer':f'/examples/{index}/{field}','visible_text':example[field]})
        for field in ('markdown','basis'):
            doc['blocks'].append({'block_id':f'e{index}-step-{field}',
                'json_pointer':f'/examples/{index}/steps/0/{field}',
                'visible_text':example['steps'][0][field]})
    doc['blocks'].append({'block_id':'annotation','json_pointer':'/annotations/0/markdown',
                         'visible_text':c['annotations'][0]['markdown']})
    return c, doc, source


def test_example_anchor_exposes_its_proof_basis_and_original_solution_to_same_subject():
    c, doc, source = owner_case()
    before = deepcopy(c)
    catalog = build_evidence_catalog(c, doc, source)
    for key in ('e0-statement','e0-approach','e0-conclusion','e0-book_solution',
                'e0-step-markdown','e0-step-basis'):
        assert set(catalog['reader:'+key]['subject_ids']) == {'unit:U','G','K','SO1'}
    assert c == before
    criterion = item(subject_id='G', content_hash=catalog['reader:e0-statement']['content_hash'],
                     required_evidence_kinds=['reader'])
    row = verdict(evidence_refs=[{'evidence_id':'reader:e0-step-markdown',
                                'quote':'Multiply by the inverse.'}])
    assert validate_verdicts([criterion], {'verdicts':[row]}, catalog).decision == 'accepted'


@pytest.mark.parametrize('key', ['e1-statement','e1-step-markdown','annotation','B'])
def test_optional_reader_location_does_not_manufacture_subject_binding(key):
    c, doc, source = owner_case()
    catalog = build_evidence_catalog(c, doc, source)
    assert catalog['reader:'+key]['subject_ids'] == ['unit:U']
    criterion = item(subject_id='G', content_hash=catalog['reader:'+key]['content_hash'],
                     required_evidence_kinds=['reader'])
    row = verdict(evidence_refs=[{'evidence_id':'reader:'+key,'quote':catalog['reader:'+key]['text']}])
    before = deepcopy(catalog)
    assert validate_verdicts([criterion], {'verdicts':[row]}, catalog).decision == 'accepted'
    assert catalog == before


def test_supplementary_owner_context_never_satisfies_missing_explicit_coverage():
    c, doc, source = owner_case()
    doc['blocks'] = [b for b in doc['blocks'] if b['json_pointer'] != '/examples/0/statement']
    catalog = build_evidence_catalog(c, doc, source)
    assert 'G' in catalog['reader:e0-step-markdown']['subject_ids']
    assert catalog['absence:SO1']['parent_pointer'] == '/teaching_evidence'


def test_root_anchor_does_not_expand_to_other_reader_objects():
    c, doc, source = owner_case()
    c['teaching_evidence'][0]['reader_pointer'] = '/body_markdown'
    catalog = build_evidence_catalog(c, doc, source)
    assert 'G' in catalog['reader:B']['subject_ids']
    assert catalog['reader:e0-step-markdown']['subject_ids'] == ['unit:U']


def test_hidden_or_nonexistent_anchor_cannot_grant_owner_scope():
    c, doc, source = owner_case()
    c['teaching_evidence'][0]['reader_pointer'] = '/examples/0/source_refs/0/quote'
    catalog = build_evidence_catalog(c, doc, source)
    assert catalog['reader:e0-step-markdown']['subject_ids'] == ['unit:U']
    assert 'absence:SO1' in catalog


def test_goal_batch_can_inspect_and_cite_the_same_examples_nested_step():
    import json
    from aiforstudy.blocks.content.content_contracts import content_digest
    from aiforstudy.blocks.content.reader_document import project_reader
    from aiforstudy.blocks.content.review import review_content
    from test_feedback import review_case
    c, source, _, criterion, _, _ = review_case()
    c['examples'] = [{'id':'E','kind':'derived_example','statement':'Check n=0.',
        'steps':[{'id':'S','markdown':'Zero is nonnegative.', 'basis':'The convention includes zero.'}]}]
    c['teaching_evidence'] = [{'goal_id':'G','purpose':'example','claim_refs':['K'],
                             'reader_pointer':'/examples/0/statement'}]
    criterion={**criterion,'subject_id':'G','content_hash':content_digest(c),
               'required_evidence_kinds':['reader']}
    class StepReviewer:
        def chat_json(self,messages,**kwargs):
            request=json.loads(messages[1]['content'])['frozen_context']
            eid,entry=next((eid,row) for eid,row in request['evidence_catalog'].items()
                if row.get('json_pointer')=='/examples/0/steps/0/markdown')
            return {'data':{'verdicts':[{'criterion_id':criterion['criterion_id'],
                'version':criterion['version'],'status':'unknown',
                'reason':'Inspect the visible proof; semantic judgment is not simulated.',
                'evidence_refs':[{'evidence_id':eid,'quote':entry['text']}]}]}}
    report=review_content(c,project_reader(c,source['reference_catalog']),source,[criterion],None,
                          StepReviewer())
    assert report.decision=='needs_review'
    quoted=report.evidence_catalog[report.verdicts[0]['evidence_refs'][0]['evidence_id']]
    assert quoted['json_pointer']=='/examples/0/steps/0/markdown'
