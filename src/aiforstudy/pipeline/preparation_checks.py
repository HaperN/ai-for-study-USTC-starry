"""Read existing preparation products and prove their identity before reuse."""
from __future__ import annotations
from pathlib import Path
from ..blocks.concepts.index import build_index
from ..blocks.parse.catalog import build_catalog,build_toc_nodes,build_units,detect_shape,verify_references
from ..blocks.parse.review import apply_review,expected_inputs_digest,load_review,review_needs
from ..contracts.enums import QualityState
from ..contracts.stage import StageResult
from ..core.digest import digest_file,digest_of,digest_text
from ..core.jsonio import read_json,read_json_soft,write_json_atomic
from ..core.source import CanonicalSource, RepairRecord
from ..errors import CapabilityError,ContractError
from ..providers import mineru
from .services import StageContext, reraise_control_error
from .graph import PREPARATION_STAGES as DETERMINISTIC_STAGES
from .snapshot import freeze
from .stage_io import (_store_root,_emit,_file_digest,_blocked_if_missing,_legacy,_gateway_client,
                       KIND_BY_STAGE,KIND_CONTEXT)

# Dependencies belong to the consumer, never to the entire mutable parse tree.
# Include absent optional files so their later appearance invalidates that proof.
CATALOG_INPUTS = ('toc.v2.json', 'body.json', 'chapters.json', 'toc.draft.json',
    'toc.review.json', 'toc.dual-source.json', 'toc.authority.json', 'toc.decisions.json',
    'toc.json', 'toc.pdf.json', 'toc.visual.json', 'toc.comparison.json',
    'toc.arbitration.json', 'toc.alignment.json', 'toc.regions.json', 'pdf.path', 'ingest.json')


def parse_input_files(ctx, *names, unit_sources=False):
    root = Path(ctx.context.parse)
    paths = [str(root / name) for name in names]
    if unit_sources:
        catalog = read_json_soft(root / 'chapters.json') or {}
        paths.extend(str(root / row['original_text']) for row in catalog.get('lesson_units', [])
                     if isinstance(row, dict) and row.get('original_text'))
    return tuple(dict.fromkeys(paths))


def source_material_input_files(ctx):
    """Only original-source and directory inputs affect read-only materials."""
    from ..core.source_materials import INPUTS
    from ..core.course_library import library_input_files
    return (*parse_input_files(ctx, 'source_materials.json', *INPUTS),
            *(str(p) for p in library_input_files(Path(ctx.context.parse) / 'course-library')))


def catalog_input_files(ctx):
    return (*parse_input_files(ctx, *CATALOG_INPUTS), *source_material_input_files(ctx))


def _source_materials_ready(ctx):
    from ..core.source_materials import is_current
    if not is_current(Path(ctx.context.parse)):
        return StageResult.blocked(ctx.node.stage,
            '目录原文资料契约缺失或过期；请从 structure 阶段按完整原文重新准备')
    return None


def _dedupe_needs(rows) -> tuple:
    """待裁决项去重（复核留下的与不依赖复核也能看出的会重叠）。"""
    seen: set[tuple[str, str]] = set()
    out = []
    for row in rows:
        token = (row.kind, row.subject)
        if token in seen:
            continue
        seen.add(token)
        out.append(row)
    return tuple(out)


def _review_findings(applied, needs) -> list[dict]:
    findings: list[dict] = []
    if applied.stale:
        findings.append({"code": "review-stale", "why": applied.reason})
    for row in needs:
        findings.append({"code": "needs-review", "kind": row.kind, "subject": row.subject,
                         "why": row.why, "arbiter": row.arbiter})
    for decision, why in applied.rejected:
        findings.append({"code": "review-rejected", "subject": decision.subject, "why": why})
    return findings


def _declared_source_hash(ctx: StageContext) -> tuple[str, str]:
    """本次认定的源哈希与它的出处。

    可读取的注册 PDF 必须现算字节指纹，并与注册表冻结身份核对；不能由旧声明覆盖
    实际文件。文件不在本地时才保留注册表声明供只读导入检查；两者都没有则返回空串。
    """
    for source in ctx.context.sources:
        if source.kind == "pdf" and Path(source.path).is_file():
            actual = digest_file(source.path)
            if (source.sha256 and str(source.sha256).strip().lower().removeprefix('sha256:')
                    != actual.removeprefix('sha256:')):
                raise CapabilityError('PDF 内容指纹与注册表冻结指纹不一致；不得复用旧 MinerU 产物')
            return actual, "local-file"
    for source in ctx.context.sources:
        if source.kind == "pdf" and source.sha256:
            return str(source.sha256), "registry"
    return "", "absent"


def _verify_local_mineru(parse_root: Path) -> dict:
    """核验"要复用的那份 MinerU 产物"确实还是 `ingest.json` 记的那一份。

    为什么值得单独一步：`decide()` 回答的是"目录里有产物"，不回答"盘上这份还是当初那份"。
    `ingest.json` 里恰好记着 `mineru.layout_sha256`（真实书写着它），拿它跟盘上重算的比一次，
    就把"看起来没问题的复用"变成了"核验过的复用"。对不上就阻断 —— 而不是"凑合用"。
    """
    present, mineru_dir = mineru.local_result_present(parse_root)
    if not present:
        return {"ok": False, "reason": f"没有可复用的 MinerU 产物：{mineru_dir}"}
    ingest = read_json_soft(Path(parse_root) / "ingest.json") or {}
    recorded = ""
    if isinstance(ingest.get("mineru"), dict):
        recorded = str(ingest["mineru"].get("layout_sha256") or "")
    files = []
    layout_sha = ""
    for marker in mineru.LAYOUT_MARKERS:
        path = Path(mineru_dir) / marker
        if not path.is_file():
            continue
        token = digest_file(path)
        files.append({"file": marker, "sha256": token})
        if marker == "layout.json":
            layout_sha = token
    if recorded and layout_sha and layout_sha.removeprefix("sha256:") != recorded:
        return {"ok": False, "files": files, "recorded_layout_sha256": recorded,
                "reason": (f"要复用的 MinerU 产物指纹与 ingest.json 记的不一致："
                           f"{layout_sha.removeprefix('sha256:')} ≠ {recorded}。"
                           f"方案 §11：**文件存在不是缓存有效证据**，所以这里阻断而不是沿用。")}
    return {"ok": True, "files": files, "recorded_layout_sha256": recorded,
            "layout_sha256": layout_sha}


def mineru_stage(ctx: StageContext) -> StageResult:
    """`mineru`：复用已有产物；缺就 blocked。**不上传、不走 API、不回退 OCR。**"""
    ctx.services.cancel.checkpoint()
    parse_root = Path(ctx.context.parse)
    try:
        source_hash, source_from = _declared_source_hash(ctx)
    except CapabilityError as exc:
        return StageResult.blocked(ctx.node.stage, str(exc), outputs=(),
                                   findings=[{"code": "mineru-source-mismatch", "why": str(exc)}])
    # allow_api=False 是这一层的**核心承诺**：零成本运行里没有"顺手用一次云端"这条路。
    decision = mineru.decide(ctx.workspace, parse_root, source_hash=source_hash,
                             allow_api=False)
    if decision.blocked:
        return StageResult.blocked(ctx.node.stage, decision.reason, outputs=(),
                                   findings=[{"code": "mineru-blocked",
                                              "route": decision.route,
                                              "why": decision.reason}])
    verification = _verify_local_mineru(parse_root)
    if not verification["ok"]:
        return StageResult.blocked(ctx.node.stage, verification["reason"], outputs=(),
                                   findings=[{"code": "mineru-unverified",
                                              "why": verification["reason"]}])
    ref = _emit(ctx, name="mineru", kind=KIND_BY_STAGE["mineru"],
                file_type="parse_mineru_reuse",
                payload={"route": decision.route, "reason": decision.reason,
                         "requires_network": bool(decision.requires_network),
                         "requires_api_key": bool(decision.requires_api_key),
                         "source_hash": source_hash, "source_hash_from": source_from,
                         "recorded_source_hash": decision.recorded_source_hash,
                         "evidence": list(decision.evidence),
                         "layout_verification": verification},
                inputs={"book_id": ctx.context.book_id, "revision": ctx.context.revision,
                        "source_hash": source_hash, "source_hash_from": source_from,
                        "recorded_source_hash": decision.recorded_source_hash,
                        "layout_sha256": verification.get("layout_sha256", ""),
                        "recorded_layout_sha256": verification.get("recorded_layout_sha256", "")})
    ctx.log(f"复用已有 MinerU 产物（{decision.route}）：{decision.reason}")
    return StageResult.ok(ctx.node.stage, outputs=(ref,),
                          quality_state=QualityState.ACCEPTED,
                          input_files=parse_input_files(ctx, 'mineru', 'ingest.json'))


def toc_stage(ctx: StageContext) -> StageResult:
    """`toc`：目录持久键 + 复核（**按输入指纹应用，过期就拒绝**）。"""
    ctx.services.cancel.checkpoint()
    parse_root = Path(ctx.context.parse)
    missing = _blocked_if_missing(ctx, parse_root / "toc.v2.json", "目录产物 toc.v2.json")
    if missing is not None:
        return missing
    toc = read_json(parse_root / "toc.v2.json")
    nodes, _ = build_toc_nodes(toc)
    review = load_review(parse_root)
    expected = expected_inputs_digest(parse_root)
    disagreements = list(toc.get("disagreements") or [])
    applied = apply_review(review, expected_inputs_digest=expected,
                           disagreements=disagreements)

    # ── 没有有效复核（缺失或过期）时，**生成一份建议稿**（方案 §11：check 与 repair 分离）
    #
    # ⚠️ 三条边界，每条都有理由：
    # 1. **只在注入了模型传输时才生成**。没注入就保持现状（待裁决项留着、质量态 needs_review），
    #    绝不"顺手连一次真实服务"——那会绕过 5M 闸门。
    # 2. **写进 run 的 work 目录，不写 `data/`**。`toc.review.json` 一旦落进 `data/`，
    #    下一次运行就会把它当成**已有权威复核**应用，而它只是本次生成的建议稿 ——
    #    那等于让一次自动生成悄悄变成长期生效的裁决。硬约束也要求不动旧产物。
    # 3. **不自动应用**。`review_toc` 自己写了"返回**建议稿**"，所以本阶段把它记下来、
    #    给出路径与指纹，由人决定是否提升为 `data/` 下的正式复核。这样"生成了什么"
    #    看得见，而"生效了什么"仍然是一次显式动作。
    generated: dict | None = None
    if (review is None or applied.stale):
        client, transport_source = _gateway_client(ctx, role="toc_review", bucket="prepare")
        if client is not None:
            # 按**包路径**导入：该模块内部用的是相对导入（见 `_legacy` 的说明）
            legacy_review = _legacy("blocks.parse.toc.toc_review")
            try:
                draft = legacy_review.review_toc(toc, parse_dir=parse_root, client=client,
                                                 progress=lambda i, n: ctx.log(
                                                     f"目录复核 {i}/{n}"))
            except Exception as exc:                            # noqa: BLE001
                # ⚠️ 取消／预算停止**不是**"这次复核没生成"：它们要求整个 run 停下，
                #    不能被记成 blocked 之后继续往下跑（§6.1：必须穿透旧宽泛 catch）。
                reraise_control_error(exc)
                # ⚠️ 接的是 `Exception` 而不是 `AifsError`：复核生成失败**不该把整个阶段带崩**。
                #    这个阶段的正事是"把目录持久键产出、并如实记下复核状态"——
                #    它已经成功了。让一次模型侧的失败把产物一起废掉，会让"目录结构没问题"
                #    和"复核没生成"这两件事混成一个失败，排查时看不出是哪一头。
                #    所以这里记 `blocked` 并写明原因，`pending` 照旧留着（质量态 needs_review）。
                draft = None
                generated = {"state": "blocked", "why": f"{type(exc).__name__}: {exc}",
                             "transport": transport_source}
            if draft is not None:
                draft_path = _store_root(ctx) / "toc.review.suggested.json"
                write_json_atomic(draft_path, draft)
                generated = {
                    "state": "suggested", "path": str(draft_path),
                    "sha256": _file_digest(draft_path),
                    "decisions": len(draft.get("decisions") or []),
                    "suspects": len(draft.get("flagged") or []),
                    "model_calls": int(draft.get("model_calls") or 0),
                    "tokens": dict(draft.get("tokens") or {}),
                    "transport": transport_source,
                    "note": ("建议稿**未应用**（§11 check 与 repair 分离）：它写在 run 的 work "
                             "目录里。目录归属仍未确定，当前建议不能作为已核实权威；"
                             "保留未决记录，不生成受影响内容。"),
                }
        else:
            generated = {"state": "no-transport",
                         "why": "toc 复核生成缺少配置的模型传输；请检查 toc_review 角色与真实服务配置。",
                         "transport": transport_source}
    body = read_json_soft(parse_root / "body.json")
    unconfirmed = list((body or {}).get("unconfirmed") or []) if isinstance(body, dict) else []
    # ⚠️ **已被裁决的分歧不许再报一遍**。`review_needs()` 只看原始分歧列表，
    #    不知道哪些已经被 `apply_review` 应用了 —— 于是"复核已经裁决"的条目
    #    仍会以"未裁决"的形状出现在 pending 里。实测：真实书那两条标题分歧
    #    本来就已经被带图的视觉复核裁决并落盘，却**每次运行都重报**，
    #    而且被 `toc` 与 `export` **各报一次**（CH04 那一章 32 条里的大头）。
    #    这条判据的语义是"**还没裁决的**才需要人看"，所以按已应用的 subject 过滤。
    decided = {str(d.subject) for d in applied.applied}
    undecided = [d for d in disagreements if str(d.get("toc_id") or d.get("subject") or "?")
                 not in decided]
    needs = _dedupe_needs(tuple(applied.pending) + review_needs(undecided, unconfirmed))
    findings = _review_findings(applied, needs)
    ref = _emit(ctx, name="toc", kind=KIND_BY_STAGE["toc"], file_type="parse_toc",
                payload={"toc_nodes": [n.as_dict() for n in nodes],
                         "toc_nodes_count": len(nodes),
                         "review": {"expected_inputs_digest": expected,
                                    "artifact_present": review is not None,
                                    "artifact_inputs_digest":
                                        (review.inputs_digest if review else None),
                                    "stale": applied.stale, "reason": applied.reason,
                                    "applied": [d.as_dict() for d in applied.applied],
                                    "rejected": [{"decision": d.as_dict(), "why": why}
                                                 for d, why in applied.rejected]},
                         "review_generated": generated,
                         "pending": [n.as_dict() for n in needs],
                         "disagreements": disagreements,
                         "unresolved": list(toc.get("unresolved") or []),
                         "blocked_for_release": bool(applied.stale or needs)},
                inputs={"book_id": ctx.context.book_id, "revision": ctx.context.revision,
                        "toc_v2_sha256": _file_digest(parse_root / "toc.v2.json"),
                        "toc_draft_sha256": _file_digest(parse_root / "toc.draft.json"),
                        "toc_review_sha256": _file_digest(parse_root / "toc.review.json"),
                        "expected_inputs_digest": expected},
                findings=findings)
    quality = (QualityState.NEEDS_REVIEW if (applied.stale or needs)
               else QualityState.ACCEPTED)
    ctx.log(f"目录 {len(nodes)} 个持久键；复核{'过期' if applied.stale else '有效'}，"
            f"待裁决 {len(needs)} 条"
            + (f"；本次生成建议稿（{(generated or {}).get('decisions', 0)} 条裁决，"
               f"{(generated or {}).get('tokens', {}).get('total', 0)} token，**未应用**）"
               if generated and generated.get("state") == "suggested" else ""))
    return StageResult.ok(ctx.node.stage, outputs=(ref,), quality_state=quality,
                          findings=findings, input_files=parse_input_files(ctx,
                              'toc.v2.json', 'toc.draft.json', 'toc.review.json', 'body.json'))


def structure_stage(ctx: StageContext) -> StageResult:
    """`structure`：正文归属（角色 / 容器 / 空缺 / 孤儿 / 歧义）。

    有未裁决歧义时**写产物但报 blocked**：方案 §7 要的正是"不能唯一匹配时先阻断受影响迁移
    并**提供审阅表**"—— 审阅表就是这份产物里的 `ambiguities`。
    """
    ctx.services.cancel.checkpoint()
    parse_root = Path(ctx.context.parse)
    missing = _blocked_if_missing(ctx, parse_root / "body.json", "正文归属产物 body.json")
    if missing is not None:
        return missing
    body = read_json(parse_root / "body.json")
    units, exclusions, ambiguities = build_units(body)
    role_counts: dict[str, int] = {}
    for unit in units:
        role_counts[unit.teaching_role] = role_counts.get(unit.teaching_role, 0) + 1
    findings = [{"code": "ambiguity", "kind": a.kind, "subject": a.subject, "why": a.why,
                 "needs": a.needs, "candidates": list(a.candidates)} for a in ambiguities]
    # 用户 2026-10-20 定：原文归属有歧义时只拦受影响的单元（歧义里点名的候选单元），其他单元照常生成。
    from ..core.identity import KEY_RE
    blocked_units = sorted({key for a in ambiguities for key in (*a.candidates, a.subject)
                            if isinstance(key, str) and KEY_RE.match(key)})
    ref = _emit(ctx, name="structure", kind=KIND_BY_STAGE["structure"],
                file_type="parse_structure",
                payload={"blocked_units": blocked_units,
                         "shape": detect_shape(body),
                         "units": [u.as_dict() for u in units],
                         "unit_count": len(units),
                         "teaching": sum(1 for u in units if u.is_teaching),
                         "role_counts": dict(sorted(role_counts.items())),
                         "exclusions": dict(sorted(exclusions.items())),
                         "ambiguities": [a.as_dict() for a in ambiguities]},
                inputs={"book_id": ctx.context.book_id, "revision": ctx.context.revision,
                        "body_sha256": _file_digest(parse_root / "body.json"),
                        "toc_v2_sha256": _file_digest(parse_root / "toc.v2.json")},
                findings=findings)
    if ambiguities and not _STRUCTURE_SCOPED_BLOCKING:
        detail = "；".join(f"{a.kind}:{a.subject}" for a in ambiguities[:5])
        return StageResult.blocked(
            ctx.node.stage,
            f"正文归属有 {len(ambiguities)} 处**未裁决歧义**（{detail}），受影响范围不能当成"
            f"迁移完成。方案 §7：同名/重复正文/多处匹配不能只按标题自动合并，"
            f"源归属未确定，不能生成受影响内容；待解决记录见本次产物。",
            outputs=(ref,), quality_state=QualityState.REJECTED, findings=findings)
    if ambiguities:
        ctx.log(f"正文归属有 {len(ambiguities)} 处未裁决歧义：只拦受影响单元 {blocked_units[:8]}，其余照常")
    ctx.log(f"正文归属 {len(units)} 个单元（教学 {sum(1 for u in units if u.is_teaching)}），"
            f"排除项 {len(exclusions)} 条")
    return StageResult.ok(ctx.node.stage, outputs=(ref,),
                          quality_state=QualityState.ACCEPTED, findings=findings,
                          input_files=parse_input_files(ctx, 'body.json', 'toc.v2.json'))


def export_stage(ctx: StageContext) -> StageResult:
    """`export`：统一 catalog（持久逻辑键 + 内容实例 + 显式排除理由）。

    有**未裁决歧义**（catalog 自检不通过）时 `failed`：那说明身份表本身不一致，
    下游建立在它上面只会把错误摊大。有**待裁决项**时 `succeeded + needs_review`：
    身份是清楚的，只是不能发布（§9 的两个轴正是为这种情形准备的）。
    """
    ctx.services.cancel.checkpoint()
    parse_root = Path(ctx.context.parse)
    material_problem = _source_materials_ready(ctx)
    if material_problem is not None:
        return material_problem
    for name, what in (("toc.v2.json", "目录产物"), ("body.json", "正文归属产物"),
                       ("chapters.json", "导出产物")):
        missing = _blocked_if_missing(ctx, parse_root / name, f"{what} {name}")
        if missing is not None:
            return missing
    catalog, details = build_catalog(parse_root, book_id=ctx.context.book_id,
                                     revision=ctx.context.revision)
    verify = catalog.verify()
    pending = [n for n in catalog.needs_review if not n.resolved]
    findings = [{"code": "catalog-problem", "why": why} for why in verify["problems"]]
    findings += [{"code": "needs-review", "kind": n.kind, "subject": n.subject, "why": n.why}
                 for n in pending]
    ref = _emit(ctx, name="export", kind=KIND_BY_STAGE["export"], file_type="parse_catalog",
                payload=catalog.as_dict(),
                inputs={"book_id": ctx.context.book_id, "revision": ctx.context.revision,
                        "toc_v2_sha256": _file_digest(parse_root / "toc.v2.json"),
                        "body_sha256": _file_digest(parse_root / "body.json"),
                        "chapters_sha256": _file_digest(parse_root / "chapters.json"),
                        "shape": details["shape"]},
                findings=findings)
    if verify["problems"]:
        return StageResult.failed(
            ctx.node.stage,
            f"catalog 自检不通过（{len(verify['problems'])} 处）：{verify['problems'][:3]}。"
            f"身份表不一致时下游只会把错误摊大，所以这里失败而不是继续。",
            outputs=(ref,), quality_state=QualityState.REJECTED, findings=findings)
    quality = (QualityState.NEEDS_REVIEW if (pending or catalog.ambiguities)
               else QualityState.ACCEPTED)
    ctx.log(f"catalog：单元 {verify['counts']['units']}（教学 {verify['counts']['teaching']}）"
            f"｜待裁决 {len(pending)}")
    return StageResult.ok(ctx.node.stage, outputs=(ref,), quality_state=quality,
                          findings=findings, input_files=catalog_input_files(ctx))


def body_stage(ctx: StageContext) -> StageResult:
    """`body`：教学单元与交叉引用对账，附上只读原文资料来源信息。"""
    ctx.services.cancel.checkpoint()
    parse_root = Path(ctx.context.parse)
    material_problem = _source_materials_ready(ctx)
    if material_problem is not None:
        return material_problem
    for name, what in (("toc.v2.json", "目录产物"), ("body.json", "正文归属产物"),
                       ("chapters.json", "导出产物")):
        missing = _blocked_if_missing(ctx, parse_root / name, f"{what} {name}")
        if missing is not None:
            return missing
    catalog, details = build_catalog(parse_root, book_id=ctx.context.book_id,
                                     revision=ctx.context.revision)
    recon = details["reconciliation"]
    materials = read_json_soft(parse_root / "source_materials.json") or {}
    # Concept files are produced by the next stage. Check their references there,
    # after generation, so body never freezes a pre-generation concept directory.
    references = verify_references(parse_root, ctx.context.lessons, catalog, include_concepts=False)
    findings: list[dict] = []
    if not recon["consistent"]:
        findings += [{"code": "lesson-unit-unmatched", "why": row.get("why"),
                      "lesson_unit": row.get("lesson_unit")} for row in recon["unmatched"]]
    findings += [{"code": "reference-missing", **row} for row in references["missing"]]
    summary = {
        "reconciliation": {"lesson_units": recon["lesson_units"],
                           "matched": len(recon["matched"]),
                           "unmatched": recon["unmatched"],
                           "catalog_only": recon["catalog_only"],
                           "consistent": recon["consistent"]},
        "source_materials": {
            "path": "source_materials.json",
            "source_dir": str(parse_root.resolve()),
            "count": len(materials.get("materials") or []),
            "unlocated": [row.get("id") for row in materials.get("materials", [])
                          if row.get("state") != "recorded"],
            "schema_version": materials.get("schema_version"),
            "input_hashes": materials.get("input_hashes") or {},
        },
        "references": references,
    }
    ref = _emit(ctx, name="body", kind=KIND_BY_STAGE["body"], file_type="parse_body",
                payload=summary,
                inputs={"book_id": ctx.context.book_id, "revision": ctx.context.revision,
                        "body_sha256": _file_digest(parse_root / "body.json"),
                        "chapters_sha256": _file_digest(parse_root / "chapters.json"),
                        "source_materials_sha256": _file_digest(parse_root / "source_materials.json")},
                findings=findings)
    # 用户 2026-10-20 定：run 外旧教案/旧审核文件的引用对不上只记警告（findings 里照列）。
    if not recon["consistent"]:
        return StageResult.failed(
            ctx.node.stage,
            f"正文对账没通过：引用解析不出来 {len(references['missing'])} 处、"
            f"lesson_units 未命中 {len(recon['unmatched'])} 条。方案 §7：CH/U 谱系与原文坐标"
            f"必须可对账，对不上就是谱系缺环。",
            outputs=(ref,), quality_state=QualityState.REJECTED, findings=findings)
    ctx.log(f"对账 {recon['lesson_units']} 条 lesson_units；引用 "
            f"{references['concept_occurrences'] + references['concepts_parts'] + references['lesson_files'] + references['audit_files']} "
            f"处全部解析出来；保留原文资料 {len(materials.get('materials') or [])} 处")
    return StageResult.ok(ctx.node.stage, outputs=(ref,), quality_state=QualityState.ACCEPTED,
                          findings=findings, input_files=catalog_input_files(ctx))


def _available_figures(parse_root: Path) -> tuple[tuple[str, ...], str]:
    """快照的"可用图"。**形状不认就留空并说明**，不去猜字段名。"""
    payload = read_json_soft(Path(parse_root) / "figures.json")
    if not isinstance(payload, dict):
        return (), "figures.json 不在位或不是对象"
    rows = payload.get("images")
    if not isinstance(rows, list):
        return (), "figures.json 里没有 images 数组（形状不认，不猜）"
    ids: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        token = row.get("figure_id") or row.get("id")
        if token:
            ids.append(str(token))
    return tuple(ids), ""


def _prior_approved(lessons_root: Path) -> dict[str, str]:
    """起跑前就"已通过"的前序信息（§7 要求快照冻结它，而不是读本趟实时结果）。

    来源只能是**本次运行开始之前**就存在的教案索引；只取 `status == "done"` 的行，
    并把每行压成一行摘要。读不到就空 —— 不拿"模型可能记得"兜底。
    """
    payload = read_json_soft(Path(lessons_root) / "lessons.index.json")
    if not isinstance(payload, dict):
        return {}
    out: dict[str, str] = {}
    for row in payload.get("units") or []:
        if not isinstance(row, dict) or str(row.get("status") or "") != "done":
            continue
        token = str(row.get("unit_id") or "")
        if token:
            out[token] = str(row.get("title") or "")[:120]
    return out


def _canonical_source(ctx: StageContext) -> CanonicalSource | None:
    """Freeze the immutable source and the single source-anchored teaching view."""
    raw_path = Path(ctx.context.parse) / "document.md"
    if not raw_path.is_file():
        return None
    projection = read_json_soft(raw_path.parent / 'source.projection.json')
    if projection:
        selected_path = raw_path.parent / 'document.selected.md'
        for path, key in ((raw_path, 'raw_sha256'), (selected_path, 'selected_sha256')):
            if not path.is_file() or digest_file(path).removeprefix('sha256:') != projection[key]:
                raise ContractError('正文源视图与来源记录不一致：' + str(path))
        revision = 'parse-projection-' + projection['selected_sha256'][:20]
        repair = RepairRecord(version=revision, scope='spans',
            basis='source-reference roles and independently verified page-image corrections',
            diff_digest=digest_of(projection), enabled_by='automatic-source-policy-v3',
            checked={'raw_identity': True, 'selected_identity': True}, formula_only=False)
        return CanonicalSource.build(raw_path.read_bytes(), selected_path.read_bytes(),
            revision=revision, repairs=(repair,), spans_changed=True,
            raw_path=str(raw_path), selected_path=str(selected_path))
    return CanonicalSource.build(raw_path.read_bytes(), revision=ctx.context.revision,
                                 raw_path=str(raw_path), selected_path=str(raw_path))


_STRUCTURE_SCOPED_BLOCKING = True


def concepts_stage(ctx: StageContext) -> StageResult:
    """`concepts`：全书概念索引（单写方合并）+ 冻结上下文快照。

    索引没冻结时：**照写产物、照冻快照，但本阶段 `blocked`**（理由见模块 docstring）。
    """
    ctx.services.cancel.checkpoint()
    parse_root = Path(ctx.context.parse)
    for name, what in (("toc.v2.json", "目录产物"), ("body.json", "正文归属产物"),
                       ("chapters.json", "导出产物")):
        missing = _blocked_if_missing(ctx, parse_root / name, f"{what} {name}")
        if missing is not None:
            return missing
    catalog, _details = build_catalog(parse_root, book_id=ctx.context.book_id,
                                      revision=ctx.context.revision)
    references = verify_references(parse_root, ctx.context.lessons, catalog)
    # 用户 2026-10-20 定：引用对不上只记警告——指向不存在单元的引用本来就不会生成任何内容。
    reference_findings = [{'code':'reference-missing', **row} for row in references['missing']]
    index = build_index(parse_root, catalog, book_id=ctx.context.book_id)
    # 用户 2026-10-20 定：概念 id 重复时先审核再决定合并或分开，不让整个概念阶段失败。
    from ..blocks.concepts.duplicates import resolve_duplicate_ids
    ids = [c.concept_id for c in index.concepts]
    judge = _gateway_client(ctx, role='concepts', bucket='prepare')[0] if len(set(ids)) != len(ids) else None
    duplicate_decisions = resolve_duplicate_ids(index, judge)
    if duplicate_decisions:
        from ..core.jsonio import write_json_atomic as _write
        _write(_store_root(ctx)/'products'/'concepts'/'duplicate-review.json',
               {'schema_version':'ConceptDuplicateReviewV1','decisions':duplicate_decisions})
    verify = index.verify()
    blocking = index.blocking_parts()
    findings = [{"code": "concept-part-blocking", "unit_key": p.unit_key, "state": p.state,
                 "why": p.why} for p in blocking]
    findings += reference_findings
    findings += [{"code": "concept-duplicate-" + d['decision'], "concept_id": d['concept_id'], "why": d['reason']}
                 for d in duplicate_decisions]
    if not verify["ok"]:
        findings += [{"code": "concept-index-problem", "why": why}
                     for why in verify["problems"]]
    index_ref = _emit(ctx, name="concepts", kind=KIND_BY_STAGE["concepts"],
                      file_type="concept_index", payload=index.as_dict(),
                      inputs={"book_id": ctx.context.book_id, "revision": ctx.context.revision,
                              "source_digest": index.source_digest,
                              "parts": {p.unit_key: p.text_sha256 for p in index.parts}},
                      findings=findings)
    if not verify["ok"]:
        return StageResult.failed(
            ctx.node.stage,
            f"概念索引自检不通过（{len(verify['problems'])} 处）：{verify['problems'][:3]}",
            outputs=(index_ref,), quality_state=QualityState.REJECTED, findings=findings)

    source = _canonical_source(ctx)
    if source is None:
        return StageResult.blocked(
            ctx.node.stage,
            f"缺原文 {parse_root / 'document.md'} —— 快照必须钉住 selected_text 指纹，"
            f"缺原文不能用'模型可能记得'兜底（方案 §7）。",
            outputs=(index_ref,), quality_state=QualityState.NEEDS_REVIEW, findings=findings)

    figures, figures_note = _available_figures(parse_root)
    notes: list[str] = []
    if figures_note:
        notes.append(f"可用图未识别：{figures_note}")
    if blocking:
        # 用户 2026-10-20 定：重新抽取 2 轮后仍失败的单元，只把它的概念标成缺失，其余照常继续。
        notes.append(
            f"概念索引有缺口：{len(blocking)} 个单元的分片重新抽取后仍未完成（例如 "
            f"{blocking[0].unit_key}={blocking[0].state}），这些单元的概念标为缺失；其余单元照常生成。")
    snapshot = freeze(ctx.context.book_id, catalog, index, source,
                      prior_approved=_prior_approved(ctx.context.lessons),
                      figures=figures, notes=tuple(notes))
    context_ref = _emit(ctx, name="context", kind=KIND_CONTEXT,
                        file_type="run_snapshot", payload=snapshot.as_dict(),
                        inputs={"book_id": ctx.context.book_id,
                                "snapshot_digest": snapshot.digest(),
                                "selected_text_sha256": source.selected_text_sha256,
                                "figures": list(figures)},
                        findings=findings)
    if blocking:
        missing_units = sorted(p.unit_key for p in blocking)
        ctx.log(f"概念缺失的单元（重新抽取后仍失败）：{missing_units[:8]}；其余单元照常继续。"
                f"之后可用 --prepare refresh 补抽。")
        return StageResult.ok(ctx.node.stage, outputs=(index_ref, context_ref),
                              quality_state=QualityState.NEEDS_REVIEW,
                              findings=[*findings, {"code": "concept-units-missing", "units": missing_units,
                                                    "why": "这些单元的概念分片重新抽取后仍未完成，概念标为缺失"}],
                              input_files=(*catalog_input_files(ctx), *parse_input_files(ctx,
                                  'concepts.json', 'concepts.ids.json', 'concepts.parts', 'figures.json',
                                  'document.md', 'document.selected.md', 'source.projection.json',
                                  unit_sources=True)))
    ctx.log(f"概念索引 {verify['counts']['concepts']} 条、快照 {snapshot.digest()[:20]}…")
    return StageResult.ok(ctx.node.stage, outputs=(index_ref, context_ref),
                          quality_state=QualityState.ACCEPTED, findings=findings,
                          input_files=(*catalog_input_files(ctx), *parse_input_files(ctx,
                              'concepts.json', 'concepts.ids.json', 'concepts.parts', 'figures.json',
                              'document.md', 'document.selected.md', 'source.projection.json',
                              unit_sources=True)))
