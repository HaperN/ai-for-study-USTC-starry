"""The same backend must still receive the right teaching task and example."""
from copy import deepcopy
import json

from aiforstudy.core.review_mode import review_mode
from aiforstudy.blocks.media.code_generation import media_code_prompt


def test_drawing_without_internal_models_gets_a_still_scene_not_animation_steps():
    request = {'request_id': 'R-static', 'kind': 'drawing',
               'learning_goal': '共轭复数关于实轴对称。'}
    content = {'body_markdown': 'z=a+bi，共轭为a-bi；两个点实部相同、虚部互为相反数。'}
    original = deepcopy((request, content))
    with review_mode('balanced'):
        messages = media_code_prompt(request, content=content, approval={})
    payload = json.loads(messages[-1]['content'])
    assert payload['backend'] == 'manim'
    assert payload['allowed_author_step_indices'] == []
    assert payload['example']['context']['request']['kind'] == 'drawing'
    assert payload['example']['program']['backend'] == 'manim'
    assert payload['example']['program']['steps'] == []
    assert 'media_step' not in payload['example']['program']['code']
    assert 'GeneratedScene' in messages[0]['content']
    assert 'steps=[]' in messages[0]['content']
    assert 'build_figure' not in messages[0]['content']
    assert '媒体步骤末端' not in messages[0]['content']
    assert (request, content) == original


def test_static_sample_is_self_contained_and_keeps_its_values_out_of_the_lesson():
    from aiforstudy.blocks.media.static_example import conjugate_static_example
    first = conjugate_static_example()
    second = conjugate_static_example()
    assert first['context']['request']['request_id'] == first['program']['request_id']
    assert first['context']['approved_models'] == []
    assert '示意' in first['purpose']
    first['context']['approved_content']['illustration']['a'] = 100
    assert second['context']['approved_content']['illustration']['a'] == 2


def test_new_generation_does_not_silently_run_the_retired_static_backend():
    import pytest
    from aiforstudy.blocks.media.code_generation import generate_media_code, MediaCodeGenerationError
    candidate = {'backend': 'matplotlib', 'code': 'def build_figure(context): return None'}
    calls = []

    class Client:
        def chat_json(self, messages, **kwargs):
            calls.append(kwargs)
            return {'data': deepcopy(candidate)}

    with review_mode('balanced'), pytest.raises(MediaCodeGenerationError) as caught:
        generate_media_code({'request_id': 'R', 'kind': 'drawing'},
                            content={'body_markdown': '示意图'}, approval={}, client=Client())
    assert caught.value.candidate == candidate
    assert not getattr(caught.value, 'generation_stopped', False)
    assert len(calls) == 1  # Execution repair belongs to the existing outer loop.
