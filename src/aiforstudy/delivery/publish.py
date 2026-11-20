"""原子发布：**失败不覆盖旧发布**，按清单切换指针。

方案 §10：

> 发布按新 manifest **原子切换 current**，不原地覆盖旧成功版本。单章发布只替换目标章引用，
> 其余已发布产物**字节哈希不变**；全书派生视图可按清单重算。基础版本变更使其他章过期时
> 明确记录 stale，不自动重生成。

方案 §12：

> 默认交付包括章页面目录、目标范围单 HTML、ZIP、manifest 与验收摘要；生产运行自动完成。
> ZIP 从**已验证清单**构建，成员哈希与清单一致。正式包只含阅读所需材料，
> 不带密钥、调试日志或内部临时文件。

## 为什么必须"不原地覆盖"

"就地更新目录"的实现有两个后果，而且都不报错：
① 更新到一半失败 → 交付目录处于**半新半旧**状态，而它看起来是完整的；
② 新版本有问题 → **旧版本已经没了**，连回退对象都不存在。

所以这里只有一种写法：先在**新目录**里把整份 release 建好、验完，最后**原子替换
一个指针文件**（`current.json`）。中途任何一步失败，指针不动，读者看到的仍是上一份合格版本。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..core.digest import digest_bytes, digest_file
from ..core.jsonio import read_json_soft, write_json_atomic
from ..errors import ContractError

CURRENT_NAME = "current.json"
RELEASE_SCHEMA = "1.0"
PUBLICATION_RECEIPTS = "publication_receipts"


@dataclass
class ChapterRef:
    chapter_key: str
    release_id: str
    page_digest: str = ""
    parse_revision: str = ""
    context_revision: str = ""

    def as_dict(self) -> dict:
        return {"chapter_key": self.chapter_key, "release_id": self.release_id,
                "page_digest": self.page_digest, "parse_revision": self.parse_revision,
                "context_revision": self.context_revision}


@dataclass
class PublishResult:
    release_id: str
    release_dir: str
    switched: bool
    previous_release: str = ""
    replaced_chapters: tuple[str, ...] = ()
    untouched_chapters: tuple[str, ...] = ()
    member_hashes: dict = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"release_id": self.release_id, "release_dir": self.release_dir,
                "switched": self.switched, "previous_release": self.previous_release,
                "replaced_chapters": list(self.replaced_chapters),
                "untouched_chapters": list(self.untouched_chapters),
                "member_hashes": dict(self.member_hashes), "notes": list(self.notes)}


def _iter_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def member_hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): digest_file(p) for p in _iter_files(root)}


def read_current(releases_root: Path) -> dict:
    return read_json_soft(Path(releases_root) / CURRENT_NAME) or {}


def build_release(releases_root: Path, release_id: str, *, manifest: dict,
                  files: dict[str, bytes], verify_hashes: bool = True) -> tuple[Path, dict]:
    """把一份 release 建在**新目录**里，并逐个核对成员哈希。

    返回 `(release_dir, member_hashes)`。**这一步不碰 current 指针** ——
    建不成、验不过，读者看到的仍是旧版本。
    """
    releases_root = Path(releases_root)
    # ⚠️ 必须先建根目录：`tempfile.mkdtemp(dir=...)` 在父目录不存在时直接抛
    #    FileNotFoundError，而那时调用方看到的是一句"文件找不到"，跟"发布"毫无关系。
    releases_root.mkdir(parents=True, exist_ok=True)
    target = releases_root / release_id
    if target.exists():
        raise ContractError(
            f"release 目录已存在：{target}。发布**不原地覆盖**（方案 §10）——"
            f"不复用旧目录名，请换一个 release_id。")
    staging = Path(tempfile.mkdtemp(prefix=f".{release_id}.", dir=str(releases_root)))
    try:
        for rel, blob in files.items():
            path = staging / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
        write_json_atomic(staging / "manifest.json",
                          {"_meta": {"file_type": "release_manifest",
                                     "schema_version": RELEASE_SCHEMA,
                                     "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
                           **manifest})
        hashes = member_hashes(staging)
        declared = (manifest.get("members") or {})
        if verify_hashes and declared:
            mismatched = {rel: (declared.get(rel), sha) for rel, sha in hashes.items()
                          if rel in declared and declared[rel] != sha}
            missing = [rel for rel in declared if rel not in hashes]
            if mismatched or missing:
                raise ContractError(
                    f"release 成员与清单不一致：对不上 {list(mismatched)[:3]}｜"
                    f"缺 {missing[:3]}。方案 §12：ZIP 从**已验证清单**构建，"
                    f"成员哈希与清单一致。")
        os.replace(staging, target)      # 原子：要么整份在，要么整份不在
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target, hashes


def switch_current(releases_root: Path, release_id: str, *, book_id: str,
                   scope_kind: str, chapters: tuple[ChapterRef, ...] = ()) -> PublishResult:
    """**原子切换** current 指针。

    单章/多章发布按 `ChapterRef` **只替换目标章**的引用，其余章保持原样
    （方案 §10：其余已发布产物字节哈希不变）。整本发布才整体替换。
    此低层接口只切换指针。正式发布由已核验的发布流程记录。
    """
    return _switch_current(releases_root, release_id, book_id=book_id,
                           scope_kind=scope_kind, chapters=chapters)


def _switch_current(releases_root: Path, release_id: str, *, book_id: str, scope_kind: str,
                    chapters: tuple[ChapterRef, ...],
                    verified_hashes: dict[str, str] | None = None) -> PublishResult:
    from ..pipeline.lease import ReleasePointerLease

    releases_root = Path(releases_root)
    if _safe_relative(release_id) != release_id or '/' in release_id:
        raise ContractError(f"无效的发布 release_id：{release_id!r}")
    release_dir = releases_root / release_id
    if not release_dir.is_dir():
        raise ContractError(f"release 目录不在位：{release_dir}")
    with ReleasePointerLease(releases_root):
        if verified_hashes is None:
            hashes = member_hashes(release_dir)
        else:
            _verify_release_files(release_dir, verified_hashes)
            hashes = verified_hashes
        previous = read_current(releases_root)
        merged, replaced, untouched = _select_chapters(previous, scope_kind, chapters)
        # Store the original activation evidence in the same atomic write as the pointer.
        receipts = dict(previous.get(PUBLICATION_RECEIPTS, {}))
        if release_id in receipts or verified_hashes is not None:
            manifest_hash = hashes.get("manifest.json")
            if not manifest_hash:
                raise ContractError(f"release 的清单已缺失：{release_id}")
            receipt = _publication_receipt(book_id, scope_kind, chapters, manifest_hash)
            if release_id in receipts:
                if receipts[release_id] != receipt:
                    raise ContractError(f"同名 release 的原始发布证据不一致：{release_id}")
            else:
                receipts[release_id] = receipt

        payload = {"_meta": {"file_type": "current_release", "schema_version": RELEASE_SCHEMA,
                             "generator": "aiforstudy.delivery.publish",
                             "field_notes": {
                                 "release_id": "当前生效的 release（指针）",
                                 "chapters": "按章引用不同范围 release 的产物；每个引用自带 parse/context 版本",
                                 "note": "单章发布只替换目标章；其余章字节不变",
                             }},
                   "book_id": book_id, "release_id": release_id, "scope_kind": scope_kind,
                   "chapters": merged, PUBLICATION_RECEIPTS: receipts,
                   "switched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        write_json_atomic(releases_root / CURRENT_NAME, payload)
    return PublishResult(release_id=release_id, release_dir=str(release_dir), switched=True,
                         previous_release=str(previous.get("release_id") or ""),
                         replaced_chapters=replaced, untouched_chapters=untouched,
                         member_hashes=hashes,
                         notes=(f"未替换的章 {len(untouched)} 个，引用保持不变",))


def _select_chapters(
    previous: dict, scope_kind: str, chapters: tuple[ChapterRef, ...]
) -> tuple[list[dict], tuple[str, ...], tuple[str, ...]]:
    selected = [ref.as_dict() for ref in chapters]
    replaced = tuple(ref.chapter_key for ref in chapters)
    other = {row["chapter_key"]: row for row in previous.get("chapters") or []
             if row["chapter_key"] not in replaced}
    if scope_kind != "book":
        selected.extend(other.values())
        selected.sort(key=lambda row: row["chapter_key"])
    return selected, replaced, tuple(sorted(other))


def assert_release_validated(release_dir: Path, *, checks_ok: bool) -> None:
    """发布前的最后一道：**检查没过就不许发布**（§9/§15 A23）。"""
    if not checks_ok:
        raise ContractError(
            f"{release_dir} 的必要检查未通过，**不许发布**。"
            f"方案 §9：正式发布要求所有必要检查 accepted；"
            f"§15 A23：检查非零则包不发布。")


def _safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or any(c in value for c in ('\\', ':', '\0')):
        raise ContractError(f'发布成员必须是安全相对路径：{value!r}')
    rel = PurePosixPath(value)
    if not rel.parts or rel.is_absolute() or '..' in rel.parts:
        raise ContractError(f'发布成员必须是安全相对路径：{value!r}')
    return str(rel)


def _require_offline_acceptance(acceptance: object) -> None:
    if (not isinstance(acceptance, dict) or acceptance.get("passed") is not True
            or acceptance.get("blocked") is not False):
        raise ContractError("真实浏览器验收未通过")
    checks = acceptance.get("checks")
    if (not isinstance(checks, list)
            or any(not isinstance(check, dict) or check.get("status") != "accepted"
                   or check.get("ok") is False for check in checks)
            or sum(check.get("name") == "browser-execution" for check in checks) != 1):
        raise ContractError("离线验收记录有冲突或缺实际浏览器执行证据")


def _read_manifest(release_dir: Path) -> tuple[dict, str]:
    manifest_path = release_dir / "manifest.json"
    if release_dir.is_symlink() or manifest_path.is_symlink():
        raise ContractError(f"回退目标包含符号链接：{release_dir}")
    try:
        data = manifest_path.read_bytes()
        manifest = json.loads(data)
    except (OSError, ValueError) as exc:
        raise ContractError(f"无法读取 release 清单：{manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise ContractError(f"回退目标不是一份合格 release：{release_dir}")
    return manifest, digest_bytes(data)


def _verify_release_files(release_dir: Path, expected_hashes: dict[str, str]) -> None:
    if release_dir.is_symlink() or not release_dir.is_dir():
        raise ContractError(f"无法核对 release 目录：{release_dir}")
    actual_names = set()
    for path in release_dir.rglob("*"):
        if path.is_symlink():
            raise ContractError(f"release 包含符号链接：{path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ContractError(f"release 包含无法核对的资源：{path}")
        actual_names.add(path.relative_to(release_dir).as_posix())
    if actual_names != expected_hashes.keys():
        raise ContractError(f"release 资源清单改变：{release_dir}")
    for name, expected in expected_hashes.items():
        try:
            actual = digest_file(release_dir / name)
        except OSError as exc:
            raise ContractError(f"无法核对 release 资源：{name}") from exc
        if actual != expected:
            raise ContractError(f"release 资源哈希改变：{name}")


def _rollback_manifest(release_dir: Path, release_id: str) -> tuple[dict, str]:
    manifest_path = release_dir / "manifest.json"
    manifest, manifest_hash = _read_manifest(release_dir)
    meta = manifest.get("_meta")
    book_id, scope_kind = manifest.get("book_id"), manifest.get("scope_kind")
    if (not isinstance(meta, dict) or meta.get("file_type") != "release_manifest"
            or meta.get("schema_version") != RELEASE_SCHEMA
            or manifest.get("release_id") != release_id
            or not isinstance(book_id, str) or not book_id.strip()
            or scope_kind not in ("all", "book", "chapters", "units", "selection")):
        raise ContractError(f"回退清单身份或范围不一致：{manifest_path}")
    _require_offline_acceptance(manifest.get("offline_acceptance"))

    chapters, scope = manifest.get("chapters"), manifest.get("scope")
    for entries in (chapters, scope):
        if (not isinstance(entries, list) or not entries
                or any(not isinstance(item, str) or not item.strip() for item in entries)
                or len(set(entries)) != len(entries)):
            raise ContractError(f"回退清单缺少有效的章节或范围：{manifest_path}")
    if any(_safe_relative(chapter) != chapter or '/' in chapter for chapter in chapters):
        raise ContractError(f"回退清单的章节名称无效：{manifest_path}")
    chapter_keys = set(chapters)
    if {unit.split("-", 1)[0] for unit in scope} != chapter_keys:
        raise ContractError(f"回退清单的范围与章节不一致：{manifest_path}")
    if any(not isinstance(manifest.get(field, ""), str)
           for field in ("parse_revision", "context_revision")):
        raise ContractError(f"回退清单的修订字段无效：{manifest_path}")

    members = manifest.get("members")
    if not isinstance(members, dict) or any(f"{chapter}/index.html" not in members
                                            for chapter in chapters):
        raise ContractError(f"回退清单缺少章页面：{manifest_path}")
    for name in members:
        if _safe_relative(name) != name or PurePosixPath(name).parts[0] not in chapter_keys:
            raise ContractError(f"回退资源不在清单范围：{name}")
    _verify_release_files(release_dir, {**members, "manifest.json": manifest_hash})
    return manifest, manifest_hash


def _publication_receipt(
    book_id: str, scope_kind: str, chapters: tuple[ChapterRef, ...], manifest_hash: str
) -> dict:
    return {"book_id": book_id, "scope_kind": scope_kind, "manifest_hash": manifest_hash,
            "chapters": [chapter.as_dict() for chapter in chapters]}


def _pointer_scope(scope_kind: str) -> str:
    return "book" if scope_kind in ("all", "book") else "selection"


def _legacy_publication_receipt(
    release_dir: Path, manifest: dict, manifest_hash: str, *, run_root: Path | None = None
) -> dict:
    release_dir = release_dir.resolve()
    release_id, book_id = manifest["release_id"], manifest["book_id"]
    releases = release_dir.parent
    if run_root is None:
        # The historical engine always placed its run journal under the book root.
        book_releases = releases.parent.parent if releases.parent.name == "selections" else releases
        run_root = book_releases.parent / "runs" / release_id
    state = read_json_soft(run_root / "state.json")
    publication = state.get("publication") if isinstance(state, dict) else None
    if (not isinstance(publication, dict) or state.get("run_id") != release_id
            or state.get("book_id") != book_id or publication.get("release_id") != release_id
            or publication.get("formally_published") is not True or publication.get("switched") is not True
            or publication.get("scope") != manifest["scope"]
            or publication.get("replaced_chapters") != manifest["chapters"]
            or publication.get("namespace") != manifest.get("namespace")
            or publication.get("release_dir") != str(release_dir)
            or publication.get("manifest") != str(release_dir / "manifest.json")
            or publication.get("pointer") != str(releases / CURRENT_NAME)):
        raise ContractError(f"缺少可核对的原始发布记录：{release_id}")
    published_hashes = publication.get("member_hashes")
    if not isinstance(published_hashes, dict) or published_hashes.get("manifest.json") != manifest_hash:
        raise ContractError(f"回退清单与原始发布记录不一致：{release_id}")

    delivery_path = run_root / "work/products/delivery/delivery.json"
    try:
        data = delivery_path.read_bytes()
        delivery = json.loads(data)
    except (OSError, ValueError) as exc:
        raise ContractError(f"无法核对原始交付产物：{release_id}") from exc
    meta = delivery.get("_meta") if isinstance(delivery, dict) else None
    revision = meta.get("revision") if isinstance(meta, dict) else None
    if (digest_bytes(data) != manifest.get("delivery_artifact_digest")
            or not isinstance(revision, str) or not revision.strip()
            or meta.get("book_id") != book_id or meta.get("run_id") != release_id
            or meta.get("stage") != "delivery" or meta.get("file_type") != "delivery.scope"):
        raise ContractError(f"原始交付产物的书籍、运行或修订身份无效：{release_id}")
    refs = tuple(ChapterRef(chapter, release_id, manifest["members"][f"{chapter}/index.html"], revision)
                 for chapter in manifest["chapters"])
    return _publication_receipt(book_id, _pointer_scope(manifest["scope_kind"]), refs, manifest_hash)


def _chapter_refs_from_receipt(manifest: dict, receipt: dict, manifest_hash: str) -> tuple[ChapterRef, ...]:
    release_id = manifest["release_id"]
    if (not isinstance(receipt, dict) or receipt.get("book_id") != manifest["book_id"]
            or receipt.get("manifest_hash") != manifest_hash
            or receipt.get("scope_kind") not in ("book", "selection", "chapters", "units")
            or _pointer_scope(receipt["scope_kind"]) != _pointer_scope(manifest["scope_kind"])):
        raise ContractError(f"回退清单与原始发布证据不一致：{release_id}")
    rows = receipt.get("chapters")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ContractError(f"原始发布缺少章节引用：{release_id}")
    if [row.get("chapter_key") for row in rows] != manifest["chapters"]:
        raise ContractError(f"回退章节与原始发布证据不一致：{release_id}")

    refs = []
    for row in rows:
        chapter = row["chapter_key"]
        page_digest = manifest["members"][f"{chapter}/index.html"]
        revision, context_revision = row.get("parse_revision"), row.get("context_revision")
        if (row.get("release_id") != release_id or row.get("page_digest") != page_digest
                or not isinstance(revision, str) or not revision.strip()
                or not isinstance(context_revision, str)
                or manifest.get("parse_revision", revision) != revision
                or manifest.get("context_revision", context_revision) != context_revision):
            raise ContractError(f"原始章节发布引用无效：{chapter}")
        refs.append(ChapterRef(chapter, release_id, page_digest, revision, context_revision))
    return tuple(refs)


def rollback(releases_root: Path, release_id: str) -> PublishResult:
    """核对目标资源，再按目标范围原子恢复章节引用（§16）。"""
    from ..pipeline.lease import ReleasePointerLease

    releases_root = Path(releases_root)
    if _safe_relative(release_id) != release_id or '/' in release_id:
        raise ContractError(f"无效的回退 release_id：{release_id!r}")
    release_dir = releases_root / release_id
    with ReleasePointerLease(releases_root):
        manifest, manifest_hash = _rollback_manifest(release_dir, release_id)
        members = manifest["members"]
        previous = read_current(releases_root)
        if previous.get("book_id") not in (None, manifest["book_id"]):
            raise ContractError(f"回退目标属于另一书籍：{release_dir}")
        receipts = previous.get(PUBLICATION_RECEIPTS, {})
        receipt = receipts[release_id] if release_id in receipts else _legacy_publication_receipt(
            release_dir, manifest, manifest_hash)
        refs = _chapter_refs_from_receipt(manifest, receipt, manifest_hash)
        scope_kind = receipt["scope_kind"]
        chapters, replaced, untouched = _select_chapters(previous, scope_kind, refs)
        payload = {**previous,
                   "_meta": {**(previous.get("_meta") or {}),
                             "note": "回退：原子切换指针到上一份合格 release（已发生费用不回滚）"},
                   "book_id": manifest["book_id"], "release_id": release_id,
                   "scope_kind": scope_kind, "chapters": chapters,
                   "rolled_back_from": previous.get("release_id"),
                   "rolled_back_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        write_json_atomic(releases_root / CURRENT_NAME, payload)
    return PublishResult(release_id=release_id, release_dir=str(release_dir), switched=True,
                         previous_release=str(previous.get("release_id") or ""),
                         replaced_chapters=replaced,
                         untouched_chapters=() if scope_kind == "book" else untouched,
                         member_hashes=members,
                         notes=("回退完成：指针已切回。已发生的服务费用不回滚，"
                                "追加日志保留（方案 §16）",))


__all__ = ["CURRENT_NAME", "RELEASE_SCHEMA", "ChapterRef", "PublishResult", "member_hashes",
           "read_current", "build_release", "switch_current", "assert_release_validated",
           "rollback", "publish_candidate"]


def publish_candidate(*, store, context, scope: tuple[str, ...],
                      scope_kind: str = 'selection', before_switch=None) -> dict:
    """Publish verified HTML after the engine's whole-run quality and budget gates.

    Partial chapter selections have an independent stable namespace, so publishing one
    unit can never replace the complete chapter's current pointer. Same-run resume is
    idempotent only when the immutable release still matches every accepted member.
    """
    from ..contracts.artifact import ArtifactRef
    from ..core.digest import digest_of

    if not scope or len(scope) != len(set(scope)):
        raise ContractError('发布必须有无重复的已固定范围')
    state = store.node_states().get('delivery')
    if state is None or not state.finished_ok() or state.quality_state.value != 'accepted':
        raise ContractError('缺 accepted delivery 阶段，不能发布')
    artifacts = [ArtifactRef.from_dict(raw).verify() for raw in state.outputs]
    refs = [r for r in artifacts if r.kind == 'delivery.scope' and r.book_id == context.book_id
            and r.producer_stage == 'delivery' and r.path.resolve().is_relative_to(store.work.resolve())]
    if len(refs) != 1:
        raise ContractError('交付范围产物缺失/重复/身份不匹配')
    delivery_artifact = read_json_soft(refs[0].path) or {}
    delivery = delivery_artifact.get('payload') or {}
    if (delivery_artifact.get('_meta') or {}).get('revision') != context.revision:
        raise ContractError('交付产物的解析修订与发布不一致')
    acceptance = delivery.get('offline_acceptance')
    _require_offline_acceptance(acceptance)
    tree = store.work / 'legacy/render'
    members = delivery.get('members') or {}
    if not members:
        raise ContractError('交付资源清单为空')
    files = {}
    for member, expected in members.items():
        rel = _safe_relative(member)
        path = tree / rel
        if (not path.resolve().is_relative_to(tree.resolve()) or path.is_symlink()
                or not path.is_file() or digest_file(path) != expected):
            raise ContractError(f'验收后资源改变或缺失：{rel}')
        files[rel] = path.read_bytes()

    chapters = delivery.get('chapters') or []
    if not chapters or len(chapters) != len(set(chapters)):
        raise ContractError('交付章身份缺失/重复')
    rendered_units = []
    for chapter in chapters:
        _safe_relative(chapter)
        if f'{chapter}/index.html' not in members:
            raise ContractError(f'交付缺章页面：{chapter}')
        rendered = (read_json_soft(store.work / 'products/render' / f'{chapter}.json') or {}).get('payload') or {}
        rendered_units.extend(rendered.get('units') or [])
    if len(rendered_units) != len(set(rendered_units)) or set(rendered_units) != set(scope):
        raise ContractError('渲染产物没有逐项覆盖发布范围')
    if any(PurePosixPath(name).parts[0] not in chapters for name in members):
        raise ContractError('清单包含范围之外的资源')

    catalog = read_json_soft(Path(context.parse) / 'chapters.json') or {}
    all_chapter_units = {str(row.get('unit_id')) for row in catalog.get('lesson_units') or []
                         if str(row.get('chapter')) in set(chapters)}
    partial = set(scope) != all_chapter_units
    scope_digest = digest_of({'book_id': context.book_id, 'units': sorted(scope)}).split(':')[1][:24]
    releases = Path(context.releases_root)
    if partial:
        releases = releases / 'selections' / scope_digest
    release_id = _safe_relative(str(store.run_id))
    if '/' in release_id:
        raise ContractError('release_id 不能带目录')
    manifest = {'book_id': context.book_id, 'release_id': release_id, 'scope': list(scope),
                'scope_kind': scope_kind, 'chapters': chapters, 'members': members,
                'parse_revision': context.revision,
                'offline_acceptance': acceptance, 'delivery_artifact_digest': refs[0].content_digest,
                'namespace': 'selection' if partial else 'chapters'}
    target = releases / release_id
    if target.exists():
        previous_manifest, manifest_hash = _read_manifest(target)
        if any(previous_manifest.get(key) != expected for key, expected in manifest.items()
               if key != 'parse_revision'):
            raise ContractError('同run发布目录已存在且内容/范围不同，禁止覆盖')
        if 'parse_revision' in previous_manifest:
            if previous_manifest['parse_revision'] != context.revision:
                raise ContractError('同run发布目录的解析修订不一致，禁止覆盖')
        else:
            receipt = _legacy_publication_receipt(target, previous_manifest, manifest_hash, run_root=store.root)
            if any(ref.parse_revision != context.revision
                   for ref in _chapter_refs_from_receipt(previous_manifest, receipt, manifest_hash)):
                raise ContractError('同run历史发布目录的解析修订不一致，禁止覆盖')
        verified_hashes = {**members, 'manifest.json': manifest_hash}
        _verify_release_files(target, verified_hashes)
    else:
        target, verified_hashes = build_release(releases, release_id, manifest=manifest, files=files)
        manifest_hash = verified_hashes.get('manifest.json')
        if not manifest_hash or verified_hashes != {**members, 'manifest.json': manifest_hash}:
            raise ContractError('已构建的发布资源与验收清单不一致')
    assert_release_validated(target, checks_ok=True)
    if before_switch is not None:
        before_switch()
    published = _switch_current(releases, release_id, book_id=context.book_id,
        scope_kind=_pointer_scope(scope_kind), chapters=tuple(
            ChapterRef(chapter_key=chapter, release_id=release_id,
                page_digest=members[f'{chapter}/index.html'], parse_revision=context.revision)
            for chapter in chapters), verified_hashes=verified_hashes)
    return {**published.as_dict(), 'pointer': str(releases / CURRENT_NAME),
            'manifest': str(target / 'manifest.json'), 'namespace': manifest['namespace'],
            'scope_digest': scope_digest, 'scope': list(scope), 'formally_published': True}
