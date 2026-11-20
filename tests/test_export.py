"""导出层回归测试。

锁住的是任务基线里 T1.4 的四条验收判据：
  ① 章节树与 block 一一对应（无遗漏、无重复）
  ② 每个 .md 逐字节等于 block 的 raw_markdown（原文零改写）
  ③ manifest 列出的产物都存在且 sha256 对得上
  ④ 每个 JSON 都有 _meta（全局约束 G3）
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "blocks" / "parse" / "export"))

import export_parse as E  # noqa: E402


def _block(block_id, kind, anchor, title, body, lines):
    return {
        "block_id": block_id,
        "block_type": kind,
        "anchor": anchor,
        "raw_markdown": f"{title}\n{body}",
        "normalized_markdown": f"{'#' * {'chapter': 1, 'section': 2, 'subsection': 3}.get(kind, 0)} {title}\n{body}",
        "source_lines": lines,
        "content_sha256": hashlib.sha256(f"{title}{body}".encode()).hexdigest(),
        "disposition": "unprocessed",
    }


@pytest.fixture
def project(tmp_path):
    """一份最小但结构完整的输入：前置件 + 1 章 + 2 节 + 3 小节。"""
    blocks = [
        dict(_block("CH00-S00-SS00-B0001", "toc", None, "封面", "", [1, 2]),
             block_type="toc", disposition="toc_only"),
        _block("CH01-S00-SS00-B0002", "chapter", "1", "第1章 向量", "本章引言。", [3, 5]),
        _block("CH01-S01-SS00-B0003", "section", "1.1", "§1.1 线性运算", "1.1 的引言。", [6, 8]),
        _block("CH01-S01-SS01-B0004", "subsection", "1.1.1", "1.1.1 向量及其表示", "正文甲。", [9, 20]),
        _block("CH01-S01-SS02-B0005", "subsection", "1.1.2", "1.1.2 向量的运算", "正文乙。", [21, 30]),
        _block("CH01-S02-SS00-B0006", "section", "1.2", "§1.2 坐标系", "1.2 没有小节，自己就是正文。", [31, 40]),
    ]
    source = tmp_path / "normalized-source.jsonl"
    source.write_text("".join(json.dumps(b, ensure_ascii=False) + "\n" for b in blocks),
                      encoding="utf-8")
    out = tmp_path / "parse"
    result = E.export_parse(source, out, book_title="测试教材")
    return {"source": source, "out": out, "result": result, "blocks": blocks}


# ── ① 章节树与 block 一一对应 ───────────────────────────────────────────
def test_every_block_is_assigned_exactly_once(project):
    chapters = json.loads((project["out"] / "chapters.json").read_text(encoding="utf-8"))

    assigned: list[str] = []

    def walk(node):
        assigned.extend(node["block_ids"])
        for key in ("sections", "subsections"):
            for child in node.get(key, []):
                walk(child)

    for chapter in chapters["chapters"]:
        walk(chapter)

    assert len(assigned) == len(set(assigned)), "有 block 被挂到多个节点"
    front = [b["block_id"] for b in project["blocks"]
             if b["block_type"] == "toc" or b.get("disposition") == "toc_only"]
    all_ids = {b["block_id"] for b in project["blocks"]}
    assert set(assigned) | set(front) == all_ids, "有 block 没有归属"


def test_tree_shape(project):
    chapters = json.loads((project["out"] / "chapters.json").read_text(encoding="utf-8"))
    assert len(chapters["chapters"]) == 1
    chapter = chapters["chapters"][0]
    assert chapter["id"] == "CH01"
    assert [s["id"] for s in chapter["sections"]] == ["CH01-S01", "CH01-S02"]
    assert [ss["id"] for ss in chapter["sections"][0]["subsections"]] == \
        ["CH01-S01-SS01", "CH01-S01-SS02"]
    assert chapter["sections"][1]["subsections"] == []      # 没有小节的节
    assert chapters["frontmatter"] == "original_text/_frontmatter.md"


def test_lesson_units_include_every_node_with_content(project):
    chapters = json.loads((project["out"] / "chapters.json").read_text(encoding="utf-8"))
    units = chapters["lesson_units"]
    by_id = {u["unit_id"]: u for u in units}
    # 章有导言 → 章导言也是一个单元（曾经漏掉过，导言等于没人处理）
    assert by_id["CH01"]["level"] == "chapter_intro"
    # 有小节的节 → 节的引言单独一个单元 + 每个小节一个单元
    assert by_id["CH01-S01"]["level"] == "section_intro"
    assert by_id["CH01-S01-SS01"]["level"] == "subsection"
    assert by_id["CH01-S01-SS02"]["level"] == "subsection"
    # 没有小节的节 → 它自己就是单元
    assert by_id["CH01-S02"]["level"] == "section"
    assert len(units) == 5
    # 不变量：有正文的节点必须都能在 lesson_units 里找到（否则教案块会漏内容）
    def nodes(node):
        yield node
        for child in node.get("subsections") or node.get("sections") or []:
            yield from nodes(child)
    with_content = [n["id"] for c in chapters["chapters"] for n in nodes(c) if n["block_ids"]]
    assert set(with_content) <= set(by_id), set(with_content) - set(by_id)


# ── ② 原文零改写 ────────────────────────────────────────────────────────
def test_original_text_is_byte_identical_to_raw_markdown(project):
    chapters = json.loads((project["out"] / "chapters.json").read_text(encoding="utf-8"))
    by_id = {b["block_id"]: b for b in project["blocks"]}
    checked = 0
    for unit in chapters["lesson_units"]:
        node = _find_node(chapters["chapters"], unit["unit_id"])
        text = (project["out"] / node["original_text"]).read_text(encoding="utf-8")
        expected = "\n\n".join(by_id[i]["raw_markdown"].rstrip("\n")
                               for i in node["block_ids"]) + "\n"
        assert text == expected, f"{node['id']} 的原文与 raw_markdown 不一致"
        checked += 1
    assert checked == len(chapters["lesson_units"])


def test_original_text_files_are_not_rewritten_by_a_second_export(project):
    """G5：再跑一次导出，原文文件的 sha256 必须一个字节都不变。"""
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (project["out"] / "original_text").glob("*.md")}
    E.export_parse(project["source"], project["out"], book_title="测试教材")
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in (project["out"] / "original_text").glob("*.md")}
    assert before == after


def test_stale_original_text_is_removed_but_other_blocks_are_untouched(project):
    """核心坑：结构变过之后（合并/拆开/改层级），上一次导出留下的原文文件会残留，
    跟 chapters.json 对不上 —— 实测在真教材上残留了 5 个（旧的 1.6.x 和整个 1.9）。

    只准删本层自己产的 original_text/*.md，别碰别的块的文件。"""
    text_dir = project["out"] / "original_text"
    (text_dir / "ch01_s99_ss09.md").write_text("旧结构留下的垃圾\n", encoding="utf-8")
    keep = project["out"] / "figures.json"
    keep.write_text('{"_meta": {}}\n', encoding="utf-8")

    result = E.export_parse(project["source"], project["out"], book_title="测试教材")

    assert not (text_dir / "ch01_s99_ss09.md").exists()
    assert keep.is_file(), "别的块的文件被误删了"
    assert result["removed_stale"] == ["ch01_s99_ss09.md"]
    manifest = json.loads((project["out"] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["removed_stale"] == ["ch01_s99_ss09.md"]
    for row in manifest["produced"]:
        assert (project["out"] / row["path"]).is_file()


# ── ③ manifest 完整性 ───────────────────────────────────────────────────
def test_manifest_lists_every_product_with_correct_hash(project):
    manifest = json.loads((project["out"] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["produced"], "manifest 没列出任何产物"
    for row in manifest["produced"]:
        path = project["out"] / row["path"]
        assert path.is_file(), f"manifest 列了不存在的文件：{row['path']}"
        data = path.read_bytes()
        assert len(data) == row["bytes"]
        assert hashlib.sha256(data).hexdigest() == row["sha256"]
    listed = {row["path"] for row in manifest["produced"]}
    # 三个索引文件也必须被列进去
    assert {"chapters.json", "images.json", "tables.json"} <= listed


def test_manifest_counts_match_reality(project):
    manifest = json.loads((project["out"] / "manifest.json").read_text(encoding="utf-8"))
    counts = manifest["counts"]
    assert counts["blocks_total"] == len(project["blocks"])
    assert counts["chapters"] == 1
    assert counts["sections"] == 2
    assert counts["subsections"] == 2
    assert counts["lesson_units"] == 5
    assert counts["original_text_files"] == len(list((project["out"] / "original_text").glob("*.md")))


# ── ④ 每个 JSON 都有 _meta（G3） ────────────────────────────────────────
@pytest.mark.parametrize("name", ["manifest", "chapters", "images", "tables"])
def test_every_json_has_meta(project, name):
    payload = json.loads((project["out"] / f"{name}.json").read_text(encoding="utf-8"))
    meta = payload["_meta"]
    assert meta["file_type"] == name
    assert meta["schema_version"]
    assert meta["generator"] == E.GENERATOR
    assert meta["source_sha256"]
    assert meta["field_notes"]


# ── 图片：只抽 markdown 里的引用，不伪造 ────────────────────────────────
def test_images_collects_markdown_refs_without_faking_files(tmp_path):
    blocks = [
        _block("CH01-S00-SS00-B0001", "chapter", "1", "第1章", "", [1, 2]),
        dict(_block("CH01-S01-SS01-B0002", "subsection", "1.1.1", "1.1.1 图",
                    "看图 ![加法示意图](assets/add.png)", [3, 10]),
             normalized_markdown="### 1.1.1 图\n看图 ![加法示意图](assets/add.png)"),
    ]
    source = tmp_path / "normalized-source.jsonl"
    source.write_text("".join(json.dumps(b, ensure_ascii=False) + "\n" for b in blocks),
                      encoding="utf-8")
    E.export_parse(source, tmp_path / "parse")
    images = json.loads((tmp_path / "parse" / "images.json").read_text(encoding="utf-8"))
    assert len(images["images"]) == 1
    image = images["images"][0]
    assert image["id"] == "IMG-0001"
    assert image["caption"] == "加法示意图"
    assert image["occurrences"][0]["unit_id"] == "CH01-S01-SS01"
    # 尚未提取 → file/sha256 必须是 null，不能编一个出来
    assert image["file"] is None and image["sha256"] is None


def test_tables_is_empty_not_fake(tmp_path):
    blocks = [_block("CH01-S00-SS00-B0001", "chapter", "1", "第1章", "", [1, 2])]
    source = tmp_path / "normalized-source.jsonl"
    source.write_text("".join(json.dumps(b, ensure_ascii=False) + "\n" for b in blocks),
                      encoding="utf-8")
    E.export_parse(source, tmp_path / "parse")
    tables = json.loads((tmp_path / "parse" / "tables.json").read_text(encoding="utf-8"))
    assert tables["tables"] == []
    assert "未提供" in tables["note"]


def test_source_table_html_and_location_survive_export(project):
    table = {'id': 'IMG-0042', 'kind': 'table', 'page': 12,
             'source_block_ids': ['P0012-B0003'], 'source_lines': [91, 91],
             'table_html': '<table><tr><td>5</td><td>20</td></tr></table>',
             'file': 'images/table.jpg', 'bbox': [10, 20, 50, 60]}
    sidecar = project['out'] / 'source.blocks.json'
    sidecar.write_text(json.dumps({'_meta': {'document_sha256': 'doc'}, 'assets': [table]}))
    E.export_parse(project['source'], project['out'])
    exported = json.loads((project['out'] / 'tables.json').read_text())
    assert exported['tables'] == [table]
    assert exported['_meta']['source_blocks_sha256'] == hashlib.sha256(sidecar.read_bytes()).hexdigest()


# ── 输入校验 ────────────────────────────────────────────────────────────
def test_bad_input_is_rejected_loudly(tmp_path):
    missing = tmp_path / "nope.jsonl"
    with pytest.raises(ValueError, match="找不到"):
        E.export_parse(missing, tmp_path / "out")
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="空的"):
        E.export_parse(empty, tmp_path / "out")
    broken = tmp_path / "broken.jsonl"
    broken.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="不是合法 JSON"):
        E.export_parse(broken, tmp_path / "out")
    no_id = tmp_path / "no_id.jsonl"
    no_id.write_text('{"block_type": "chapter"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="缺少 block_id"):
        E.export_parse(no_id, tmp_path / "out")


def _find_node(chapters, node_id):
    stack = list(chapters)
    while stack:
        node = stack.pop()
        if node["id"] == node_id:
            return node
        stack.extend(node.get("sections", []))
        stack.extend(node.get("subsections", []))
    raise AssertionError(f"树里找不到节点 {node_id}")


# ── 习题区标记不是教案单元（2026-09-27 实测《数分讲义第二册》）────────────────
@pytest.mark.parametrize("title", [
    "习题", "习题一", "习题十二", "习题 8.1", "习题11.1", "练习 2",
    "第 8 章综合习题", "综合习题", "复习题三", "总习题",
])
def test_exercise_marker_titles_are_recognized(title):
    """判据必须和结构层 `structure.EXERCISE_MARK` 一样宽。

    原来这里只认 `习题`/`习题一`，于是 `习题11.1` 被当成小节进了 `lesson_units`
    （`CH04-S02 习题11.1`）—— 白跑一个单元的生成与审核。
    """
    assert E.EXERCISE_TITLE.match(title), title


@pytest.mark.parametrize("title", ["§1.1 线性运算", "1.1.1 向量及其表示", "习题课", "综合习题课"])
def test_non_exercise_titles_are_not_markers(title):
    assert not E.EXERCISE_TITLE.match(title), title


def test_exercise_marker_section_is_kept_out_of_lesson_units():
    """`习题11.1` 这种节标题要留在树里当边界，但**不能**成为教案单元。"""
    blocks = [
        _block("CH01-S00-SS00-B0001", "chapter", "1", "第1章 向量", "引言。", [1, 2]),
        _block("CH01-S01-SS00-B0002", "section", "1.1", "§1.1 线性运算", "正文甲。", [3, 6]),
        _block("CH01-S02-SS00-B0003", "section", "1.2", "习题11.1", "1. 求下列向量组的秩", [7, 9]),
        _block("CH01-S03-SS00-B0004", "section", "1.3", "§1.3 方程组", "正文乙。", [10, 14]),
    ]
    chapters, _front = E.build_tree(blocks)
    units = E.build_lesson_units(chapters)
    titles = [u["title"] for u in units]
    assert "习题11.1" not in titles, "习题区标记混进了教案单元"
    assert "线性运算" in titles and "方程组" in titles


def test_heading_only_nodes_are_not_lesson_units():
    """**只有标题**的节点不是教案单元（`_has_body`）。

    实测《数值计算方法》：`## §3.1 迭代法`（19 字节，只有标题）也进了 `lesson_units`，
    教案层会拿它去生成讲解 —— 费 token 还容易编出不存在的内容。
    """
    blocks = [
        _block("CH01-S00-SS00-B0001", "chapter", "1", "第1章 向量", "本章导言。", [1, 2]),
        _block("CH01-S01-SS00-B0002", "section", "1.1", "§1.1 线性运算", "", [3, 3]),   # 只有标题
        _block("CH01-S01-SS01-B0003", "subsection", "1.1.1", "1.1.1 向量及其表示", "正文甲。", [4, 8]),
        _block("CH01-S02-SS00-B0004", "section", "1.2", "§1.2 坐标系", "正文乙。", [9, 12]),
    ]
    chapters, _front = E.build_tree(blocks)
    units = {u["unit_id"]: u for u in E.build_lesson_units(chapters)}
    assert "CH01" in units and units["CH01"]["level"] == "chapter_intro"
    assert "CH01-S01" not in units, "只有标题的节被当成了教案单元"
    assert "CH01-S01-SS01" in units, "小节的正文没进单元清单"
    assert "CH01-S02" in units and units["CH01-S02"]["level"] == "section"
