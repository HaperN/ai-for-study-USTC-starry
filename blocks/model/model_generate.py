"""模型这一趟：读一节正文 → 要一份 `models[]` 规格 → 校验 → 写成 sidecar。

**为什么单独一趟，而不是塞进教案生成**：
* 便宜：只给正文 + 过程图清单，2~5k token；跟着教案重跑一次要 46k+；
* 可补跑：已有的教案（第 1 章那批、试点两节）都能补上模型，不用重新生成正文；
* 契约不变：产物就是渲染层已经在读的那份 `models/<unit>.json`。

**失败怎么办**：这一趟失败（调用错、模型不听话）**不影响教案** —— 页面照常出，只是没有模型。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "lib"))

from curriculum import concept_names, introduced_by, later_than   # noqa: E402
from files import load_json, write_json       # noqa: E402
from model_prompt import SYSTEM, user_prompt        # noqa: E402
from model_validate import normalize_models         # noqa: E402

GENERATOR = "aiforstudy.model"
SCHEMA_VERSION = "1.0"


def available_ids(lesson: dict) -> set[str]:
    """这一节**真实存在**的 id 集合（概念 / 公式定理详解 / 过程图 / 插图）。

    P-003 的相关性判据拿它当关卡：模型说"我绑定 C-0004"，可本节根本没有 C-0004 ——
    那它就是通用演示，回去重写。`binds_to` 的合法性只认这一份名单。
    """
    ids: set[str] = set()
    for key in ("concepts", "figures", "diagrams", "models", "explainables"):
        for ref in lesson.get(key) or []:
            if isinstance(ref, dict) and ref.get("id"):
                ids.add(str(ref["id"]))
    return ids


def curriculum_context(parse_dir: Path, unit_id: str) -> dict:
    """P-016：这一节**已经讲过**什么、**以后才讲**什么（给提示词，也给校验器）。

    ⚠️ **三个列表都必须排序**（2026-09-26 生产版 A6 实测抓出，已实证）：

    `got` / `later` 是 **set**，而 set 的迭代顺序受**字符串 hash 随机化**
    （`PYTHONHASHSEED`）影响 —— **每个进程都可能不同**。这些列表会进模型步骤的
    prompt，于是"同一节、同样的输入"在两个进程里算出的 prompt **不一样**。

    实测证据（同一份 `concepts.json`，只改 hash 种子）：
        PYTHONHASHSEED=1 → deadf6ac…   =2 → 3eadc5fb…   =3 → e0592e0d…
        差异就在 `introduced_names` 的**顺序**上。

    后果有两层：
      ① **模型这一步永远命不中缓存** —— 每次重跑都白花钱
         （并行趟实测：prompt 含 context 的 `model:` 调用 4 次**全部未命中**，
          而不含 context 的 `lesson:` 调用全部命中）
      ② **产出不可复现**：同一件活每次结果不同

    这违反项目自己已经写下的规矩（见 `blocks/lesson/generate.py:200`）：
        **一份派生视图的内容，不该取决于你怎么调用它。**
    同一个病，上一次出现在"教案索引"，这一次出现在"模型 prompt"里。
    """
    names = concept_names(parse_dir)
    got = introduced_by(parse_dir, unit_id)
    later = later_than(parse_dir, unit_id)
    got_ids = sorted(got)          # ← 排序不是为了好看，是为了**可复现**
    later_ids = sorted(later)
    return {"introduced_ids": got_ids,
            "introduced_names": [names.get(c, "") for c in got_ids if names.get(c)],
            "later_names": [names.get(c, "") for c in later_ids if names.get(c)],
            # 原来写 `list(names.values())`：顺序 = `concepts.json` 的数组顺序，
            # 而那个顺序在并行下取决于两个 worker 的合并先后。按 id 排掉这个自由度。
            "all_names": [names[c] for c in sorted(names)],
            "concept_names_by_id": {c: names[c] for c in sorted(names)}}


def _body(lesson: dict) -> str:
    return str(lesson.get("body_markdown") or lesson.get("explanation_markdown") or "")


def _references(lesson: dict, context: dict) -> tuple[list[dict], list[dict]]:
    names = context.get("concept_names_by_id") or {}
    concepts = [{"id": ref["id"], "name": ref.get("name") or names.get(str(ref["id"]), "")}
                for ref in lesson.get("concepts") or [] if isinstance(ref, dict) and ref.get("id")]
    explainables = [{"id": item["id"], "name": item.get("name") or item.get("title")
                    or names.get(str(item.get("concept_id")), ""),
                    "summary": " ".join(str(item.get(key) or "") for key in ("what", "markdown", "warmup"))}
                   for item in lesson.get("explainables") or [] if isinstance(item, dict) and item.get("id")]
    return concepts, explainables


def _square_example(text: str) -> bool:
    compact = re.sub(r"[\s{}]", "", text).replace("²", "^2")
    # A prefix such as z^20 or z^2+z must never select the fixed z² renderer.
    end = r"(?=$|[\$，,。；;]|\\[)\]])"
    exact_function = bool(re.search(r"f\(z\)=z\^2" + end, compact))
    if "f(z)=z" in compact:
        return exact_function
    return bool(re.search(r"u(?:\(x,y\))?=x\^2-y\^2" + end, compact)
                and re.search(r"v(?:\(x,y\))?=2xy" + end, compact))


def plan_models(lesson: dict, *, diagrams: list[dict], context: dict | None = None,
                parse_dir: Path | None = None, unit_id: str = "") -> dict:
    """Free, conservative capability selection from this lesson, never catalog ids alone.

    An unsupported teaching object is not a declaration that no interaction is needed.
    The evidence is saved even when this preflight eliminates every paid call.
    """
    context = context if context is not None else curriculum_context(parse_dir or Path("."), unit_id)
    body = _body(lesson)
    concepts, explanations = _references(lesson, context)
    local_names = "、".join(c["name"] for c in concepts)
    current = body + "\n" + local_names
    templates, bindings = [], {}
    prerequisites = "、".join(context.get("introduced_names") or []) + "\n" + current
    if "矩阵" in prerequisites and any(word in current for word in ("行变换", "行操作", "交换两行", "倍加")):
        templates.append("row-operation")
    if "向量" in prerequisites and any(word in current for word in ("线性组合", "向量组合", "平行四边形")):
        templates.append("vector-combination")
    if any(word in current for word in ("消元", "方程组")):
        templates.append("elimination-steps")
    if any(word in current for word in ("向量积", "叉积", "叉乘")):
        templates.append("cross-product-3d")
    square = _square_example(body + "\n" + "\n".join(x["summary"] for x in explanations))
    harmonic = "共轭调和" in current and any(word in body for word in ("等值线", "等值曲线")) and square
    harmonic_bindings = [x["id"] for x in explanations
                         if any(word in x["name"] + x["summary"] for word in ("等值线", "等值曲线"))
                         and f"EXPLAIN:{x['id']}" in body]
    if harmonic and harmonic_bindings:
        templates.append("harmonic-contours")
        bindings["harmonic-contours"] = harmonic_bindings
    has_object = bool(explanations or diagrams or concepts or "$" in body or "=" in body
                      or any(word in body for word in ("函数", "方程", "曲线", "电流", "电阻", "速度", "向量", "矩阵")))
    status = "eligible" if templates else ("unsupported" if has_object else "not_applicable")
    return {"status": status, "templates": templates, "bindings": bindings,
            "policy": "interaction-current-lesson-capabilities-v2",
            "evidence": {"body_excerpt": body[:160], "concepts": concepts,
                         "explanations": [{"id": x["id"], "name": x["name"]} for x in explanations],
                         "diagram_ids": [d.get("id") for d in diagrams if isinstance(d, dict)],
                         "has_teaching_object": has_object, "has_square_example": square,
                         "available_templates": templates,
                         "decision": ("当前正文和解释落点匹配内置模板" if templates else
                                      "当前有教学对象，但现有模板不支持或缺少解释落点" if has_object else
                                      "当前正文没有具体数学或物理对象、公式、过程图或解释落点")}}


def build_user_prompt(lesson: dict, diagrams: list[dict], *, context: dict | None = None) -> str:
    context = context or {}
    concepts, explainables = _references(lesson, context)
    plan = plan_models(lesson, diagrams=diagrams, context=context)
    return user_prompt(title=lesson.get("title") or "", body=_body(lesson),
                       diagrams=diagrams, concepts=concepts, explainables=explainables,
                       context={**context, "templates": plan["templates"], "bindings": plan["bindings"]})


def _empty_decision(data: dict, body: str) -> tuple[dict, list[str]]:
    decision = data.get("decision")
    if not isinstance(decision, dict):
        return {}, ["有可用模板但模型返回空数组，未提供 not_applicable 理由及正文证据"]
    reason = decision.get("reason")
    evidence = decision.get("evidence")
    if (decision.get("status") != "not_applicable" or not isinstance(reason, str) or len(reason.strip()) < 8
            or not isinstance(evidence, list) or not evidence
            or any(not isinstance(row, dict) or not isinstance(row.get("quote"), str)
                   or len(row["quote"].strip()) < 6 or row["quote"] not in body
                   or not isinstance(row.get("explanation"), str) or len(row["explanation"].strip()) < 8
                   for row in evidence)):
        return {}, ["空交互的 not_applicable 声明缺具体理由或可逐字核对的正文证据"]
    return {"status": "not_applicable", "reason": reason.strip(), "evidence": evidence}, []


def generate_models(lesson: dict, *, diagrams: list[dict], client, unit_id: str,
                    parse_dir: Path | None = None, retries: int = 1, log=print, repair_context=None) -> dict:
    """Generate only applicable bounded template data, with an explicit empty-result policy."""
    context = curriculum_context(Path(parse_dir) if parse_dir else Path("."), unit_id)
    plan = plan_models(lesson, diagrams=diagrams, context=context)
    base_checks = {"policy": plan["policy"], "evidence": plan["evidence"],
                   "templates": plan["templates"], "bindings": plan["bindings"]}
    if not plan["templates"]:
        problems = [plan["evidence"]["decision"]] if plan["status"] == "unsupported" else []
        return {"models": [], "checks": {**base_checks, "status": plan["status"],
                "raw_count": 0, "kept": 0, "attempts": 0, "skipped_before_call": True,
                "tolerance_notes": [], "dropped": [], "problems": problems}, "usage": {}}
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": build_user_prompt(lesson, diagrams, context=context)}]
    if repair_context:
        messages.append({"role":"user", "content":"上次校验失败，按以下工单修复并重新提供受限模板数据：" + json.dumps(repair_context, ensure_ascii=False)})
    last_error = ""
    for attempt in range(retries + 1):
        try:
            result = client.chat_json(messages, tag=f"model:{unit_id}:a{attempt}")
        except Exception as error:
            # Budget/cancellation is owned by the pipeline, never consume it as a format retry.
            try:
                from aiforstudy.errors import AifsError
            except ImportError:
                AifsError = ()
            if isinstance(error, AifsError):
                raise
            last_error = f"{type(error).__name__}: {error}"
            log(f"  {unit_id}: 第 {attempt + 1} 次调用失败（{last_error[:80]}）")
            continue
        data = result.get("data") if isinstance(result, dict) else None
        specs = data.get("models") if isinstance(data, dict) else None
        if not isinstance(specs, list):
            last_error = "模型没返回 models 数组"
            continue
        models, notes, dropped = normalize_models(
            specs, body=_body(lesson), available_ids=available_ids(lesson),
            introduced_ids=context.get("introduced_ids") if parse_dir else None,
            introduced_names=context.get("introduced_names") if parse_dir else None,
            all_concept_names=context.get("all_names"),
            allowed_templates=plan["templates"], template_bindings=plan["bindings"])
        problems, decision = [], {}
        if not specs:
            decision, problems = _empty_decision(data, _body(lesson))
        for model in models:
            if not model.get("anchor_hint_ok") or not model.get("binds_to_ok") or not model.get("why_here"):
                problems.append(f"{model['id']}: 正文落点、绑定或教学理由未通过校验")
        if not dropped or attempt >= retries:
            status = "needs_review" if dropped or problems else "generated" if models else "not_applicable"
            return {"models": models,
                    "checks": {**base_checks, "status": status, "raw_count": len(specs), "kept": len(models),
                               "tolerance_notes": notes, "dropped": dropped, "problems": problems,
                               "decision": decision, "attempts": attempt + 1},
                    "usage": result.get("usage") or {}}
        last_error = "；".join(dropped)
        messages = messages + [{"role": "user", "content": "上一份规格未通过：" + last_error
                               + "。请只用本节允许的模板和解释落点，重新输出完整 JSON。"}]
    return {"models": [], "checks": {**base_checks, "status": "needs_review", "raw_count": 0,
            "kept": 0, "tolerance_notes": [], "dropped": [last_error], "problems": [],
            "attempts": retries + 1}, "usage": {}}


def write_models(out_dir: Path, unit_id: str, lesson_path: Path, payload: dict) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{unit_id}.json"
    # ⚠️ 记下"这份模型是拿**哪一版教案**生成的"（A18，2026-09-26）：
    #    原来只记路径、不记版本，于是 run_chapter 只能"文件在就跳过" ——
    #    教案被重写之后模型不会重跑，页面上的模型落点可能已经错了（建页会报"定位不到"）。
    #    记下 `source_lesson_revision` 之后，"跳过"就有了依据（见 `lib/scopes.models_are_fresh`）。
    _lesson_revision = int((load_json(lesson_path) or {}).get("revision") or 0)
    write_json(target, {
        "_meta": {"file_type": "handwritten_or_generated_models",
                  "schema_version": SCHEMA_VERSION, "generator": GENERATOR,
                  "unit_id": unit_id, "source_lesson": str(lesson_path),
                  "source_lesson_revision": _lesson_revision,
                  "note": "模型规格。渲染层只读这份；相机/尺寸/颜色/画法由渲染层决定"
                          "（见《AIforStudy-模型规格规范.md》）。"},
        "unit_id": unit_id,
        **payload,
    })
    return target


def guess_parse_dir(lesson_dir: Path) -> Path | None:
    """按约定猜解析目录：`data/lesson/<书>` → `data/parse/<书>`。

    P-016 的"前置知识门控"要用解析块的两份产物（`chapters.json` 的单元顺序、
    `concepts.json` 的概念出现位置），而模型块以前只管教案目录 —— 这里按约定猜，
    猜不到就不做前置判定（宁可放过，不能冤枉），并记一条提示。
    """
    lesson_dir = Path(lesson_dir)
    candidate = lesson_dir.parent.parent / "parse" / lesson_dir.name
    return candidate if (candidate / "chapters.json").is_file() else None


def run(lesson_dir: Path, out_dir: Path, *, only: list[str] | None = None, force: bool = False,
        client=None, retries: int = 1, dry_run: bool = False,
        parse_dir: Path | None = None, log=print) -> dict:
    lesson_dir, out_dir = Path(lesson_dir), Path(out_dir)
    parse_dir = Path(parse_dir) if parse_dir else guess_parse_dir(lesson_dir)
    if parse_dir is None:
        log("  ⚠ 没找到解析目录（chapters.json）→ 这次不做「前置知识」判定（换生书时请用 --parse 指路）")
    lessons = lesson_dir / "lessons"
    units = sorted(p.stem for p in lessons.glob("*.json"))
    if only:
        units = [u for u in units if u in set(only)]
    done, skipped, failed, calls, lines = [], [], [], 0, []
    for unit_id in units:
        lesson = load_json(lessons / f"{unit_id}.json")
        if not lesson:
            continue
        target = out_dir / f"{unit_id}.json"
        if target.is_file() and not force and (load_json(target) or {}).get("checks", {}).get("generated"):
            # P-008：**跳过的节也要查锚点**。实测：§2.1/§2.2 的模型是先出的、正文后重跑的，
            # 锚点早就失效了 —— 而"跳过"的路径从来不提这件事，页面上就一直堆在开头。
            stale = [str(m.get("id")) for m in ((load_json(target) or {}).get("models") or [])
                     if not m.get("anchor_hint_ok", True)]
            if stale:
                lines.append(f"{unit_id}: 已有产物的锚点已失效（{stale}），"
                             f"建议 --force 重跑这一节 —— 加 --force 即可")
                log(f"  ⚠ {unit_id}: 锚点已失效 {stale}（正文改过），加 --force 重跑这一节")
            skipped.append(unit_id)
            continue
        if dry_run:
            log(f"[dry-run] {unit_id}: 会给正文 {len(lesson.get('body_markdown') or '')} 字 + "
                f"{len(lesson.get('diagrams') or [])} 张过程图，要一次调用")
            continue
        log(f"{unit_id}: 生成模型规格…")
        payload = generate_models(lesson, diagrams=lesson.get("diagrams") or [],
                                  client=client, unit_id=unit_id,
                                  parse_dir=parse_dir if parse_dir else None,
                                  retries=retries, log=log)
        payload["checks"]["generated"] = True
        target = write_models(out_dir, unit_id, lessons / f"{unit_id}.json", payload)
        calls += payload["checks"].get("attempts", 1)
        stale = [str(m.get("id")) for m in payload["models"] if not m.get("anchor_hint_ok", True)]
        if stale:
            log(f"  ⚠ {unit_id}: {len(stale)} 个模型的 anchor_hint 在正文里找不到"
                f"（{stale}）—— 构建时会按 binds_to 重新定位；建议改完正文重跑一次")
        log(f"  保留 {payload['checks']['kept']} / {payload['checks']['raw_count']} 个模型"
            + (f"，容错 {len(payload['checks']['tolerance_notes'])} 处" if payload["checks"]["tolerance_notes"] else "")
            + (f"，丢弃 {len(payload['checks']['dropped'])} 个" if payload["checks"]["dropped"] else ""))
        (done if payload["checks"]["kept"] or payload["checks"]["raw_count"] == 0 else failed).append(unit_id)
    usage = getattr(client, "session_usage", None)
    return {"done": done, "skipped": skipped, "failed": failed, "calls": calls,
            "anchor_stale_notes": lines,
            "usage": usage.as_dict() if usage else {}, "out": str(out_dir)}


__all__ = ["run", "generate_models", "build_user_prompt", "available_ids",
           "curriculum_context", "plan_models", "write_models", "GENERATOR"]


def generate_assigned_models(content, *, assignments, client, unit_id, parse_dir,
                             requirements_hash, repair_context=None):
    """V4 delegates exact targets, models and source-bound reader placements."""
    from copy import deepcopy
    from aiforstudy.blocks.media.interaction_adapter import verify_template_response
    from aiforstudy.blocks.media.media_requirements import content_hash, candidates
    from aiforstudy.blocks.interaction.spec import interaction_capabilities
    from aiforstudy.blocks.interaction.placements import bind_assignment_placement
    from aiforstudy.errors import ContractError
    from aiforstudy.core.review_mode import reviews_frozen, frozen_review
    if reviews_frozen():
        messages = [{'role':'system','content':SYSTEM}, {'role':'user','content':json.dumps({
            'content':content, 'assignments':assignments,
            'instruction':'根据正文与委派目标生成交互模型，保持约定 models JSON 输出格式。'},ensure_ascii=False)}]
        answer = client.chat_json(messages, tag='interaction-assigned:'+unit_id)['data'] if assignments else {'models':[]}
        raw = answer.get('models', []) if isinstance(answer, dict) else []
        models = [deepcopy(row) for row in raw if isinstance(row,dict)] if isinstance(raw,list) else []
        for index, model in enumerate(models):
            model.setdefault('id',f'interaction-{index+1}')
        return {'schema_version':'InteractionResultV2', 'content_hash':content_hash(content),
            'requirements_hash':requirements_hash,'models':models,'unreviewed_response':answer,
            'review_outcome':frozen_review(),'review_status':'not_run','review_mode':'json_only',
            'results':[{'target_id':model.get('target_id',model.get('binds_to','')),
                'model_id':model['id'],'state':'unverified','spec':deepcopy(model),
                'placement':deepcopy(model.get('placement') or {}),'checks':{'status':'not_run'}} for model in models]}
    context = curriculum_context(parse_dir, unit_id)
    capabilities = interaction_capabilities(content, transport_available=client is not None, context=context)
    targets = {row['target_id']:row for row in candidates(content)}
    ids = {row['target_id'] for row in assignments}
    payload = {'schema_version':'InteractionResultV2', 'content_hash':content_hash(content),
               'requirements_hash':requirements_hash, 'models':[], 'results':[],
               'capabilities':capabilities}
    if not assignments: return payload
    unsupported = [target for target in ids if not capabilities['targets'].get(target, {}).get('templates')]
    if unsupported:
        payload['results'] = [{'target_id':row['target_id'], 'state':'failed', 'checks':{'ok':False},
            'reason':'委派目标无当前可用模板/传输/读者落点：'+row['target_id']} for row in assignments]
        return payload
    messages = [{'role':'system', 'content':SYSTEM+"\n这是原作者需求委派。逐个返回 assignments 的 models，每项额外含 target_id 和 placement。placement 使用 target_pointer、owner_id、step_id、精确 anchor_text、position=before/after，重复句用从1开始的 occurrence；不得填写 field_hash/span。只使用 target_capabilities 中该目标允许的模板及 reader_evidence 字段；binds_to 必须等于 target_id。模型ID在单元内唯一。不得返回空成功或撤销 required。"},
        {'role':'user', 'content':json.dumps({'approved_content':content, 'assignments':assignments,
            'target_capabilities':capabilities['targets'], 'reader_evidence':{target:targets[target]['reader_evidence'] for target in sorted(ids)},
            'repair_tickets':repair_context or [], 'content_hash':content_hash(content),
            'requirements_hash':requirements_hash}, ensure_ascii=False)}]
    answer = client.chat_json(messages, tag='interaction-assigned:'+unit_id)['data']
    raw = answer.get('models') if isinstance(answer, dict) else None
    if not isinstance(raw, list): raw = []
    raw_targets = [row.get('target_id') for row in raw if isinstance(row, dict)]
    for entry in raw:
        if not isinstance(entry, dict): continue
        target = entry.get('target_id')
        if target not in ids or raw_targets.count(target) != 1:
            payload['results'].append({'target_id':target, 'state':'failed', 'checks':{'ok':False},
                                      'reason':'目标未登记或重复返回'})
            continue
        capability = capabilities['targets'][target]
        item = {key:value for key,value in entry.items() if key not in ('target_id','placement')}
        local_body = '\n'.join(row['text'] for row in targets[target]['reader_evidence'])
        models, notes, dropped = normalize_models([item], body=local_body, available_ids=ids,
            introduced_ids=context.get('introduced_ids'), introduced_names=context.get('introduced_names'),
            all_concept_names=context.get('all_names'), allowed_templates=capability['templates'],
            template_bindings=capability['bindings'])
        try:
            if not models or dropped: raise ContractError('；'.join(dropped) or '缺有效交互规格')
            model = models[0]
            if not model.get('binds_to_ok') or model.get('binds_to') != target or not model.get('why_here'):
                raise ContractError('模型 binds_to 与委派目标或教学理由不一致')
            placement = bind_assignment_placement(content, target, model, entry.get('placement'))
            checks = verify_template_response(model)
            if not checks['ok']: raise ContractError(checks.get('reason') or '实际状态变化失败')
        except ContractError as exc:
            payload['results'].append({'target_id':target, 'state':'failed', 'checks':{'ok':False}, 'reason':str(exc)})
            continue
        payload['models'].append(model)
        payload['results'].append({'target_id':target, 'state':'verified', 'model_id':model['id'],
            'checks':checks, 'spec':deepcopy(model), 'placement':placement, 'notes':notes})
    for row in assignments:
        if row['target_id'] not in raw_targets:
            payload['results'].append({'target_id':row['target_id'], 'state':'failed', 'checks':{'ok':False},
                                      'reason':'委派目标未返回实际模型，不能视为空成功'})
    model_ids = [model['id'] for model in payload['models']]
    if len(model_ids) != len(set(model_ids)):
        for row in payload['results']: row.update(state='failed', checks={'ok':False}, reason='交互模型 ID 重复')
        payload['models'] = []
    return payload
