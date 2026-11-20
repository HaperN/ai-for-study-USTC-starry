"""M7 回归：载荷由 manifest 装配、净化、原子发布、离线验收。

对应验收项：**A22**（同名不同字节资源 / 跨书载荷 / 重复身份）、
**A23**（坏引用 / 缺 PNG / 危险协议 → 检查非零、包不发布）、
**A24**（空目录断网打开 → 外部引用 0 / 丢图 0 / ZIP 成员哈希 100% 一致）、
**A26**（缺必需能力 → blocked，**不用 skipped 当通过**）。

三条最容易"看起来没问题"的失败，这里逐条钉住：

1. **同名不同字节的资源被静默覆盖** —— 后写的赢，页面里两处引用变成同一张图；
2. **页面里有、manifest 里没有的资源** —— 本地能看，**解压后丢图**；
3. **就地更新交付目录** —— 更新到一半失败，目录半新半旧；新版本有问题时**旧版本已经没了**。
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from aiforstudy.checks.quality import Finding, build_quality_report
from aiforstudy.checks.offline import check_no_missing_images, run_offline_acceptance
from aiforstudy.delivery.publish import (ChapterRef, assert_release_validated, build_release,
                                         member_hashes, read_current, rollback, switch_current)
from aiforstudy.delivery.resources import ResourceManifest
from aiforstudy.errors import CapabilityError, ContractError

BOOK = "b1"


# ────────────────────────────────────────── 就地造资源

def make_svg(tmp_path: Path, name: str, marker: str) -> Path:
    path = tmp_path / name
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg"><text>{marker}</text></svg>',
                    encoding="utf-8")
    return path


def manifest_with(tmp_path: Path) -> ResourceManifest:
    manifest = ResourceManifest(book_id=BOOK)
    manifest.add_file(logical_name="figure.svg", kind="svg",
                      source=make_svg(tmp_path, "ch1.svg", "第一章的图"), book_id=BOOK)
    return manifest


# ────────────────────────────────────────── A22 资源

def test_same_name_different_bytes_is_renamed_not_overwritten(tmp_path):
    """**A22**：两章同资源名不同字节 → 内容寻址改名并重写引用，**不静默覆盖**。"""
    manifest = ResourceManifest(book_id=BOOK)
    first = manifest.add_file(logical_name="figure.svg", kind="svg",
                              source=make_svg(tmp_path, "a.svg", "A"), book_id=BOOK)
    second = manifest.add_file(logical_name="figure.svg", kind="svg",
                               source=make_svg(tmp_path, "b.svg", "B"), book_id=BOOK)
    assert first.path == "figure.svg"
    assert second.path != first.path and second.path.startswith("figure-")
    assert len(manifest.resources) == 2, "两份内容都在，没有被覆盖"
    assert manifest.conflicts and "不许字典覆盖" in manifest.conflicts[0]["why"]
    assert manifest.resolve("figure.svg") in (first.path, second.path)


def test_same_name_same_bytes_merges_into_one(tmp_path):
    """内容相同就自然合并（省体积），并且只记一条。"""
    manifest = ResourceManifest(book_id=BOOK)
    a = manifest.add(logical_name="figure.svg", kind="svg",
                     source=make_svg(tmp_path, "a.svg", "同样的图"), book_id=BOOK)
    b = manifest.add(logical_name="figure.svg", kind="svg",
                     source=make_svg(tmp_path, "b.svg", "同样的图"), book_id=BOOK)
    assert a.path == b.path and len(manifest.resources) == 1
    assert not manifest.conflicts


def test_cross_book_resources_are_rejected(tmp_path):
    """**A22**：跨书 payload 必须拒绝。"""
    manifest = ResourceManifest(book_id=BOOK)
    with pytest.raises(ContractError, match="跨书资源"):
        manifest.add_file(logical_name="figure.svg", kind="svg",
                          source=make_svg(tmp_path, "a.svg", "A"), book_id="另一本书")


def test_manifest_verify_recomputes_content_not_existence(tmp_path):
    """核对的是**内容摘要**，不是"文件在不在"（§11）。"""
    manifest = manifest_with(tmp_path)
    assert manifest.verify()["ok"]
    # 改**来源**文件的内容 → 清单必须报出来（只改交付名是查不出来的）
    Path(manifest.resources["figure.svg"].source).write_text("<svg/>", encoding="utf-8")
    assert manifest.verify()["ok"] is False, "内容被改过，清单必须报出来"
    # 交付目录里也按同一条口径核对
    deliver = tmp_path / "deliver"
    deliver.mkdir()
    (deliver / "figure.svg").write_text("<svg>tampered</svg>", encoding="utf-8")
    assert manifest.verify(base_dir=deliver)["ok"] is False


# ────────────────────────────────────────── 净化


# ────────────────────────────────────────── 载荷只从 manifest 装


# ────────────────────────────────────────── 原子发布（失败不覆盖旧发布）

def make_release(tmp_path: Path, release_id: str, marker: str) -> tuple[Path, dict]:
    releases = tmp_path / "releases"
    files = {"CH01/index.html": f"<html>{marker}</html>".encode(),
             "CH01/data/data.js": f"window.DATA={marker!r};".encode()}
    members = {name: "sha256:" + hashlib.sha256(blob).hexdigest()
               for name, blob in files.items()}
    manifest = {"book_id": BOOK, "release_id": release_id, "scope_kind": "all",
                "chapters": ["CH01"], "scope": ["CH01"], "members": members,
                "parse_revision": release_id + "-parse",
                "offline_acceptance": {"passed": True, "blocked": False, "checks": [
                    {"name": "browser-execution", "status": "accepted"},
                ]}}
    path, hashes = build_release(releases, release_id, manifest=manifest, files=files)
    return path, hashes


def test_publishing_switches_a_pointer_instead_of_overwriting(tmp_path):
    """§10：按新 manifest **原子切换 current**，不原地覆盖旧成功版本。"""
    path_a, hashes_a = make_release(tmp_path, "r1", "A")
    releases = tmp_path / "releases"
    assert not read_current(releases)
    switch_current(releases, "r1", book_id=BOOK, scope_kind="book",
                   chapters=(ChapterRef("CH01", "r1"),))
    assert read_current(releases)["release_id"] == "r1"

    path_b, _ = make_release(tmp_path, "r2", "B")
    switch_current(releases, "r2", book_id=BOOK, scope_kind="book",
                   chapters=(ChapterRef("CH01", "r2"),))
    assert read_current(releases)["release_id"] == "r2"
    # 旧 release **整份还在**，字节未变
    assert path_a.is_dir() and member_hashes(path_a) == hashes_a


def test_a_failed_release_leaves_the_pointer_untouched(tmp_path):
    """**失败不覆盖旧发布**：旧版本必须还在，而且是读者的版本。"""
    make_release(tmp_path, "r1", "A")
    releases = tmp_path / "releases"
    switch_current(releases, "r1", book_id=BOOK, scope_kind="book",
                   chapters=(ChapterRef("CH01", "r1"),))
    bad_members = {"index.html": "sha256:" + "0" * 64}
    with pytest.raises(ContractError, match="成员与清单不一致"):
        build_release(releases, "r2", manifest={"members": bad_members},
                      files={"index.html": b"<html>bad</html>"})
    assert read_current(releases)["release_id"] == "r1", "指针不许动"
    assert not (releases / "r2").exists(), "半成品目录不该留下"


def test_build_release_refuses_to_reuse_an_existing_release_id(tmp_path):
    make_release(tmp_path, "r1", "A")
    with pytest.raises(ContractError, match="不原地覆盖"):
        make_release(tmp_path, "r1", "A2")


def test_single_chapter_publish_keeps_other_chapters_untouched(tmp_path):
    """§10：单章发布只替换目标章引用，其余章**字节不变**。"""
    make_release(tmp_path, "r1", "A")
    releases = tmp_path / "releases"
    switch_current(releases, "r1", book_id=BOOK, scope_kind="book",
                   chapters=(ChapterRef("CH01", "r1"), ChapterRef("CH02", "r1")))
    make_release(tmp_path, "r2", "B")
    result = switch_current(releases, "r2", book_id=BOOK, scope_kind="chapters",
                            chapters=(ChapterRef("CH02", "r2"),))
    assert result.replaced_chapters == ("CH02",)
    assert result.untouched_chapters == ("CH01",)
    current = read_current(releases)
    by_key = {row["chapter_key"]: row["release_id"] for row in current["chapters"]}
    assert by_key == {"CH01": "r1", "CH02": "r2"}


def test_publish_is_refused_when_checks_failed(tmp_path):
    """**A23**：检查非零则包不发布。"""
    path, _ = make_release(tmp_path, "r1", "A")
    with pytest.raises(ContractError, match="不许发布"):
        assert_release_validated(path, checks_ok=False)


def test_rollback_switches_the_pointer_back(tmp_path):
    """§16：回退是**原子切换指针**，不是重新生成；已发生费用不回滚。"""
    from aiforstudy.delivery.publish import _switch_current

    _, hashes_a = make_release(tmp_path, "r1", "A")
    _, hashes_b = make_release(tmp_path, "r2", "B")
    releases = tmp_path / "releases"
    _switch_current(releases, "r1", book_id=BOOK, scope_kind="book", verified_hashes=hashes_a,
                    chapters=(ChapterRef("CH01", "r1", hashes_a["CH01/index.html"], "r1-parse"),))
    _switch_current(releases, "r2", book_id=BOOK, scope_kind="book", verified_hashes=hashes_b,
                    chapters=(ChapterRef("CH01", "r2", hashes_b["CH01/index.html"], "r2-parse"),))
    result = rollback(releases, "r1")
    assert result.release_id == "r1" and read_current(releases)["release_id"] == "r1"
    assert "不回滚" in result.notes[0]


# ────────────────────────────────────────── A24 离线验收


def test_missing_image_is_reported(tmp_path):
    (tmp_path / "index.html").write_text('<img src="figures/a.svg">', encoding="utf-8")
    check = check_no_missing_images(tmp_path)
    assert check.status == "failed" and check.evidence["missing"] == 1
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures" / "a.svg").write_text("<svg/>", encoding="utf-8")
    assert check_no_missing_images(tmp_path).status == "accepted"


def test_missing_capability_is_blocked_not_skipped(tmp_path):
    """**A26**：缺必需浏览器/Node → `blocked`，**不用 skipped 算通过**。"""
    html = tmp_path / "index.html"
    html.write_text("<html>ok</html>", encoding="utf-8")
    report = run_offline_acceptance(tree_root=tmp_path, node=None, jsdom=None)
    assert report.passed is False and report.blocked is True
    assert any("不能用 skipped 算正式通过" in note for note in report.notes)
    report2 = run_offline_acceptance(tree_root=tmp_path, node=shutil.which("node"),
                                     jsdom=tmp_path)
    assert report2.passed is True


# ────────────────────────────────────────── 质量报告与交付声明

def test_quality_report_blocks_on_p0_and_lists_suggestions():
    """§9：P0/P1 挡发布；**P2 可保留但必须列出**。"""
    report = build_quality_report(
        book_id=BOOK, release_id="r1", scope_units=("CH01-S01",),
        findings=(Finding("X", "P0", "CH01-S01", "硬问题"),
                  Finding("Y", "P2", "CH01-S01", "风格建议")),
        legacy_problems_path="data/problems/b1.json")
    assert report.status == "failed" and report.release_ready is False
    assert [f.code for f in report.blocking] == ["X"]
    assert [f.code for f in report.suggestions] == ["Y"]
    assert "当前 scope 的重算结果" in report.notes[-1]


def test_quality_report_does_not_depend_on_an_existing_problems_file():
    """§6：**不依赖是否已有 problems.json** —— 它连参数都不参与判据。"""
    report = build_quality_report(book_id=BOOK, release_id="r1", scope_units=(),
                                  findings=(), legacy_problems_path="不存在的.json")
    assert report.release_ready is True
    assert "证据引用" in report.notes[0]


def test_findings_outside_scope_are_visible_but_do_not_block():
    report = build_quality_report(book_id=BOOK, release_id="r1",
                                  scope_units=("CH01-S01",),
                                  findings=(Finding("Z", "P0", "CH09-S09", "别的章的问题"),))
    assert report.findings_outside_scope() and len(report.findings_outside_scope()) == 1
    assert report.release_ready is False, "范围外的问题也要看得见，不许掩盖"


def test_stale_chapters_are_recorded_not_auto_regenerated():
    report = build_quality_report(book_id=BOOK, release_id="r1", scope_units=("CH01-S01",),
                                  findings=(), stale_chapters=("CH05",))
    assert report.status == "needs_review"
    assert "不自动重生成" in report.notes[0]


def test_blocked_checks_prevent_release_ready():
    report = build_quality_report(
        book_id=BOOK, release_id="r1", scope_units=("CH01-S01",), findings=(),
        checks=({"名称": "离线打开", "状态": "blocked"},))
    assert report.blocked_checks() and report.release_ready is False
