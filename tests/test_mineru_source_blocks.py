"""Offline provenance and running-header regressions; no real-book writes."""
import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from test_mineru_adapt import A, block, page, text, title, write_layout


def placed(kind, value, y):
    return block(kind, value, bbox=[100, y, 400, y + 14])


def numbered_page(idx, *blocks):
    return page(idx, *blocks, discarded=[placed("page_number", str(idx + 1), 95)])


def read_source(out):
    path = out / "source.blocks.json"
    assert path.is_file(), "Every adaptation must publish inspectable source blocks"
    return json.loads(path.read_text())


def test_repeated_exercise_titles_survive_across_chapters(tmp_path):
    pages = [numbered_page(0, placed("title", "第一章 向量", 150),
                           placed("title", "习题一", 400), text("1. 证明命题。")),
             numbered_page(1, placed("title", "第二章 方程", 150),
                           placed("title", "习题一", 400), text("1. 求解方程。"))]
    result = A.adapt(write_layout(tmp_path, pages), tmp_path / "out")
    assert result.document.splitlines().count("习题一") == 2
    source = read_source(tmp_path / "out")
    exercises = [b for b in source["blocks"] if b["rendered_text"] == "习题一"]
    assert len(exercises) == 2 and all(b["source_lines"] for b in exercises)


def test_same_page_header_does_not_start_exercise_before_real_title(tmp_path):
    pages = [numbered_page(0, placed("title", "第四章 矩阵", 160)),
             numbered_page(1, placed("title", "习题三", 94),
                           placed("text", "凯莱发展了矩阵理论。", 125),
                           placed("title", "习题三", 408), text("1. 计算矩阵。"))]
    out = tmp_path / "out"
    result = A.adapt(write_layout(tmp_path, pages), out)
    assert result.document.index("凯莱") < result.document.index("习题三")
    headings = json.loads((out / "headings.json").read_text())["headings"]
    assert [h["line"] for h in headings if h["text"] == "习题三"] == [5]
    records = read_source(out)["blocks"]
    header = next(b for b in records if b["page"] == 2 and b["page_order"] == 0)
    assert header["source_lines"] is None and header["reason"] == "running_header"
    history = next(b for b in records if "凯莱" in b["rendered_text"])
    assert history["region"] == "body"


def test_repeated_section_headers_do_not_end_exercise_region(tmp_path):
    pages = [numbered_page(0, placed("title", "第五章 线性空间", 150),
                          placed("title", "§5.7 子空间运算*", 300)),
             numbered_page(1, placed("title", "习题五", 300), text("1. 证明。")),
             numbered_page(2, placed("title", "§5.7 子空间运算*", 94), text("11. 证明。")),
             numbered_page(3, placed("text", "§5.7 子空间运算*", 94), text("30. 证明。"))]
    out = tmp_path / "out"
    result = A.adapt(write_layout(tmp_path, pages), out)
    assert result.document.count("§5.7 子空间运算*") == 1
    assert all(b["region"] == "exercise" for b in read_source(out)["blocks"]
               if b["rendered_text"] in ("11. 证明。", "30. 证明。"))


def test_source_keeps_raw_discarded_formula_and_table_html_with_exact_paths(tmp_path):
    table = block("table", lines=[], bbox=[80, 200, 440, 400], blocks=[
        {"type": "table_body", "lines": [{"spans": [
            {"type": "table", "image_path": "images/nutrition.jpg"}]}]}])
    raw = [numbered_page(0, placed("title", "习题一", 150),
                        text("7. 计算每日营养摄入。"), table,
                        block("interline_equation", lines=[{"spans": [
                            {"type": "interline_equation", "content": "x=1"}]}]))]
    snapshot = copy.deepcopy(raw)
    mineru = write_layout(tmp_path, raw)
    (mineru / "images").mkdir()
    (mineru / "images" / "nutrition.jpg").write_bytes(b"crop")
    html = "<table><tr><td>蛋白质</td><td>5</td></tr></table>"
    (mineru / "content_list.json").write_text(json.dumps([
        {"type": "table", "page_idx": 0, "img_path": "images/nutrition.jpg", "table_body": html}]))
    out = tmp_path / "out"
    result = A.adapt(mineru, out)
    source = read_source(out)
    assert [b["raw_content"] for b in source["blocks"]] == snapshot[0]["para_blocks"] + snapshot[0]["discarded_blocks"]
    assert len(source["blocks"]) == 5
    table_record = source["blocks"][2]
    asset = source["assets"][0]
    assert asset["table_html"] == html and html in result.document
    assert asset["source_block_ids"] == [table_record["block_id"]]
    assert table_record["asset_ids"] == [asset["id"]]
    assert asset["bbox"] == [80, 200, 440, 400] and asset["coordinate_system"] == "pdf_points"
    assert asset["sha256"] == hashlib.sha256(b"crop").hexdigest()
    assert (out / asset["local_path"]).read_bytes() == b"crop"
    assert source["_meta"]["document_sha256"] == hashlib.sha256(result.document.encode()).hexdigest()
    for b in source["blocks"]:
        if b["source_lines"]:
            lo, hi = b["source_lines"]
            assert "\n".join(result.lines[lo-1:hi]) == b["rendered_text"]
    assert source["blocks"][-1]["disposition"] == "discarded"
    assert source["blocks"][-1]["source_lines"] is None
    assert result.figures["images"][0]["source_block_ids"] == [table_record["block_id"]]


def test_missing_image_never_reuses_an_unrelated_same_page_image(tmp_path):
    figure = block("image", lines=[], blocks=[{"type": "image_body", "lines": [{
        "spans": [{"type": "image", "image_path": "missing.jpg"}]}]}])
    mineru = write_layout(tmp_path, [page(0, figure)])
    (mineru / "images").mkdir()
    (mineru / "images" / "page_0_image_9.jpg").write_bytes(b"other")
    result = A.adapt(mineru, tmp_path / "out")
    assert result.figures["images"] == []
    source = read_source(tmp_path / "out")
    assert source["assets"][0]["local_path"] is None
    assert source["assets"][0]["status"] == "missing"


@pytest.mark.parametrize("malformed", [None, {}, [{"page_idx": 0, "para_blocks": "oops"}],
                                      [{"page_idx": 0, "para_blocks": [{"type": "text", "lines": "oops"}]}]])
def test_unsupported_layout_shape_fails_before_outputs(tmp_path, malformed):
    mineru = write_layout(tmp_path, [])
    (mineru / "layout.json").write_text(json.dumps({"_version_name": "3.4.4", "pdf_info": malformed}))
    with pytest.raises(A.MinerUFormatError):
        A.adapt(mineru, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_currentness_rejects_document_input_and_asset_changes(tmp_path):
    mineru = write_layout(tmp_path, [page(0, text("正文"))])
    out = tmp_path / "out"
    A.adapt(mineru, out)
    assert (out / "source.blocks.json").is_file()
    from source_blocks import adaptation_is_current
    assert adaptation_is_current(out, mineru)
    (out / "document.md").write_text("篡改正文")
    assert not adaptation_is_current(out, mineru)
    A.adapt(mineru, out)
    (mineru / "content_list.json").write_text("[]")
    assert not adaptation_is_current(out, mineru)


def test_currentness_rejects_modified_source_block_payload(tmp_path):
    mineru = write_layout(tmp_path, [page(0, text("正文"))])
    out = tmp_path / "out"
    A.adapt(mineru, out)
    from source_blocks import adaptation_is_current
    source = read_source(out)
    source["blocks"][0]["raw_content"]["lines"][0]["spans"][0]["content"] = "变更"
    (out / "source.blocks.json").write_text(json.dumps(source))
    assert not adaptation_is_current(out, mineru)


def test_table_variants_are_preserved_instead_of_erasing_html(tmp_path):
    raw_html = "<table><tr><td>x</td></tr></table>"
    other_html = "<table><tr><td>$x$</td></tr></table>"
    table = block("table", lines=[], blocks=[{"type": "table_body", "lines": [{"spans": [
        {"type": "table", "image_path": "table.jpg", "html": raw_html}]}]}])
    mineru = write_layout(tmp_path, [page(0, table)])
    (mineru / "content_list.json").write_text(json.dumps([
        {"type": "table", "page_idx": 0, "img_path": "table.jpg", "table_body": other_html}]))
    out = tmp_path / "out"
    result = A.adapt(mineru, out)
    asset = read_source(out)["assets"][0]
    assert raw_html in result.document
    assert asset["table_html"] == raw_html
    assert set(asset["table_html_variants"]) == {raw_html, other_html}
    assert asset["needs_review"]


def test_lone_heading_in_page_number_band_is_not_suppressed(tmp_path):
    result = A.adapt(write_layout(tmp_path, [numbered_page(0, placed("title", "Exercises", 94))]),
                     tmp_path / "out")
    assert "Exercises" in result.document


@pytest.mark.parametrize("nested_kind", ["title", "image_caption"])
def test_figure_preserves_detached_nested_section_heading(tmp_path, nested_kind):
    nested = placed(nested_kind, "§3.2 行列式的性质", 634)
    figure = block("image", lines=[], bbox=[194, 380, 353, 528], blocks=[
        {"type": "image_body", "lines": [{"spans": [{"type": "image", "image_path": "figure.jpg"}]}]},
        placed("image_caption", "图3.2 行列式的几何意义", 545), nested])
    mineru = write_layout(tmp_path, [numbered_page(0, figure, text("本节正文。"))])
    out = tmp_path / "out"
    result = A.adapt(mineru, out)
    assert "§3.2 行列式的性质" in result.document
    headings = json.loads((out / "headings.json").read_text())["headings"]
    section = next(h for h in headings if h["text"] == "§3.2 行列式的性质")
    assert section["source_block_id"] == "p00001-b0000"
    assert section["bbox"] == [100, 634, 400, 648]
    row = read_source(out)["blocks"][0]
    fragment = next(f for f in row["fragments"] if f["source_path"] == ["blocks", 2])
    assert fragment["source_lines"] == [section["line"], section["line"]]
    assert result.lines[section["line"]-1] == "§3.2 行列式的性质"


def test_currentness_checks_assets_in_the_selected_raw_copy(tmp_path):
    figure = block("image", lines=[], blocks=[{"type": "image_body", "lines": [{
        "spans": [{"type": "image", "image_path": "figure.jpg"}]}]}])
    mineru = write_layout(tmp_path, [page(0, figure)])
    (mineru / "images").mkdir()
    (mineru / "images" / "figure.jpg").write_bytes(b"original crop")
    out = tmp_path / "out"
    A.adapt(mineru, out)
    from source_blocks import adaptation_is_current
    copied = tmp_path / "copied-mineru"
    shutil.copytree(mineru, copied)
    assert adaptation_is_current(out, copied)
    (copied / "images" / "figure.jpg").write_bytes(b"different crop")
    assert not adaptation_is_current(out, copied)
    assert adaptation_is_current(out, mineru)
    local = read_source(out)["assets"][0]["local_path"]
    (out / local).write_bytes(b"broken output crop")
    assert not adaptation_is_current(out, mineru)


@pytest.mark.parametrize("heading,expected", [("Exercises", "exercise"), ("Review Exercises 2.1", "exercise"),
    ("Problems 4", "exercise"), ("Answers", "answer"), ("Solutions 2.1", "answer"),
    ("Exercises help students learn", None), ("Solutions exist when x=0", None)])
def test_english_region_titles_are_anchored(heading, expected):
    assert A.region_mark(heading) == expected
