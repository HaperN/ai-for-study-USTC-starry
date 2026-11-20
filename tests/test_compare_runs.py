"""`tools/compare_runs.py` 自己的测试。

⚠️ 为什么这个测试很重要：比对器**是裁判**。裁判要是自己有 bug，
就会把"并行真坏了"报成"等价"，或者把好数据报成"坏了"——
后者更贵（白查一天），前者更危险（放过真 bug）。所以裁判必须先被裁判。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("compare_runs", ROOT / "tools" / "compare_runs.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["compare_runs"] = mod
    spec.loader.exec_module(mod)
    return mod


CR = _load()


def _write(root: Path, rel: str, obj) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, str):
        p.write_text(obj, encoding="utf-8")
    else:
        p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def test_only_volatile_differences_are_equivalent(tmp_path):
    """两棵树只差记账/时间戳/run id/树根路径 → **必须判等价**。

    （否则每次比对都是一堆假差异，人就不看了 —— 实测这个工具最大的风险。）
    """
    a, b = tmp_path / "serial", tmp_path / "par"
    for root in (a, b):
        _write(root, "lesson/CH03-S01.json", {
            "unit_id": "CH03-S01", "revision": 3, "body": "向量组的线性相关性",
            "at": "2026-09-26T22:30:00+0800",
            "seconds": 12.5,
            "usage": {"total_tokens": 1234},
            "run_id": "book-xxx",
            "built_from": {"CH03-S01": 3},
        })
    # 只改"天然会变"的字段
    _write(b, "lesson/CH03-S01.json", {
        "unit_id": "CH03-S01", "revision": 3, "body": "向量组的线性相关性",
        "at": "2026-09-26T23:59:59+0800",
        "seconds": 99.9,
        "usage": {"total_tokens": 4321},
        "run_id": "book-yyy",
        "built_from": {"CH03-S01": 3},
    })
    r = CR.compare(a, b)
    assert r["equivalent"], f"只差易变字段却判不等：{r['differing']}"
    assert r["volatile_normalized"] > 0, "应该报告抹掉了多少处（不能静默）"


def test_real_content_difference_is_caught(tmp_path):
    """正文差一个字 → **必须判不等**（这是裁判的基本职责）。"""
    a, b = tmp_path / "serial", tmp_path / "par"
    _write(a, "lesson/CH03-S01.json", {"body": "向量组线性相关"})
    _write(b, "lesson/CH03-S01.json", {"body": "向量组线性无关"})
    r = CR.compare(a, b)
    assert not r["equivalent"]
    assert [d["file"] for d in r["differing"]] == ["lesson/CH03-S01.json"]
    assert r["differing"][0]["diff"], "应该给出差异上下文，不能只报『不同』"


def test_missing_file_is_reported_on_the_right_side(tmp_path):
    """并行**漏做**一节（历史上真发生过：读-改-写丢更新）→ 必须报出来。"""
    a, b = tmp_path / "serial", tmp_path / "par"
    _write(a, "lesson/CH03-S01.json", {"body": "x"})
    _write(a, "lesson/CH03-S02.json", {"body": "y"})
    _write(b, "lesson/CH03-S01.json", {"body": "x"})
    r = CR.compare(a, b)
    assert not r["equivalent"]
    assert r["only_left"] == ["lesson/CH03-S02.json"]
    assert r["only_right"] == []


def test_key_named_at_with_non_time_value_is_not_dropped(tmp_path):
    """名叫 `at` 但值是坐标的字段**不能被误抹** —— 抹了就漏掉真差异。"""
    a, b = tmp_path / "serial", tmp_path / "par"
    _write(a, "figures/f1.json", {"at": [10, 20]})
    _write(b, "figures/f1.json", {"at": [10, 21]})      # 坐标真变了
    r = CR.compare(a, b)
    assert not r["equivalent"], "名叫 at 的非时间字段被误抹，漏掉了真差异"


def test_jsonl_line_order_does_not_matter(tmp_path):
    """jsonl 的行序（并行下追加顺序天然会变）不该算差异，但**内容差异要算**。"""
    a, b = tmp_path / "serial", tmp_path / "par"
    (a / "u").mkdir(parents=True); (b / "u").mkdir(parents=True)
    (a / "u" / "usage.jsonl").write_text('{"k":1}\n{"k":2}\n', encoding="utf-8")
    (b / "u" / "usage.jsonl").write_text('{"k":2}\n{"k":1}\n', encoding="utf-8")
    assert CR.compare(a, b)["equivalent"], "行序不同不该判不等"
    (b / "u" / "usage.jsonl").write_text('{"k":2}\n{"k":3}\n', encoding="utf-8")
    assert not CR.compare(a, b)["equivalent"], "内容真变了必须判不等"


def test_tree_root_path_in_text_is_scrubbed(tmp_path):
    """报告/日志里含树根路径 → 抹成 <TREE>，否则每行都是假差异。"""
    a, b = tmp_path / "serial", tmp_path / "par"
    _write(a, "report.md", f"页在 {a}/delivery/CH03 里")
    _write(b, "report.md", f"页在 {b}/delivery/CH03 里")
    assert CR.compare(a, b)["equivalent"]
    _write(b, "report.md", f"页在 {b}/delivery/CH04 里")     # 章真的不同
    assert not CR.compare(a, b)["equivalent"]


def test_run_id_in_filename_is_paired(tmp_path):
    """`book-runs/book-2026…-a51d05.json` 两棵树 id 不同 → 要按逻辑名配对，不能报『各有各的』。"""
    a, b = tmp_path / "serial", tmp_path / "par"
    _write(a, "book-runs/book-20260926-222852-a51d05.json", {"chapters": ["CH03"]})
    _write(b, "book-runs/book-20260926-230011-b7c4e2.json", {"chapters": ["CH03"]})
    r = CR.compare(a, b)
    assert r["equivalent"], f"run id 文件名没配对好：{[*r['only_left'], *r['only_right']]}"


def test_main_exit_codes(tmp_path, capsys):
    """退出码遵守全项目约定：0 等价 / 1 有差异 / 2 工具自己坏了。"""
    a, b = tmp_path / "serial", tmp_path / "par"
    _write(a, "x.json", {"v": 1}); _write(b, "x.json", {"v": 1})
    assert CR.main([str(a), str(b)]) == 0
    _write(b, "x.json", {"v": 2})
    assert CR.main([str(a), str(b)]) == 1
    assert CR.main([str(tmp_path / "nope"), str(b)]) == 2
