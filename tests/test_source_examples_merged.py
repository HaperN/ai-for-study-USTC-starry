"""Source example identities survive OCR that merges several examples per line."""
from aiforstudy.blocks.content.source_context import extract_source_examples, source_context_from_frozen


CHUNKS = [
    '例2 设 $z=x+iy$，证明所给关系。证 由共轭性质可得结论。',
    '例3 求 $f(z)$ 的实部。解 代入公式得到结果。',
    '例4 试证明根共轭存在。证 设 $z_0$ 为根。$$p(\\bar z_0)=0.$$ ',
    '例5 设两个任意复数，证明$$|z_1+z_2|^2=A.$$证 展开两边即可。',
]
MERGED = '### 1.1.2 共轭复数\n\n前文性质与解释。' + ''.join(CHUNKS)


def test_plain_integer_example_labels_and_proof_marker_are_preserved():
    rows = extract_source_examples('例2 设 $z=x+iy$，证明所给关系。\n证 根据定义即可。')
    assert len(rows) == 1
    assert rows[0]['label'] == '例2'
    assert rows[0]['statement'] == '例2 设 $z=x+iy$，证明所给关系。'
    assert rows[0]['book_solution'] == '证 根据定义即可。'


def test_same_line_examples_have_separate_statements_solutions_and_source_line():
    rows = extract_source_examples(MERGED)
    assert [row['label'] for row in rows] == ['例2', '例3', '例4', '例5']
    assert len({row['id'] for row in rows}) == 4
    assert [row['source_line'] for row in rows] == [3, 3, 3, 3]
    assert rows[0]['statement'] == '例2 设 $z=x+iy$，证明所给关系。'
    assert rows[0]['book_solution'] == '证 由共轭性质可得结论。'
    assert rows[3]['statement'] == '例5 设两个任意复数，证明$$|z_1+z_2|^2=A.$$'
    assert rows[3]['book_solution'] == '证 展开两边即可。'
    for i, row in enumerate(rows):
        assert all('例' + str(j + 2) not in row['statement'] + row['book_solution']
                   for j in range(4) if i != j)


def test_source_obligations_use_exact_example_spans_instead_of_the_shared_whole_line():
    context = source_context_from_frozen(dict(book_id='B', unit_id='U', source_text=MERGED,
        original_text='U.md', original_sha256='sha256:frozen', source_inputs={},
        unit={}, chapters={}, concept_index={}))
    obligations = [row for row in context.source_obligations if row.kind == 'example']
    assert len(obligations) == 4
    assert [row.source_excerpt for row in obligations] == [chunk.strip() for chunk in CHUNKS]
    for obligation in obligations:
        ref = obligation.source_refs[0]
        assert MERGED[ref.start:ref.end] == obligation.source_excerpt
        assert ref.start == MERGED.index(obligation.source_excerpt)


def test_inline_references_to_existing_examples_are_not_new_example_blocks():
    text = '由例2可知此式成立。参见例3的推导。如例2所述，例题4不是新题。\n例4 求值。\n解 直接计算。'
    rows = extract_source_examples(text)
    assert [row['label'] for row in rows] == ['例4']


def test_explicit_inline_theory_after_an_example_is_not_swallowed():
    text = '理论引言。例2 求值。解 直接计算。定理1 实数运算具有交换律。'
    rows = extract_source_examples(text)
    assert rows[0]['statement'] == '例2 求值。'
    assert rows[0]['book_solution'] == '解 直接计算。'
    assert '理论' not in rows[0]['statement'] + rows[0]['book_solution']


def test_no_proven_solution_boundary_preserves_the_entire_example():
    text = '例2 考虑关系$$A=B$$这里继续说明尚未明确分开的题干条件。'
    rows = extract_source_examples(text)
    assert rows[0]['statement'] == text
    assert rows[0]['book_solution'] == ''


def test_example_looking_text_inside_math_is_not_a_new_source_example():
    text = r'公式 $\text{例2 不是题目}$。例3 求值。解 直接计算。'
    assert [row['label'] for row in extract_source_examples(text)] == ['例3']


def test_proof_looking_text_inside_math_is_not_a_solution_boundary():
    text = '例2 解释记号 $证 x=1$ 并给出条件。'
    rows = extract_source_examples(text)
    assert rows[0]['statement'] == text
    assert rows[0]['book_solution'] == ''


def test_repeated_example_text_keeps_distinct_original_offsets():
    chunk = '例2 求值。解 直接计算。'
    text = '引言。' + chunk + chunk
    context = source_context_from_frozen(dict(book_id='B', unit_id='U', source_text=text,
        original_text='U.md', original_sha256='sha256:frozen', source_inputs={},
        unit={}, chapters={}, concept_index={}))
    examples = [row for row in context.source_obligations if row.kind == 'example']
    assert [row.source_excerpt for row in examples] == [chunk, chunk]
    assert [row.source_refs[0].start for row in examples] == [3, 3 + len(chunk)]
