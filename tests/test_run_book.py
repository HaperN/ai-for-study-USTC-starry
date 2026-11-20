"""Retired writer refusal and current scoped author's order/concurrency invariants."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import importlib.util
from pathlib import Path

import pytest

from aiforstudy.blocks.content.generation import generate_content
from aiforstudy.blocks.content.content_contracts import content_digest
from aiforstudy.core.review_mode import review_mode
from test_lesson import OfflineAuthor, author_case

ROOT = Path(__file__).resolve().parents[1]


def run_unit(case):
    content, source, policy = case
    client = OfflineAuthor(content)
    result = generate_content(source, policy, client)['content']
    assert len(client.calls) == 1
    return result


def test_products_do_not_depend_on_unit_order():
    cases = [author_case('CH01-S01'), author_case('CH02-S01')]
    forward = {c[1].unit_id: content_digest(run_unit(c)) for c in cases}
    backward = {c[1].unit_id: content_digest(run_unit(c)) for c in reversed(cases)}
    assert forward == backward


@pytest.mark.parametrize('mode', ['full', 'balanced'])
def test_two_units_in_threads_match_serial_generation(mode):
    cases = [author_case('CH01-S01'), author_case('CH02-S01')]
    with review_mode(mode):
        serial = [run_unit(c) for c in cases]
        with ThreadPoolExecutor(max_workers=2) as pool:
            # Match the production Runner: each worker receives the run's mode,
            # rather than reading whichever repository default is currently set.
            futures = [pool.submit(copy_context().run, run_unit, c) for c in cases]
            parallel = [future.result() for future in futures]
    assert parallel == serial


def test_frozen_source_does_not_leak_other_units_or_live_mutations():
    case = author_case('CH01-S01')
    before = run_unit(case)
    other = author_case('CH02-S01')
    other[0]['body_markdown'] = '第二章新结果。'
    run_unit(other)
    detached = case[1].as_dict()
    detached['concept_index']['foreign'] = 'cannot enter frozen source'
    assert run_unit(case) == before


@pytest.mark.parametrize('command', ['run_book.py', 'run_chapter.py'])
@pytest.mark.parametrize('arguments', [[], ['--help'], ['--chapters', 'CH01', '--budget-tokens', '1']])
def test_retired_book_worker_refuses_without_changing_history(tmp_path, capsys, monkeypatch, command, arguments):
    spec = importlib.util.spec_from_file_location('retired_'+command.replace('.','_'), ROOT/'tools'/command)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths = [tmp_path/name for name in ('concepts.json','snapshot.json','lessons.index.json','CH01.json','CH02.json')]
    for path in paths: path.write_text('{"historical":true}', encoding='utf-8')
    before = {p.name:p.read_bytes() for p in paths}
    monkeypatch.chdir(tmp_path)
    assert module.main(arguments) == 2
    assert '已退役' in capsys.readouterr().err
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()} == before
