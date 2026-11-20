"""目录布局的不变量（拦住一类很容易犯、又很难查的错）。

各块都是"把自己的目录塞进 sys.path，再按裸名 import"（`import structure`、
`import prompt`）。这套写法有个致命副作用：**两个块里同名模块会互相覆盖** ——
谁先被导入谁生效，另一个块拿到的是别人的模块。

实测踩过：清洗块最初叫 `prompt.py`，跟教案块的 `prompt.py` 撞名，
结果教案块的提示词函数被换成了清洗块的（`TypeError: system_prompt() takes 0
positional arguments but 3 were given`），而且只在整套测试一起跑时才复现。

所以：**跨块不许有同名模块**（`__init__.py` 与 `cli.py` 例外，它们只通过
`python -m` 以全名加载，不会裸名导入）。
"""
import collections
from pathlib import Path

BLOCKS = Path(__file__).resolve().parents[1] / "blocks"
EXEMPT = {"__init__.py", "cli.py"}
# 有例外就写明白，别用"跳过"糊过去：
#   parse/            只是容器，真正的块在它下面（structure / images / concepts / export）
#   parse/extract/    是 structure 块的一个子步骤，由 `structure.cli extract` 驱动
NO_CLI_REQUIRED = {
    "blocks/lesson",           # 历史 snapshot/概念登记只读支持，不恢复已退休作者 CLI
    "blocks/render",           # 当前渲染由 graph 驱动；这里只留共享页面壳，旧裸教案 CLI 已退休
    "blocks/parse",            # 只是容器，真正的块在它下面
    "blocks/parse/extract",    # structure 块的子步骤，由 structure.cli extract 驱动
    # 第三方包（P-007 从 CourseForgeV2 迁进来的审核器）不是我们的块，不要求 cli.py
    "blocks/audit/vendor/hbep_controller_v2",
    # 图的**渲染子包**（backend：vector/schemdraw/circuitikz…）—— 它是 blocks/figures
    # 内部的一层，不是独立块，所以不需要 cli.py（块自己的 cli 在 blocks/figures/cli.py）
    "blocks/figures/backends",
}


def _modules_by_name() -> dict[str, list[str]]:
    found: dict[str, list[str]] = collections.defaultdict(list)
    for path in sorted(BLOCKS.rglob("*.py")):
        if path.name in EXEMPT or "__pycache__" in path.parts:
            continue
        found[path.name].append(str(path.relative_to(BLOCKS.parent)))
    return found


def test_module_names_are_unique_across_blocks():
    duplicates = {name: paths for name, paths in _modules_by_name().items() if len(paths) > 1}
    assert not duplicates, (
        "这些模块名在多个块里重复了，裸名导入会互相覆盖，请加块名前缀：\n"
        + "\n".join(f"  {name}: {paths}" for name, paths in duplicates.items()))


def test_every_implemented_block_has_a_cli():
    """写了代码的块都得有 cli.py（约定：块都能 `python -m blocks.<块>.cli` 单独跑）。

    只供当前 graph 调用的共享适配器见 NO_CLI_REQUIRED；空目录占位块也不算。
    """
    missing = []
    for path in sorted(BLOCKS.rglob("*/")):
        if "__pycache__" in path.parts:
            continue
        relative = str(path.relative_to(BLOCKS.parent))
        if relative in NO_CLI_REQUIRED:
            continue
        if not any(p.name != "__init__.py" for p in path.glob("*.py")):
            continue
        if not (path / "cli.py").is_file():
            missing.append(relative)
    assert not missing, f"这些块写了代码但没有 cli.py：{missing}"


def test_shared_helpers_live_in_lib():
    """跨块共用的小工具应该只有一份，放在 lib/ 里。"""
    assert (BLOCKS.parent / "lib" / "files.py").is_file()
    # 各块不许自己再实现一份 sha256 文件摘要
    offenders = []
    for path in sorted(BLOCKS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "def sha256_file" in text or "def sha256_bytes" in text:
            offenders.append(str(path.relative_to(BLOCKS.parent)))
    assert not offenders, f"这些文件自己实现了一份文件摘要，应该用 lib/files.py：{offenders}"


# ── P-016：模型不许借"还没讲的东西"（前置门控）──────────────────────────
def _spec():
    return {"id": "M-01", "template": "row-operation", "title": "整行一起动",
            "anchor_hint": "x", "binds_to": "C-0004",
            "params": {"matrix": [[1, 2], [3, 4]], "op": "scale", "lambda": 2, "rows": [1, 2]},
            "readouts": [{"name": "det", "expr": "det(A)"}],
            "steps": [{"label": "a", "state": {"lambda": 1}},
                      {"label": "b", "state": {"lambda": 2}}]}


def _normalize(specs, **kw):
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "blocks" / "model"))
    from model_validate import normalize_models
    return normalize_models(specs, body="x", available_ids={"C-0004"}, **kw)


def test_model_needing_a_concept_from_a_later_section_is_dropped():
    """§2.1 还没讲矩阵 —— 行变换模型（要矩阵）必须被丢掉，而不是硬塞给初学者。"""
    out, notes, dropped = _normalize([_spec()], introduced_ids=["C-0001"],
                                     introduced_names=["线性方程组"],
                                     all_concept_names=["矩阵", "行列式"])
    assert out == [] and "前置不合格" in dropped[0], dropped


def test_det_readout_is_stripped_before_determinants_are_taught():
    """矩阵讲了、行列式还没讲 → 模型留着，但 det 读数要摘掉（不是丢掉模型）。"""
    out, notes, dropped = _normalize([_spec()], introduced_ids=["C-0004"],
                                     introduced_names=["矩阵"],
                                     all_concept_names=["矩阵", "行列式"])
    assert len(out) == 1 and out[0]["readouts"] == []
    assert any("摘掉了读数" in n for n in notes), notes


def test_canvas_model_without_a_draggable_quantity_is_dropped():
    """静态矩阵图（没有任何可拖的连续量）不该占一个"可动手"的位置。"""
    spec = {"id": "M-01", "template": "row-operation", "title": "一张矩阵",
            "anchor_hint": "x", "binds_to": "C-0004",
            "params": {"matrix": [[1, 2], [3, 4]]},
            "steps": [{"label": "a", "state": {}}, {"label": "b", "state": {}}]}
    out, notes, dropped = _normalize([spec], introduced_ids=["C-0004"],
                                     introduced_names=["矩阵"], all_concept_names=["矩阵"])
    assert out == [] and "连续量" in dropped[0], dropped


def test_usable_templates_shrink_before_the_prerequisites_are_taught():
    """提示词只给"现在能用"的模板：§2.1 连 row-operation 都不该出现。"""
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "blocks" / "model"))
    from model_prompt import usable_templates
    early = usable_templates({"introduced_ids": ["C-0001"],
                              "introduced_names": ["线性方程组"],
                              "all_names": ["线性方程组", "矩阵", "行列式", "向量"]})
    assert "row-operation" not in early and "elimination-steps" in early, early
    later = usable_templates({"introduced_ids": ["C-0004"],
                              "introduced_names": ["矩阵", "向量"],
                              "all_names": ["矩阵", "行列式", "向量"]})
    assert "row-operation" in later, later


def test_historical_lesson_support_has_no_author_cli():
    assert not (BLOCKS/'lesson/cli.py').exists()
    assert not (BLOCKS/'lesson/generate.py').exists()
    assert {p.name for p in (BLOCKS/'lesson').glob('*.py')} == {'__init__.py','snapshot.py','concept_occurrences.py'}
