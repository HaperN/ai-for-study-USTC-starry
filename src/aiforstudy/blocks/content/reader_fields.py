"""Single reader text-field registry shared by author, renderer and reviewer."""
import json
import re
from ...core.digest import digest_of
from ...core.review_mode import reviews_frozen

# Every entry is reader-facing; plan/provenance fields are intentionally absent.
# '*' traverses arrays, using stable row IDs for evidence identity.
READER_FIELD_REGISTRY = (
    ('title','标题'),('one_line','导读'),('reader_intro','学习目标'),
    ('prerequisite_recaps/*/title','回顾'),('prerequisite_recaps/*/markdown','先修回顾'),
    ('body_markdown','正文'),
    ('annotations/*/title','批注'),('annotations/*/markdown','解释'),
    ('explainables/*/name','名称'),('explainables/*/what','对象与条件'),('explainables/*/why','使用理由'),
    ('explainables/*/markdown','解释'),('explainables/*/warmup','回顾'),
    ('explainables/*/steps/*/markdown','推导'),('explainables/*/steps/*/basis','依据'),
    ('explainables/*/key_points/*','要点'),('explainables/*/pitfalls/*','易错点'),
    ('examples/*/label','例题'),('examples/*/title','标题'),('examples/*/statement','题目'),
    ('examples/*/approach','思路'),('examples/*/warmup','回顾'),('examples/*/basis','依据'),
    ('examples/*/steps/*/markdown','步骤'),('examples/*/steps/*/basis','依据'),
    ('examples/*/conclusion','结论'),('examples/*/key_points/*','要点'),('examples/*/pitfalls/*','易错点'),
    ('examples/*/book_solution','原书解答'),
    ('learning_checks/*/statement','自测'),('learning_checks/*/input_spec/placeholder','作答说明'),
    ('learning_checks/*/input_spec/options/*/label','选项'),('learning_checks/*/hints/*','提示'),
    ('learning_checks/*/answer','答案'),('learning_checks/*/misconception_feedback/*/feedback','错因反馈'),
    ('notation/*/symbol','符号'),('notation/*/meaning','含义'),
    ('corrections/*/original','原文'),('corrections/*/corrected','修正'),('corrections/*/reason','原因'),
    ('summary','小结'),
    ('exercise_regions/*/title','原书习题资料'),
    ('exercise_regions/*/raw_markdown','原文记录'),
)

# These author aliases are display material, never scoring rules or a claim
# that a generated solution came from the original textbook. Full contracts
# keep their existing field registry.
_FROZEN_ALIASES = {
    'examples/*/book_solution': (('examples/*/solution', '解答'),),
    'learning_checks/*/statement': (('learning_checks/*/prompt', '自测'),),
    'learning_checks/*/input_spec/options/*/label': (('learning_checks/*/options/*', '选项'),),
    'learning_checks/*/misconception_feedback/*/feedback': (
        ('learning_checks/*/feedback', '反馈'),
        ('learning_checks/*/feedback/correct', '正确反馈'),
        ('learning_checks/*/feedback/incorrect', '错误反馈')),
    'corrections/*/reason': (('concepts/*/name', '概念'),
        ('concepts/*/definition', '定义'), ('concepts/*/markdown', '概念解释')),
}


def _registry_tree():
    """Preserve registry field order, visiting each row before the next row."""
    tree = {}
    fields = []
    for item in READER_FIELD_REGISTRY:
        fields.append(item)
        if reviews_frozen():
            fields.extend(_FROZEN_ALIASES.get(item[0], ()))
    for path, label in fields:
        node = tree
        for part in path.split('/'):
            node = node.setdefault(part, {})
        node['$label'] = label
    return tree


def _leaves(value, tree, pointer='', identity='', owner=''):
    if '$label' in tree:
        if isinstance(value, str) and value.strip():
            yield pointer, identity, owner, value, tree['$label']
        elif reviews_frozen() and type(value) in (int, float, bool):
            yield pointer, identity, owner, json.dumps(value, ensure_ascii=False), tree['$label']
        if not reviews_frozen() or not isinstance(value, (dict, list)):
            return
    for key, child_tree in tree.items():
        if key == '*':
            if not isinstance(value, list):
                continue
            for index, child in enumerate(value):
                stable = child.get('id', str(index)) if isinstance(child, dict) else str(index)
                yield from _leaves(child, child_tree, pointer + '/' + str(index),
                                   identity + '/' + str(stable), owner or str(stable))
        elif isinstance(value, dict) and key in value:
            yield from _leaves(value[key], child_tree, pointer + '/' + key,
                               identity + '/' + key, owner)


def teaching_field_patterns():
    return ['/' + path for path, _ in READER_FIELD_REGISTRY
            if not path.startswith('exercise_regions/')]


def iter_teaching_fields(content):
    """Project media anchors from the reader's ordered leaves, with stable owners.

    Frozen original exercises remain reader material, never media landing sites.
    Structural pointers keep rows distinct even when an invalid candidate reuses IDs.
    """
    for pointer, _, owner, text, _ in _leaves(content, _registry_tree()):
        if pointer.startswith('/exercise_regions/'):
            continue
        row = dict(pointer=pointer, hash=digest_of(text), text=text,
                   owner_id=owner or content['unit_id'])
        match = re.fullmatch(r'/(examples|explainables)/(\d+)/steps/(\d+)/(markdown|basis)', pointer)
        if match:
            group, parent, index, _ = match.groups()
            step_id = content[group][int(parent)]['steps'][int(index)].get('id')
            if step_id:
                row['step_id'] = step_id
        yield row


def teaching_pointer_pattern():
    return '^(?:' + '|'.join('/'.join(r'(?:0|[1-9][0-9]*)' if part == '*' else re.escape(part)
        for part in pattern.split('/')) for pattern in teaching_field_patterns()) + ')$'
