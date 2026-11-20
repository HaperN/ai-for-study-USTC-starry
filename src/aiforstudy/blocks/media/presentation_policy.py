"""Host-owned presentation resource policy, shared by planning and validation."""
from math import isfinite

POLICY_VERSION = 'presentation-2.0'
MAX_ANIMATION_SECONDS = 90.0
MAX_ANIMATION_STEPS = 8
MAX_VISIBLE_FORMULAS = 4
RENDER_PROFILES = {
    'draft': {'pixel_width': 640, 'pixel_height': 360, 'frame_rate': 15, 'timeout_seconds': 120},
    'final': {'pixel_width': 1280, 'pixel_height': 720, 'frame_rate': 30, 'timeout_seconds': 300},
}


def validate_duration(seconds):
    from ...errors import ContractError
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not isfinite(seconds) or not 0 < seconds <= MAX_ANIMATION_SECONDS:
        raise ContractError('animation duration 必须满足 0 < 总时长 ≤ 90 秒')
    return float(seconds)


def presentation_policy():
    return {'version': POLICY_VERSION, 'max_seconds': MAX_ANIMATION_SECONDS,
            'max_steps': MAX_ANIMATION_STEPS,
            'max_visible_formulas': MAX_VISIBLE_FORMULAS,
            'step_count_advice': '通常 4–8 步；有教学目的的单步动态也允许。',
            'duration_advice': '时长应足够阅读字幕与观察变化；90 秒只是资源上限。'}
