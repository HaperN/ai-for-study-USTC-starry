"""The sole production registry; historical runs are read-only."""
from .graph import GRAPH_VERSION,CANONICAL_STAGES
from .services import StageRegistry
from ..errors import ContractError

def release_registry(*, graph_version=GRAPH_VERSION):
    if graph_version != GRAPH_VERSION:
        raise ContractError("旧图只读；新运行只支持 graph " + GRAPH_VERSION)
    from .preparation import preparation_stages
    from .preparation_checks import body_stage
    from .content_stages import lesson_stage,text_audit_stage
    from .media_stages import media_plan_stage,figures_stage,audit_stage,interaction_stage,render_stage
    from .delivery_stage import delivery_stage
    from .source_format_stage import source_format_stage
    stages = {"body":body_stage, **preparation_stages(),"lesson":lesson_stage,"text_audit":text_audit_stage,
              "media_plan":media_plan_stage,"figures":figures_stage,"audit":audit_stage,
              "interaction":interaction_stage,"render":render_stage,"delivery":delivery_stage,
              "source_format":source_format_stage}
    if set(stages) != {stage.name for stage in CANONICAL_STAGES}:
        raise ContractError("production registry differs from canonical graph")
    registry=StageRegistry(stages)
    registry.graph_version=GRAPH_VERSION
    return registry
