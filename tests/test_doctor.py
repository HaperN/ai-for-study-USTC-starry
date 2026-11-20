"""`doctor` 的契约：**退出码不许塌陷**（调研报告里的第一条落地建议）。

这条为什么重要（实测过）：`quality_report.py` 的约定是"发现问题返回 1"，
它被打成「✗ 失败」之后，**真正的崩溃和正常的告警长得一模一样** ——
于是真失败藏在一片假失败里。所以 doctor 的退出码写死为：

    0 = 全部不变量通过
    1 = 有违规（**不是崩溃**，是被发现的漂移）
    2 = doctor 自己坏了

另外测一条容易再犯的：检查范围必须**只覆盖当前交付页**。
第一版把 `delivery/` 整个目录扫进来，一下报了 4 条"没有 范围.json"
（全是历史的试点目录）—— 假警报会把真失败淹掉。

## 产物从哪来（2026-10-03 改造）

退出码那几条本来就不需要产物（`CHECKS` 被 monkeypatch 成假的）。
"只查当前交付页"与"退出码在真产物上也成立"这两条原来绑在**真实交付页**上；
现在：前者**下沉为 per-test 守卫**（并补了一条合成等价覆盖），后者**改为在合成夹具上无条件跑**。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from fixtures import synth_book                                        # noqa: E402


def _load():
    spec = importlib.util.spec_from_file_location("doctor_under_test", ROOT / "tools" / "doctor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


doc = _load()

#: 真实产物守卫（原来在模块级的那个表达式，逐字搬到这里，只作用于**它自己那一条**测试）：
#: 这条断言读的是磁盘上的历史交付页（旧交付里的"第六章-两节"样例页）—— 归档后自然跳过。
REAL_SAMPLE_PAGE_PRESENT = bool(
    list((ROOT / "delivery").glob("**/第六章-两节*")))


def test_exit_code_is_zero_when_everything_passes(monkeypatch):
    monkeypatch.setattr(doc, "CHECKS", (("假装没问题的检查", lambda **kw: []),))
    assert doc.main([]) == 0


def test_exit_code_is_one_for_violations_not_two(monkeypatch):
    """"发现漂移"必须是 1，不能和"doctor 崩了"混成一个码。"""
    bad = lambda **kw: [{"不变量": "假装", "说明": "有违规", "证据": "-", "后果": "-"}]
    monkeypatch.setattr(doc, "CHECKS", (("假装有问题的检查", bad),))
    assert doc.main(["--json"]) == 1


def test_exit_code_is_two_when_doctor_itself_breaks(monkeypatch):
    def boom(**kw):
        raise RuntimeError("doctor 自己坏了")

    monkeypatch.setattr(doc, "CHECKS", (("会抛异常的检查", boom),))
    assert doc.main(["--json"]) == 2, "自己崩了必须是 2，不能混进「发现违规」里"


def test_a_crashing_check_does_not_hide_the_other_checks(monkeypatch):
    """一条检查崩了，其余检查的结论仍要出来（不能整体中止）。"""
    bad = lambda **kw: [{"不变量": "假装", "说明": "有违规", "证据": "-", "后果": "-"}]
    monkeypatch.setattr(doc, "CHECKS", (("会抛异常的检查", lambda **kw: 1 / 0),
                                        ("假装有问题的检查", bad)))
    assert doc.main(["--json"]) == 2
    assert len(doc.CHECKS) == 2


@pytest.mark.skipif(
    not REAL_SAMPLE_PAGE_PRESENT,
    reason="旧交付样例页已归档（delivery/ 里没有'第六章-两节'）—— 这条守卫不适用")
def test_delivery_pages_does_not_include_historical_pilots():
    """只查当前交付页；历史试点目录不在其中（否则全是假警报）。"""
    names = [p.name for p in doc.delivery_pages()]
    assert "第六章-两节" in names
    assert "第二章-全章" in names
    assert "第二章试点-v3" not in names
    assert "线性代数-样张" not in names


def test_json_payload_is_machine_readable(monkeypatch, capsys):
    """agent 要能解析它 —— 这是"给 agent 补上反馈信号"的前提。"""
    monkeypatch.setattr(doc, "CHECKS", (("没问题的检查", lambda **kw: []),))
    doc.main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["通过"] is True
    assert payload["违规"] == []
    assert "检查项" in payload


# ── 合成夹具上的等价覆盖（离线，不依赖 data/ 下任何真实产物）──────────────────

def test_delivery_pages_scope_comes_from_the_manifest_not_a_directory_glob(tmp_path, monkeypatch):
    """**离线等价覆盖**：`delivery_pages()` 只收清单（`SECTIONS`/`DELIVERY_DIRS`）
    里的页，历史试点目录不算 —— 只有 `--all`（`include_strays=True`）才看得见它们。

    这是原来 `test_delivery_pages_does_not_include_historical_pilots` 的合成版：
    把"当前交付页"换成夹具造的那一页，把"历史试点"换成一个造出来的 stray 目录。
    """
    book = synth_book.build(tmp_path)
    stray = book.pages_root / "第二章试点-v3"
    stray.mkdir(parents=True, exist_ok=True)
    (stray / "render.index.json").write_text(json.dumps({"scope": ["X"]}), encoding="utf-8")
    synth_book.use_for_doctor(monkeypatch, doc, book)

    names = [p.name for p in doc.delivery_pages()]
    assert names == [book.page.name] == ["CH01"]
    assert "第二章试点-v3" not in names
    # `--all` 的口子仍在：历史页只能这样被看见
    assert "第二章试点-v3" in [p.name for p in doc.delivery_pages(include_strays=True)]


def test_synthetic_historical_book_is_readable_but_never_quality_verified(tmp_path, monkeypatch, capsys):
    """An internally consistent historical snapshot still cannot certify graph-5 quality."""
    book = synth_book.build(tmp_path)
    synth_book.use_for_doctor(monkeypatch, doc, book)
    before={str(p):p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert doc.main(["--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["通过"] is False
    assert {v["不变量"] for v in payload["违规"]} == {"历史质量不可继承"}
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert payload["doctor自己坏了"] == []
    assert len(payload["检查项"]) >= 10


def test_exit_code_is_one_on_a_broken_synthetic_book(tmp_path, monkeypatch, capsys):
    """**离线等价覆盖**：合成产物被弄坏 → 真不变量报出来 → 退出码 1（不是 2）。"""
    book = synth_book.build(tmp_path, broken="index")
    synth_book.use_for_doctor(monkeypatch, doc, book)
    assert doc.main(["--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["通过"] is False
    assert payload["doctor自己坏了"] == []
    assert {v["不变量"] for v in payload["违规"]} == {"教案索引完整", "历史质量不可继承"}
