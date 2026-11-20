import importlib
from types import SimpleNamespace
import json
import pytest
from aiforstudy.errors import ContractError
from support.content_case import teaching_case

def candidate(): return teaching_case()[0]


def scripted_client(author=None):
    from support.runner_v5 import TeachingTransport
    transport=TeachingTransport()
    class Client:
        calls=[]
        def chat_json(self,messages,**kwargs):
            payload=json.loads(messages[1]['content'])
            self.calls.append(kwargs.get('tag',''))
            if payload.get('role_task')=='author' and author is not None:
                value=author()
            else:
                value=json.loads(transport._answer(messages[0]['content'],messages[1]['content']))
            return {'data':value}
    return Client()


def mod():
    assert importlib.util.find_spec(
        "aiforstudy.pipeline.content_stages"
    ), "production content stages missing"
    return importlib.import_module("aiforstudy.pipeline.content_stages")


def context(tmp_path, stage="lesson"):
    from aiforstudy.pipeline import stage_io as T

    parse = tmp_path / "parse"
    parse.mkdir(exist_ok=True)
    (parse / "source.md").write_text(teaching_case()[1]["source_text"], encoding="utf-8")
    (parse / "chapters.json").write_text(
        json.dumps(
            {
                "book": {"title": "记号"},
                "lesson_units": [
                    {"unit_id": "U", "title": "约定", "original_text": "source.md"}
                ],
            }
        ),
        encoding="utf-8",
    )
    return SimpleNamespace(
        node=SimpleNamespace(scope_key="U", stage=stage, node_id=stage + ":U"),
        context=SimpleNamespace(parse=parse, book_id="B", revision="r"),
        workspace=SimpleNamespace(root=tmp_path),
        services=SimpleNamespace(
            store=SimpleNamespace(work=tmp_path / "work", run_id="run"),
            cancel=SimpleNamespace(checkpoint=lambda: None),
        ),
        repair_batch=None,
        log=lambda *a, **k: None,
    )


def test_stage_generates_immutable_content_without_legacy_attachments(
    tmp_path, monkeypatch
):
    m = mod()
    from aiforstudy.pipeline import stage_io as T

    client=scripted_client()
    monkeypatch.setattr(T, "_gateway_client", lambda *a, **k: (client, "offline"))
    result = m.lesson_stage(context(tmp_path))
    assert result.execution_state.value == "succeeded"
    c, a = m.load_content(context(tmp_path))
    assert a == {}
    assert len(client.calls) == 2 and c["body_markdown"] == candidate()["body_markdown"]
    assert (tmp_path / "work/products/content/U/revisions/000001.json").is_file()
    assert len(result.outputs) >= 2
    assert not (tmp_path / "work/legacy/lesson").exists()
    with pytest.raises(ContractError):
        m.load_content(context(tmp_path), approved=True)


def test_model_media_fields_are_rejected_not_silently_stripped(tmp_path, monkeypatch):
    m = mod()
    from aiforstudy.pipeline import stage_io as T

    c = candidate()
    c["figure_requests"] = [{"kind": "svg"}]
    monkeypatch.setattr(
        T,
        "_gateway_client",
        lambda *a, **k: (
            scripted_client(lambda:c),
            "offline",
        ),
    )
    result = m.lesson_stage(context(tmp_path))
    assert result.quality_state.value != "accepted"
    assert result.execution_state.value == "blocked", result.as_dict()
    assert result.outputs, "rejected provider response must remain inspectable"
    blobs=[json.loads(o.path.read_text()) for o in result.outputs]
    # 被拒的原始答复必须**保留可查**（媒体字段不被静默剥离）。
    assert any("figure_requests" in json.dumps(b, ensure_ascii=False) for b in blobs), blobs[:2]


def test_full_concept_projection_keeps_cross_unit_definitions_and_source_parts(
    tmp_path,
    monkeypatch,
):
    m = mod()
    ctx = context(tmp_path)
    concepts = {
        "concepts": [
            {
                "id": f"C{i:04d}",
                "name": "概念" + str(i),
                "type": "definition",
                "formal": "本节条件" if i == 0 else "x" * 800,
                "source_parts": [{"text": "y" * 800}],
                "occurrences": [
                    {"unit_id": "U" if i == 0 else "OLD", "role": "introduced"},
                    {"unit_id": "NEXT", "role": "reused"},
                ],
            }
            for i in range(600)
        ]
    }
    (ctx.context.parse / "concepts.json").write_text(
        json.dumps(concepts), encoding="utf-8"
    )
    frozen = m._frozen_context(ctx)
    assert len(frozen["concept_index"]["concepts"]) == 600
    assert len(frozen["reference_catalog"]["entries"]) >= 600
    assert {row["id"] for row in frozen["concept_index"]["concepts"]} == {f"C{i:04d}" for i in range(600)}
    current=next(row for row in frozen["concept_index"]["concepts"] if row["id"]=="C0000")
    assert current["formal"] == "本节条件" and current["source_parts"] == [{"text":"y" * 800}]
    reused = [row for row in frozen["concept_index"]["concepts"] if row["id"] != "C0000"]
    assert all(row["formal"] == "x" * 800 and row["source_parts"] == [{"text":"y" * 800}]
               for row in reused)
    assert not ({row["concept_id"] for row in frozen["source_obligations"]} &
                {row["id"] for row in reused})
    # Complete source evidence may exceed request capacity. Reject explicitly;
    # dropping prerequisite definitions would manufacture unusable references.
    # 上限本身已提到 8MB（见 content_policy.MAX_REQUEST_BYTES 的说明），所以这里把上限
    # 临时调小来验"超限即拒"，这条不变量就与具体容量数值解耦。
    from aiforstudy.blocks.content import generation_corrections
    monkeypatch.setattr(generation_corrections, 'MAX_REQUEST_BYTES', 4096)
    from aiforstudy.blocks.content.generation import generate_content
    from aiforstudy.blocks.content.content_policy import load_content_policy
    with pytest.raises(ContractError, match='request exceeds'):
        generate_content(frozen, load_content_policy(frozen['learner_profile']),
                         SimpleNamespace(chat_json=lambda *a,**k:pytest.fail('capacity must reject before API')))
    assert frozen["source_inputs"]["concepts.json"]


def test_explicit_import_is_candidate_and_calls_no_author(tmp_path, monkeypatch):
    m = mod()
    ctx = context(tmp_path)
    from aiforstudy.pipeline import stage_io as T

    path = tmp_path / "work/products/imported_content/U.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"payload": {"content": candidate()}}))
    client=scripted_client(lambda: (_ for _ in ()).throw(AssertionError('import must not generate')))
    monkeypatch.setattr(T,'_gateway_client',lambda *a,**k:(client,'offline-inventory'))
    result = m.lesson_stage(ctx)
    assert result.execution_state.value == "succeeded", result.error
    assert result.quality_state.value == "needs_review"
    assert m.load_content(ctx)[0]["body_markdown"] == candidate()["body_markdown"]


def test_host_policy_recheck_reuses_candidate_with_zero_author_calls(
    tmp_path, monkeypatch
):
    m = mod()
    ctx = context(tmp_path)
    from aiforstudy.pipeline import stage_io as T

    monkeypatch.setattr(
        T,
        "_gateway_client",
        lambda *a, **k: (
            scripted_client(),
            "offline",
        ),
    )
    assert m.lesson_stage(ctx).execution_state.value == "succeeded"
    revision = tmp_path / "work/products/content/U/revisions/000001.json"
    before = revision.read_bytes()
    monkeypatch.setattr(
        T,
        "_gateway_client",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("policy recheck must call no author")
        ),
    )
    ctx.recheck_only = True
    ctx.recheck_reason = "policy digest changed with identical generation inputs"
    result = m.lesson_stage(ctx)
    assert result.execution_state.value == "succeeded", result.error
    assert revision.read_bytes() == before
    assert not (revision.parent / "000002.json").exists()
    payload = json.loads((tmp_path / "work/products/lesson/U.json").read_text())[
        "payload"
    ]
    assert payload["model_calls"] == 0 and payload["recheck_only"] is True
    (ctx.context.parse / "source.md").write_text("新教材条件")
    assert m.lesson_stage(ctx).execution_state.value == "blocked"
