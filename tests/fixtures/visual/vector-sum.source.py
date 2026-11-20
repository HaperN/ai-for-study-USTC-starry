"""T06 可信编译产物（由 aiforstudy.blocks.visual.compiler 生成）。

所有数学量都来自受限 AST 的数值采样；所有文字只作为数据出现，
不参与求值、也不拼进代码标识符。本文件不渲染视频，输出路径由宿主分配。
"""
from __future__ import annotations

COMPILER_VERSION = 'visual-compiler-1.0'
PROFILE = 'draft'
SCENE = {'captions': ['把 v1 与 v2 首尾相接。', '合向量的终点是 (3, 5)。'],
 'compiler_version': 'visual-compiler-1.0',
 'format': 'visual-scene-1.0',
 'formulas': [],
 'math_values': {'sum': [3.0, 5.0]},
 'objects': {'axes2d': {'kind': 'axes2d',
                        'x_label': 'x',
                        'x_range': [-4, 6],
                        'y_label': 'y',
                        'y_range': [-2, 8]},
             'tip': {'at': [3.0, 5.0], 'kind': 'point', 'space': 'math2d'},
             'v1': {'end': [1.0, 2.0], 'kind': 'vector', 'space': 'math2d', 'start': [0.0, 0.0]},
             'v2': {'end': [3.0, 5.0], 'kind': 'vector', 'space': 'math2d', 'start': [1.0, 2.0]},
             'vsum': {'end': [3.0, 5.0], 'kind': 'vector', 'space': 'math2d', 'start': [0.0, 0.0]}},
 'parameters': {},
 'profile': 'draft',
 'steps': [{'actions': [{'action': 'reveal', 'object_id': 'axes2d'},
                        {'action': 'reveal', 'object_id': 'v1'},
                        {'action': 'reveal', 'object_id': 'v2'}],
            'caption': '把 v1 与 v2 首尾相接。',
            'checkpoints': [{'at': 3, 'note': '看两个分向量'}],
            'end': 6,
            'expected_change': '出现两条分向量',
            'start': 0,
            'step_id': 's1'},
           {'actions': [{'action': 'reveal', 'object_id': 'vsum'},
                        {'action': 'reveal', 'object_id': 'tip'},
                        {'action': 'pause', 'seconds': 1.5}],
            'caption': '合向量的终点是 (3, 5)。',
            'checkpoints': [{'at': 9, 'note': '读出终点坐标'}],
            'end': 12,
            'expected_change': '出现合向量与终点',
            'start': 6,
            'step_id': 's2'}]}


def construct(scene):
    """由 T07 的 manim_adapter 把 SCENE 变成 Manim 对象。"""
    raise NotImplementedError('T07 manim_adapter 负责渲染')
