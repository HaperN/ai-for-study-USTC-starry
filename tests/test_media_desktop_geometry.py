"""Media is designed for the published desktop comparison column."""
import json

from aiforstudy.blocks.render.display_policy import reader_display_policy, reader_display_css
from aiforstudy.blocks.media.review_policy import media_review_policy, build_media_quality_contract
from aiforstudy.blocks.media.code_generation import media_code_prompt
from test_media_code_contracts import approved_case


def test_desktop_uses_real_comparison_media_pixels_not_the_single_column_limit():
    policy = reader_display_policy()
    assert policy['maximum_media_width_px'] == 780
    assert [(p['width'], p['content_width']) for p in policy['profiles']] == [(1280, 450)]
    assert media_review_policy()['required_viewports'] == [1280]
    assert build_media_quality_contract('animation')['display_profiles'] == policy['profiles']


def test_generation_and_review_share_desktop_geometry_for_both_media_kinds():
    for kind in ('drawing', 'animation'):
        content, approval, request, _ = approved_case(kind)
        messages = media_code_prompt(request, content=content, approval=approval)
        payload = json.loads(messages[-1]['content'])
        assert payload['display_profiles'] == build_media_quality_contract(kind)['display_profiles']
        assert payload['display_profiles'][0]['content_width'] == 450
        assert 'mobject.height * 450 / config.frame_width' in messages[0]['content']
        assert '双栏' in messages[0]['content']
