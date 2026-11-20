from copy import deepcopy
import pytest
from tools.apply_video_editorial import apply_edits

def sample():
    return {'sections':[{'markdown':'原来的解释','evidence_ids':['asr-1']}], 'formulas':[{'latex':'F_G/F_e'}],
            'corrections':[],'frames':[{'id':'frame-1'}],'transcript':{'segments':[{'id':'asr-1'}]},
            'source':{'sha256':'original'},'review':{'decision':'no_issues_found'},'provenance':{'model':'real'}}

def test_precise_editorial_edit_preserves_original_layers_and_input():
    base=sample();before=deepcopy(base)
    result=apply_edits(base,[{'json_pointer':'/sections/0/markdown','before':'原来的解释','after':'补齐对象区别。','reason':'原片限定','source_ids':['asr-1']}])
    assert base==before and result['sections'][0]['markdown']=='补齐对象区别。'
    for k in ['source','frames','transcript','review']:assert result[k]==base[k]

@pytest.mark.parametrize('change',['pointer','before','source','provenance'])
def test_editorial_patch_cannot_rewrite_sources_or_claim_unbound_edits(change):
    base=sample();edit={'json_pointer':'/sections/0/markdown','before':'原来的解释','after':'新的解释','reason':'核对','source_ids':['asr-1']}
    if change=='pointer':edit.update(json_pointer='/source/sha256',before='original',after='forged')
    if change=='before':edit['before']='未出现的原文'
    if change=='source':edit['source_ids']=['invented']
    if change=='provenance':edit.update(json_pointer='/provenance',before=base['provenance'],after={'model':'forged'})
    with pytest.raises(ValueError):apply_edits(base,[edit])
