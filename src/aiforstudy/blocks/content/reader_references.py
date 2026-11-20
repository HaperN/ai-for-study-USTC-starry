"""读者文字字段目录与引用契约文本。

用户 2026-10-11 决定删除 `reader_reference_errors`（锚点/指针合法性校验）与
`_same_target_candidates`（候选恢复）。保留下面两个函数：提示词要用字段目录，
媒体需求侧要用引用契约文本。
"""
from .reader_fields import _leaves, _registry_tree, teaching_field_patterns


def teaching_text_fields(content):
    return [dict(json_pointer=pointer, owner_id=owner, raw_text=text, field_label=label)
            for pointer, _, owner, text, label in _leaves(content, _registry_tree())
            if not pointer.startswith('/exercise_regions/')]


def reader_reference_contract():
    return dict(text_leaf_patterns=teaching_field_patterns(),
        rules='路径相对于 content 根；* 仅表示规则里的数组位置，输出时必须替换为从 0 开始的数字索引。'
              '必须指向本次输出已有的非空文本叶，不能使用对象 ID、容器、教学计划、数学对象或 exercise_regions。'
              '例如 /examples/0/steps/0/markdown；不是 /examples/EX-2 或 /examples/0。'
              'concepts.explained_where 非空时适用相同规则；没有定位时原字段可省略，已提供的解释绑定不可在纠正时删空。',
        nested_example={
            'examples':[{'id':'EX-2','kind':'derived_example','statement':'判断零是否非负。',
                'steps':[{'id':'STEP-1','markdown':'零等于零，因此非负。','basis':'非负的定义。'}]}],
            'prerequisite_recaps':[{'id':'R-ALG','title':'整数','markdown':'整数包括零。'}],
            'concepts':[{'id':'C-INTEGER','role':'reused','explained_where':'/prerequisite_recaps/0/markdown'}],
            'teaching_evidence':[{'goal_id':'G','purpose':'derivation',
                'reader_pointer':'/examples/0/steps/0/markdown','claim_refs':['K']}]})
