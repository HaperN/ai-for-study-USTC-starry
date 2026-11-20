"""图形块测试：画得对不对，靠断言，不靠眼睛。

这台机器没有浏览器，所以"图形对不对"必须能机械判定。测试里既证明"好图能过"，
也证明"坏图一定被抓"—— 后者才是校验器存在的意义。
"""
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "blocks" / "figures"))

import library as LIB                # noqa: E402
import vector_parallelogram as V     # noqa: E402
import verify_svg as S               # noqa: E402

matplotlib = pytest.importorskip("matplotlib", reason="没装 matplotlib，跳过图形测试")


@pytest.fixture
def figure(tmp_path):
    produced = V.draw(tmp_path)
    return {"svg": Path(produced["svg"]), "png": Path(produced["png"]),
            "geometry": Path(produced["geometry"]), "dir": tmp_path}


def test_draw_produces_svg_png_and_geometry(figure):
    assert figure["svg"].is_file() and figure["svg"].stat().st_size > 500
    assert figure["png"].is_file()
    assert figure["geometry"].is_file()


def test_drawn_figure_passes_every_check(figure):
    result = S.verify(figure["svg"], figure["geometry"])
    assert result["ok"], result["problems"]


def test_labels_are_real_text_not_outlines(figure):
    """标注必须是 <text>：能被搜索、能被选中，也能被校验（默认会被转成路径）。"""
    root = ET.fromstring(figure["svg"].read_text(encoding="utf-8"))
    texts = [e.text for e in root.iter() if e.tag.endswith("text")]
    assert "a" in texts and "b" in texts and "a+b" in texts


def test_svg_is_vector_and_theme_aware(figure):
    text = figure["svg"].read_text(encoding="utf-8")
    assert "base64" not in text, "内嵌位图就不是矢量了"
    assert "currentColor" in text, "颜色写死了，深色主题下看不见"
    assert "#000000" not in text and "black" not in text


def test_all_five_elements_are_present(figure):
    root = ET.fromstring(figure["svg"].read_text(encoding="utf-8"))
    ids = {e.get("id") for e in root.iter() if e.get("id")}
    for name in S.EXPECTED_IDS:
        assert name in ids, f"少了图元 {name}（实际有：{sorted(i for i in ids if i)}）"


def _tamper(figure, **changes):
    payload = json.loads(figure["geometry"].read_text(encoding="utf-8"))
    payload.update(changes)
    figure["geometry"].write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_moved_point_is_caught(figure):
    """把 C 挪一下 → 不再是平行四边形 → 必须报错。"""
    payload = json.loads(figure["geometry"].read_text(encoding="utf-8"))
    display = payload["display"]
    display["C"] = [display["C"][0] + 40, display["C"][1] + 40]
    _tamper(figure, display=display)
    result = S.verify(figure["svg"], figure["geometry"])
    assert not result["ok"]
    # 通用化之后，平行四边形的关系由它自己声明的 relations 判，报的是"位移不相等"
    assert any("位移不相等" in p for p in result["problems"]), result["problems"]


def test_label_outside_canvas_is_caught(figure):
    """核心坑：第一版几何全对，但 "a+b" 被顶到画布外，PNG 里只剩 "a+"。
    "算得对"不等于"看得见"，这条专门查它。"""
    payload = json.loads(figure["geometry"].read_text(encoding="utf-8"))
    width, height = payload["canvas"]
    payload["label_boxes"]["a+b"] = [width - 5, 10, width + 60, 40]
    _tamper(figure, label_boxes=payload["label_boxes"])
    result = S.verify(figure["svg"], figure["geometry"])
    assert not result["ok"]
    assert any("超出画布" in p for p in result["problems"])


def test_embedded_bitmap_is_caught(figure):
    text = figure["svg"].read_text(encoding="utf-8")
    figure["svg"].write_text(text.replace("</svg>", '<image href="data:image/png;base64,AAA"/></svg>'),
                             encoding="utf-8")
    result = S.verify(figure["svg"], figure["geometry"])
    assert not result["ok"]
    assert any("base64" in p for p in result["problems"])


def test_missing_element_is_caught(figure):
    text = figure["svg"].read_text(encoding="utf-8")
    figure["svg"].write_text(text.replace('id="vec-b"', 'id="vec-b-renamed"'),
                             encoding="utf-8")
    result = S.verify(figure["svg"], figure["geometry"])
    assert not result["ok"]
    assert any("少了这些图元" in p for p in result["problems"])


def test_cli_draw_then_verify(tmp_path):
    """命令行：画一次、校验一次，都要以 0 退出。"""
    draw = subprocess.run([sys.executable, "-m", "blocks.figures.cli", "draw",
                           "--out", str(tmp_path)],
                          cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert draw.returncode == 0, draw.stdout + draw.stderr
    verify = subprocess.run([sys.executable, "-m", "blocks.figures.cli", "verify",
                             "--out", str(tmp_path)],
                            cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert verify.returncode == 0, verify.stdout + verify.stderr
    summary = json.loads(verify.stdout)
    assert summary["未通过"] == 0 and summary["通过"] == summary["画了"]


# ── 图库（声明式）：新增一张图必须**自带数学断言**并且能过 ──────────────
def test_every_library_figure_draws_and_passes_its_own_assertions(tmp_path):
    """这是图库的核心保证：图里的数学关系由断言证明，不是"看着像"。
    新加一张图如果断言写错或坐标画错，这条会立刻红。"""
    assert LIB.LIBRARY, "图库是空的？"
    for spec in LIB.LIBRARY:
        produced = LIB.draw(spec, tmp_path)
        result = S.verify(produced["svg"], produced["geometry"])
        assert result["ok"], f"{spec['name']} 没通过断言：{result['problems']}"


def test_every_library_figure_declares_assertions_and_labels(tmp_path):
    """每张图都必须声明"它该有什么图元、标注挂在哪、数学关系是什么"——
    否则校验器就只剩"文件生成了"这种没意义的检查。"""
    for spec in LIB.LIBRARY:
        produced = LIB.draw(spec, tmp_path)
        payload = json.loads(Path(produced["geometry"]).read_text(encoding="utf-8"))
        assert payload["expected_ids"], f"{spec['name']} 没声明图元"
        assert payload["labels"], f"{spec['name']} 没声明标注"
        assert payload["anchors"], f"{spec['name']} 没声明标注挂点"
        has_math = (payload["closes"] or payload["relations"] or payload["lengths"]
                    or payload["angles"] or payload["directions"])
        assert has_math, f"{spec['name']} 没有任何数学断言（那就只能「看着像」了）"


def test_wrong_geometry_is_caught_by_the_figures_own_assertions(tmp_path):
    """把数乘图里 0.5a 那条画成 1.6a 那么长 → 长度比断言必须报错。
    （这个错我真犯过：第一版 0.5a 和 1.6a 一样长。）"""
    spec = LIB.by_name("vector-scalar")
    produced = LIB.draw(spec, tmp_path)
    payload = json.loads(Path(produced["geometry"]).read_text(encoding="utf-8"))
    # 把 B 点挪到和 C 一样远
    payload["display"]["B"] = list(payload["display"]["C"])
    Path(produced["geometry"]).write_text(json.dumps(payload, ensure_ascii=False),
                                         encoding="utf-8")
    result = S.verify(produced["svg"], produced["geometry"])
    assert not result["ok"]
    assert any("长度比不对" in p for p in result["problems"]), result["problems"]


def test_direction_assertion_catches_a_wrong_flip(tmp_path):
    """把"反向量"画成同向 → 方向断言必须报错。"""
    spec = LIB.by_name("vector-scalar")
    produced = LIB.draw(spec, tmp_path)
    payload = json.loads(Path(produced["geometry"]).read_text(encoding="utf-8"))
    o, a = payload["display"]["O"], payload["display"]["A"]
    # 把 D→E 改成正向（和 a 同向）
    payload["display"]["D"] = list(o)
    payload["display"]["E"] = [o[0] + abs(a[0] - o[0]), o[1]]
    Path(produced["geometry"]).write_text(json.dumps(payload, ensure_ascii=False),
                                         encoding="utf-8")
    result = S.verify(produced["svg"], produced["geometry"])
    assert not result["ok"]
    assert any("方向不对" in p for p in result["problems"]), result["problems"]


def test_cli_draws_the_whole_library(tmp_path):
    draw = subprocess.run([sys.executable, "-m", "blocks.figures.cli", "draw",
                           "--out", str(tmp_path), "--name", "all"],
                          cwd=ROOT, capture_output=True, text=True, timeout=600)
    assert draw.returncode == 0, draw.stdout + draw.stderr
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    names = {row["name"] for row in index["figures"]}
    # 整库 = 向量图 + 新图种（电路/曲线/3D，见 spec_extra.EXTRA_LIBRARY）；
    # vector-parallelogram 是更早的独立模块（不在图库里，但 CLI 也会画）
    assert {"vector-parallelogram"} | {s["name"] for s in LIB.ALL_FIGURES} == names
    # 登记表里的图必须都带"服务哪些概念"（渲染层靠它做概念配图）。
    # ⚠️ 2026-09-25 起改成**按名字声明**（`concept_names`），id 由 rehang 按当前概念表算：
    # 只登记 id 的图，在概念表重抽之后会配错概念（P-020/P-032 就是这么来的）。
    for row in index["figures"]:
        assert row["concept_names"], f"{row['name']} 没声明服务哪些概念"
        assert row["checks"]["ok"] is True
    # rehang：拿一份"名字齐全"的概念表去挂，id 必须挂上；名字对不上的一律不挂
    table = {"concepts": [{"id": f"C-{i:04d}", "name": n} for i, n in
                          enumerate(sorted({x for row in index["figures"]
                                            for x in row["concept_names"]}), start=1)]}
    # 故意少给一个名字（最后一个），验证"查不到就不挂"
    dropped = table["concepts"].pop()["name"]
    (tmp_path / "concepts.json").write_text(json.dumps(table, ensure_ascii=False),
                                            encoding="utf-8")
    rehang = subprocess.run([sys.executable, "-m", "blocks.figures.cli", "rehang",
                             "--out", str(tmp_path), "--concepts",
                             str(tmp_path / "concepts.json")],
                            cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert rehang.returncode == 0, rehang.stdout + rehang.stderr
    after = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    ids = {c["name"]: c["id"] for c in table["concepts"]}
    for row in after["figures"]:
        # 顺序按**这张图自己声明的名字顺序**（rehang 就是这么挂的）
        want = [ids[n] for n in row["concept_names"] if n in ids]
        assert row["concepts"] == want, row["name"]
    holder = [row for row in after["figures"] if dropped in row["concept_names"]]
    assert holder and dropped in holder[0]["unresolved"], "对不上的名字要如实记下来"
