import pytest
from aiforstudy.practice.model import question_input

def test_latex_negative_space_before_bracket_is_not_a_missing_image():
    q={'id':'L','stem':r'求 $\mathcal L\![(1-\cos t)/t]$。','source':{'course':'复变函数A'}}
    assert question_input(q,'blind')['stem']==q['stem']
    q['stem']=r'看图 \\![区域](image.svg)'
    with pytest.raises(ValueError,match='image'):question_input(q,'blind')
