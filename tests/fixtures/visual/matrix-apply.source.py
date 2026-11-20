"""T06 可信编译产物（由 aiforstudy.blocks.visual.compiler 生成）。

所有数学量都来自受限 AST 的数值采样；所有文字只作为数据出现，
不参与求值、也不拼进代码标识符。本文件不渲染视频，输出路径由宿主分配。
"""
from __future__ import annotations

COMPILER_VERSION = 'visual-compiler-1.0'
PROFILE = 'draft'
SCENE = {'captions': ['先看被作用的向量 v。', '矩阵作用的终点是 (5, 11)。'],
 'compiler_version': 'visual-compiler-1.0',
 'format': 'visual-scene-1.0',
 'formulas': [],
 'math_values': {'apply': [5.0, 11.0]},
 'objects': {'axes2d': {'kind': 'axes2d',
                        'x_label': 'x',
                        'x_range': [-4, 8],
                        'y_label': 'y',
                        'y_range': [-4, 14]},
             'm': {'kind': 'matrix2d', 'rows': [[1.0, 2.0], [3.0, 4.0]]},
             'mv': {'end': [5.0, 11.0], 'kind': 'vector', 'space': 'math2d', 'start': [0.0, 0.0]},
             'mv_tip': {'at': [5.0, 11.0], 'kind': 'point', 'space': 'math2d'},
             'v': {'end': [1.0, 2.0], 'kind': 'vector', 'space': 'math2d', 'start': [0.0, 0.0]}},
 'parameters': {'k': 2},
 'profile': 'draft',
 'steps': [{'actions': [{'action': 'reveal', 'object_id': 'axes2d'},
                        {'action': 'reveal', 'object_id': 'm'},
                        {'action': 'reveal', 'object_id': 'v'}],
            'caption': '先看被作用的向量 v。',
            'checkpoints': [{'at': 2.5, 'note': '读出 v 的两个分量'}],
            'end': 5,
            'expected_change': '出现矩阵与向量',
            'start': 0,
            'step_id': 's1'},
           {'actions': [{'action': 'reveal', 'object_id': 'mv'},
                        {'action': 'reveal', 'object_id': 'mv_tip'},
                        {'action': 'camera', 'phi': 15, 'zoom': 1.4}],
            'caption': '矩阵作用的终点是 (5, 11)。',
            'checkpoints': [{'at': 8, 'note': '核对终点坐标'}],
            'end': 11,
            'expected_change': '出现矩阵作用后的终点',
            'start': 5,
            'step_id': 's2'}]}


def construct(scene):
    """由 T07 的 manim_adapter 把 SCENE 变成 Manim 对象。"""
    raise NotImplementedError('T07 manim_adapter 负责渲染')
