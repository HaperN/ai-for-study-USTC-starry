import pytest
from aiforstudy.practice.catalog import validate_catalog,content_digest
from test_practice_catalog import approved

def test_a_review_hash_cannot_authorize_corrupted_formula_control_characters():
    q=approved();q['answer']='$-\frac12$';q['review']['content_sha256']=content_digest(q)
    with pytest.raises(ValueError,match='control'):validate_catalog([q])

def test_escaped_theta_cannot_silently_become_tab_heta():
    q=approved();q['stem']='证明对所有 $'+chr(9)+'heta$ 成立。';q['review']['content_sha256']=content_digest(q)
    with pytest.raises(ValueError,match='control'):validate_catalog([q])
    q['stem']='列表\ttext\n证明对所有 $\\theta$ 成立。';q['review']['content_sha256']=content_digest(q)
    assert validate_catalog([q])
