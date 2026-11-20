"""原子补丁：**要么整批落地，要么一个字节都不动**（§5.2）。

## 为什么必须是原子的

旧路径（`blocks/lesson/generate.apply_replacements`）的行为是"能落几条落几条"：
实测两条操作、一条命中一条没命中 → 正文被改写、另一条静默留在拒绝清单里，
而**没有任何版本/哈希说明这次改写基于哪一版**。审核返修最怕的就是这种半成品：
下一轮拿着不知道哪一版的正文继续判。

本模块把这件事做成结构性的：

* `patch.base_hash` 必须等于 `revision.hash`（过期补丁直接拒绝）；
* 每个 `operation.path` 必须落在工单 `allowed_paths` 白名单内，且不能有 `..`／反斜杠／冒号；
* 同一批里同一条路径上的区间**不许重叠**；
* 改动不许落在工单 `preserve` 声明的内容上；
* 结构校验（指针存在、仍是字符串、占位符计数不变）不过 → **不产生新 revision**。

## 拒绝必须可执行

每个拒绝都是 `Rejection(code, message, next_step)`，`next_step` 是**具体动作**
（"用 locate_evidence 重新定位这一处"／"把 base_hash 换成 revision N-1 的 hash"），
不是"请重试"。生成器拿到"请重试"只会把同一个错误再犯一遍。

## 白名单从哪儿来（签名限制下的唯一选择）

冻结签名是 `apply_patchset(patch, revision, artifact)`，**没有 ticket 参数**。
所以治理上下文只能由 `artifact` 携带，本模块按以下顺序读取：

* `artifact["ticket"]`：可以是 `RepairTicket` 或它的 `as_dict()`（推荐，字段最全）；
* `artifact["allowed_paths"]` / `artifact["preserve"]`：显式覆盖；
* `artifact["required_placeholders"]`：结构不变量（每个占位符出现次数不许变）。

拿不到白名单时**不是"放行"而是拒绝**：失败关闭（fail closed）是这里唯一安全的默认。

## 落地写回

`PatchOutcome` 没有"新内容"这一格（契约冻结），所以成功时本函数**就地更新**
`artifact`：写回被改的指针、更新 `artifact["hash"]`／`parent_hash`／`revision`。
拒绝时 `artifact` **一个字节都不动** —— 这就是"原子"的可观察含义。
"""
from __future__ import annotations

import hashlib

from ...core.digest import canonical_json
from .contracts import (ArtifactRevision, PatchOperation, PatchOutcome, PatchSet,
                        RepairTicket, Rejection)
from .evidence import resolve_json_pointer

#: 进内容哈希时要排除的治理字段：它们不是"被审的内容"，改了它们不该换 revision。
_GOVERNANCE_KEYS = frozenset({"ticket", "allowed_paths", "preserve",
                              "required_placeholders", "hash", "parent_hash", "revision"})

#: `preserve` 里以 `/` 开头的条目按 **JSON pointer** 解释（整条路径不许动）。
_INTERVAL_OPS = ("replace", "test")


# --------------------------------------------------------------------------- #
# 治理上下文
# --------------------------------------------------------------------------- #
def _as_paths(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _governance(artifact: dict) -> dict:
    """从 artifact 携带的工单上下文读出白名单／preserve／criterion 集合。"""
    ticket = artifact.get("ticket")
    allowed = artifact.get("allowed_paths")
    preserve = artifact.get("preserve")
    ticket_id = ""
    criterion_ids: tuple[str, ...] = ()
    if isinstance(ticket, RepairTicket):
        allowed = ticket.allowed_paths if allowed is None else allowed
        preserve = ticket.preserve if preserve is None else preserve
        ticket_id = ticket.ticket_id
        criterion_ids = tuple(item.criterion_id for item in ticket.criteria)
    elif isinstance(ticket, dict):
        allowed = ticket.get("allowed_paths") if allowed is None else allowed
        preserve = ticket.get("preserve") if preserve is None else preserve
        ticket_id = str(ticket.get("ticket_id") or "")
        criterion_ids = tuple(str(item.get("criterion_id"))
                              for item in (ticket.get("criteria") or ())
                              if isinstance(item, dict))
    return {"allowed_paths": _as_paths(allowed), "preserve": _as_paths(preserve),
            "ticket_id": ticket_id, "criterion_ids": criterion_ids}


def _path_escape_reason(path: str) -> str:
    """路径本身是不是在试图逃出白名单（不是"白名单里没有"，是"这不是一条合法指针"）。"""
    text = str(path or "")
    if not text.startswith("/"):
        return "不是 JSON pointer（必须以 `/` 开头）"
    if "\\" in text:
        return "含反斜杠（像文件系统路径，不是 JSON pointer）"
    if ":" in text:
        return "含冒号（像盘符／URL，不是 JSON pointer）"
    if "\x00" in text:
        return "含 NUL 字符"
    if ".." in text.split("/"):
        return "含 `..` 段（路径逃逸）"
    if any(segment == "" for segment in text.split("/")[1:]):
        return "含空段（`//`），不是合法 JSON pointer"
    return ""


def _path_allowed(path: str, allowed: tuple[str, ...]) -> bool:
    for entry in allowed:
        entry = str(entry or "")
        if entry in ("", "/", "*"):
            return True
        if path == entry or path.startswith(entry.rstrip("/") + "/"):
            return True
    return False


def _interval(operation: PatchOperation) -> tuple[int, int]:
    start = int(operation.start)
    if operation.op == "insert_after":
        return start, start + 1
    return start, int(operation.end)


def _preserve_reason(entry: str, operation: PatchOperation, text: str) -> str:
    """这次改动是不是落到了 `preserve` 声明的内容上。"""
    entry = str(entry or "")
    if not entry:
        return ""
    if entry.startswith("/"):
        if operation.path == entry or operation.path.startswith(entry.rstrip("/") + "/"):
            return f"这次改动落在 preserve 声明的整条路径 {entry!r} 上"
        return ""
    hits: list[tuple[int, int]] = []
    cursor = 0
    while True:
        found = text.find(entry, cursor)
        if found < 0:
            break
        hits.append((found, found + len(entry)))
        cursor = found + len(entry)
    if not hits:
        return ""
    if operation.op == "insert_after":
        point = int(operation.start)
        if any(start <= point < end for start, end in hits):
            return (f"插入点 {point} 落在必须保留的内容 {entry[:24]!r} 内部")
        return ""
    start, end = int(operation.start), int(operation.end)
    if any(max(start, hit_start) < min(end, hit_end) for hit_start, hit_end in hits):
        return f"改动区间 [{start},{end}) 与必须保留的内容 {entry[:24]!r} 重叠"
    return ""


def _occurrences(text: str, needle: str) -> int:
    if not needle:
        return 0
    count = 0
    cursor = 0
    while True:
        found = text.find(needle, cursor)
        if found < 0:
            return count
        count += 1
        cursor = found + len(needle)


def _set_pointer(document, pointer: str, value) -> None:
    """把 `pointer` 指向的值写回 `document`（只在整批校验通过后调用）。"""
    tokens = [token.replace("~1", "/").replace("~0", "~")
              for token in str(pointer).split("/")[1:]]
    current = document
    for token in tokens[:-1]:
        current = current[int(token)] if isinstance(current, (list, tuple)) else current[token]
    last = tokens[-1]
    if isinstance(current, (list, tuple)):
        current[int(last)] = value
    else:
        current[last] = value


def _content_hash(artifact: dict) -> str:
    payload = {key: value for key, value in artifact.items()
               if key not in _GOVERNANCE_KEYS}
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 冻结接口
# --------------------------------------------------------------------------- #
def apply_patchset(patch: PatchSet, revision: ArtifactRevision,
                   artifact: dict) -> PatchOutcome:
    """把一批补丁**原子地**应用到 `artifact` 上（§5.2 冻结接口）。

    成功时：`applied=True`、`result_revision=revision.revision+1`，并且 `artifact`
    已经带上改后的内容与新的 `hash`／`parent_hash`／`revision`。
    失败时：`applied=False`、逐条 `operation_results` 与 `Rejection`，`artifact` 不被触碰。
    """
    artifact = artifact if isinstance(artifact, dict) else {}
    governance = _governance(artifact)
    allowed = governance["allowed_paths"]
    global_rejections: list[Rejection] = []

    declared_hash = str(artifact.get("hash") or artifact.get("artifact_hash") or "")
    if str(patch.base_hash) != str(revision.hash):
        global_rejections.append(Rejection(
            "BASE_HASH_MISMATCH",
            f"补丁声称基于 {str(patch.base_hash)[:24]}…，而当前 revision "
            f"{revision.revision} 的 hash 是 {str(revision.hash)[:24]}… —— "
            f"过期的补丁不许应用",
            f"按 revision {revision.revision}（hash {str(revision.hash)[:24]}…）重新生成"
            f"补丁：重新读一次当前正文，把 operation 的 start/end 按现在的偏移重算，"
            f"再把 base_hash 设成这个 hash"))
    if declared_hash and declared_hash != str(revision.hash):
        global_rejections.append(Rejection(
            "ARTIFACT_HASH_MISMATCH",
            f"artifact 自称 hash {declared_hash[:24]}…，而 revision {revision.revision} "
            f"是 {str(revision.hash)[:24]}… —— 改的可能是别的版本",
            "把 artifact 换成这个 revision 对应的那一份内容再提交补丁"))
    declared_artifact_id = str(artifact.get("artifact_id") or "")
    if declared_artifact_id and declared_artifact_id != revision.artifact_id:
        global_rejections.append(Rejection(
            "ARTIFACT_ID_MISMATCH",
            f"artifact {declared_artifact_id!r} 与 revision {revision.artifact_id!r} 不是同一个",
            "把补丁指向 revision 对应的 artifact_id"))

    if not allowed:
        global_rejections.append(Rejection(
            "NO_WHITELIST",
            "补丁没有可用的 `allowed_paths` 白名单（artifact 里既没有 allowed_paths，"
            "也没有携带工单）—— 没有白名单就无法证明这次改动在授权范围内",
            "在 artifact 里带上工单（`artifact['ticket']`，含 allowed_paths/preserve）"
            "或显式给出 `artifact['allowed_paths']`"))
    elif governance["ticket_id"]:
        if not patch.ticket_ids:
            global_rejections.append(Rejection(
                "NO_TICKET_BINDING",
                f"补丁没有绑定工单 {governance['ticket_id']} —— "
                f"没有工单的改动无法追溯是谁要求的、按什么标准验收",
                f"把 `ticket_ids=[{governance['ticket_id']!r}]` 写进 PatchSet"))
        elif governance["ticket_id"] not in tuple(patch.ticket_ids):
            global_rejections.append(Rejection(
                "TICKET_MISMATCH",
                f"补丁声明的工单 {list(patch.ticket_ids)} 里没有当前工单 "
                f"{governance['ticket_id']} —— 拿别的工单的授权改这里",
                f"把 {governance['ticket_id']!r} 加进 patch.ticket_ids，"
                f"或针对该工单另出一批补丁"))

    if governance["criterion_ids"]:
        unknown = sorted(set(patch.criterion_ids) - set(governance["criterion_ids"]))
        if unknown:
            global_rejections.append(Rejection(
                "UNKNOWN_CRITERION",
                f"补丁声称满足 criterion {unknown}，但工单 {governance['ticket_id']} 里"
                f"冻结的标准只有 {list(governance['criterion_ids'])} —— "
                f"新问题要新建工单，不许拿别的标准来改",
                "把 patch.criterion_ids 换成工单里冻结的 criterion_id；"
                "如果是新问题，先为它建工单并冻结标准"))

    values: dict[str, str] = {}
    operation_results: list[dict] = []
    for index, operation in enumerate(patch.operations):
        entry: dict = {"index": index, "op": operation.op, "path": operation.path,
                       "start": operation.start, "end": operation.end,
                       "status": "rejected", "rejections": []}
        rejections: list[Rejection] = []

        escape = _path_escape_reason(operation.path)
        if escape:
            rejections.append(Rejection(
                "PATH_ESCAPE",
                f"operation {index} 的 path {operation.path!r} 不合法：{escape}",
                "改用 artifact 内真实存在的 JSON pointer（以 `/` 开头，"
                "不含 `..`／反斜杠／冒号）"))
        elif not _path_allowed(operation.path, allowed):
            rejections.append(Rejection(
                "PATH_NOT_ALLOWED",
                f"operation {index} 的 path {operation.path!r} 不在工单白名单 "
                f"{list(allowed)} 里",
                f"把 path 改成白名单里的 {list(allowed)}（或它们的子路径）；"
                f"确实需要改别处，先让 reviewer 更新工单的 allowed_paths 并留裁定依据"))

        found, value = resolve_json_pointer(artifact, operation.path)
        text = ""
        if not rejections:
            if not found:
                rejections.append(Rejection(
                    "POINTER_NOT_FOUND",
                    f"operation {index} 指向的 {operation.path!r} 在 artifact 里不存在 —— "
                    f"指针失效（多半是引用了旧 revision 的结构）",
                    "用 locate_evidence 在**当前** revision 上重新定位这一处，"
                    "再按定位结果重算 path 与 start/end"))
            elif not isinstance(value, str):
                rejections.append(Rejection(
                    "NOT_A_STRING",
                    f"operation {index} 指向的 {operation.path!r} 不是字符串"
                    f"（实际 {type(value).__name__}）—— 补丁只改文本",
                    "把 path 指到真正的正文字符串上（例如 /body_markdown）；"
                    "结构化字段请走各自类型的校验"))
            else:
                text = value
                length = len(text)
                start = int(operation.start)
                if start < 0 or start > length:
                    rejections.append(Rejection(
                        "RANGE_OUT_OF_BOUNDS",
                        f"operation {index} 的 start={start} 超出正文长度 {length}",
                        f"在**当前** revision 上重新定位（locate_evidence），"
                        f"把 start 落在 0..{length} 之内"))
                elif operation.op == "insert_after":
                    pass
                elif int(operation.end) > length:
                    rejections.append(Rejection(
                        "RANGE_OUT_OF_BOUNDS",
                        f"operation {index} 的 end={operation.end} 超出正文长度 {length}",
                        f"在**当前** revision 上重新定位（locate_evidence），"
                        f"把 end 落在 {start}..{length} 之内"))
                else:
                    slice_text = text[start:int(operation.end)]
                    if operation.op == "replace":
                        if slice_text == operation.text:
                            rejections.append(Rejection(
                                "NO_CHANGE",
                                f"operation {index} 的 new 与原文 [{start},{operation.end}) "
                                f"一模一样 —— 这不是一次修改",
                                "要么写下真正改后的文字，要么去掉这条操作；"
                                "如果已经不需要改，请让 reviewer 重验并给出有效证据"))
                    elif operation.op == "test":
                        if slice_text != operation.text:
                            rejections.append(Rejection(
                                "TEST_UNSATISFIED",
                                f"operation {index} 断言 [{start},{operation.end}) 等于 "
                                f"{operation.text[:24]!r}，实际是 {slice_text[:24]!r}",
                                "按当前正文重写这条 test 的 text（或在当前 revision 上"
                                "重新定位后再断言）"))
                        elif _occurrences(text, operation.text) != 1:
                            rejections.append(Rejection(
                                "AMBIGUOUS_ANCHOR",
                                f"operation {index} 断言的文本在当前正文里出现 "
                                f"{_occurrences(text, operation.text)} 次 —— "
                                f"多处匹配时不许改，必须先确定唯一位置",
                                "把范围收窄到只覆盖其中一处的 start/end，"
                                "或先用 locate_evidence 拿到唯一 span（它会返回 ambiguous）"))
            if not rejections:
                for entry_text in governance["preserve"]:
                    reason = _preserve_reason(entry_text, operation, text)
                    if reason:
                        rejections.append(Rejection(
                            "PRESERVE_VIOLATION", f"operation {index}：{reason}",
                            f"收窄 start/end 避开 {entry_text[:24]!r}；"
                            f"确实必须动它，先让 reviewer 调整工单的 preserve 并留依据"))
                        break
        entry["rejections"] = [item.as_dict() for item in rejections]
        operation_results.append(entry)

    # 同一批里同一条路径上的区间不许重叠（重叠 = 后一条依赖前一条的偏移，必然改错）。
    grouped: dict[str, list[tuple[tuple[int, int], int]]] = {}
    for index, operation in enumerate(patch.operations):
        grouped.setdefault(operation.path, []).append((_interval(operation), index))
    overlap_indices: set[int] = set()
    for path, items in grouped.items():
        ordered = sorted(items)
        for (first, first_index), (second, second_index) in zip(ordered, ordered[1:]):
            if max(first[0], second[0]) < min(first[1], second[1]):
                overlap_indices.add(first_index)
                overlap_indices.add(second_index)
    for index in sorted(overlap_indices):
        operation = patch.operations[index]
        message = (f"operation {index}（{operation.path} [{operation.start},"
                   f"{operation.end})）与同一批里另一条操作的区间重叠")
        rejection = Rejection("OVERLAPPING_OPERATIONS", message,
                              "把重叠的操作拆成两批（前一批落地并产生新 revision 后，"
                              "再按新偏移提交后一批），或合并成一条覆盖整个区间的 replace")
        operation_results[index]["rejections"].append(rejection.as_dict())

    rejections_all: list[Rejection] = list(global_rejections)
    for entry in operation_results:
        for raw in entry["rejections"]:
            rejections_all.append(Rejection(str(raw["code"]), str(raw["message"]),
                                            str(raw["next_step"])))
    if rejections_all:
        return PatchOutcome(applied=False,
                            reason=(f"补丁被原子拒绝：{len(rejections_all)} 条阻塞项，"
                                    f"artifact 未被修改（{len(patch.operations)} 个操作）"),
                            rejected=tuple(rejections_all),
                            result_revision=None,
                            operation_results=tuple(operation_results))

    # ── 整批校验通过：一次性算出所有新值 → 结构校验 → 才写回
    by_path: dict[str, list[PatchOperation]] = {}
    for operation in patch.operations:
        by_path.setdefault(operation.path, []).append(operation)
    new_values: dict[str, str] = {}
    for path, operations in by_path.items():
        _, value = resolve_json_pointer(artifact, path)
        text = str(value)
        for operation in sorted(operations, key=lambda item: int(item.start),
                                reverse=True):
            if operation.op == "insert_after":
                point = int(operation.start)
                text = text[:point] + str(operation.text) + text[point:]
            elif operation.op == "replace":
                text = (text[:int(operation.start)] + str(operation.text)
                        + text[int(operation.end):])
            # test：只断言，不改内容
        new_values[path] = text

    structure_rejections: list[Rejection] = []
    for path, new_text in new_values.items():
        _, before = resolve_json_pointer(artifact, path)
        before_text = str(before)
        if before_text.strip() and not new_text.strip():
            structure_rejections.append(Rejection(
                "STRUCTURE_INVALID", f"operation 把 {path!r} 整段清空了",
                "改小替换范围（保留被替换段落里必须留下的文字），再重新提交"))
        for token in artifact.get("required_placeholders") or ():
            token = str(token)
            before_count = _occurrences(before_text, token)
            after_count = _occurrences(new_text, token)
            if before_count != after_count:
                structure_rejections.append(Rejection(
                    "STRUCTURE_INVALID",
                    f"占位符 {token!r} 在 {path!r} 里的出现次数从 {before_count} "
                    f"变成 {after_count} —— 结构被破坏（这类占位符在整节里只能出现一次）",
                    f"让替换后的文字里 {token!r} 恰好保留原来的次数；"
                    f"如果不确定，先用 locate_evidence 定位它在正文里的位置"))
    if structure_rejections:
        return PatchOutcome(applied=False,
                            reason=(f"补丁结构校验失败：{len(structure_rejections)} 条"
                                    f"（不提交新 revision）"),
                            rejected=tuple(structure_rejections), result_revision=None,
                            operation_results=tuple(operation_results))

    if not any(operation.op in ("replace", "insert_after")
               for operation in patch.operations):
        # 只有 test 断言且全部成立：**没有内容改动，就不产生新 revision**。
        # 凭空造一个"和上一版一模一样的新版本"会让后续的失效判断凭空多一次。
        for index in range(len(operation_results)):
            operation_results[index]["status"] = "tested"
        return PatchOutcome(
            applied=True,
            reason="这一批只有 test 断言且全部成立：正文无改动，不产生新 revision",
            rejected=(), result_revision=None,
            operation_results=tuple(operation_results))

    for path, new_text in new_values.items():
        _set_pointer(artifact, path, new_text)
    new_hash = _content_hash(artifact)
    artifact["hash"] = new_hash
    artifact["parent_hash"] = revision.hash
    artifact["revision"] = revision.revision + 1
    for index, operation in enumerate(patch.operations):
        operation_results[index]["status"] = ("tested" if operation.op == "test"
                                              else "applied")
    return PatchOutcome(
        applied=True,
        reason=(f"补丁原子应用成功：{len(patch.operations)} 个操作全部落地，"
                f"新 revision={revision.revision + 1}，hash={new_hash[:24]}…"),
        rejected=(), result_revision=revision.revision + 1,
        operation_results=tuple(operation_results))


__all__ = ["apply_patchset"]
