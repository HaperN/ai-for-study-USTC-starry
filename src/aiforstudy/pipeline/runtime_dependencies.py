"""Declared runtime components; no source introspection or text slicing.

Whole components own cache identity. Reader CSS belongs only to presentation;
source context, mathematical interpretation and learner profiles belong to content.
"""
from .graph import PREPARATION_STAGES

# The public provider descriptor cannot substitute for implementation bytes.
# Include the routing factory and SDK bridge as well as the HTTP worker; these
# shared components can change both author output and interpretation of review.
TRANSPORT_COMPONENTS = ('providers/transport.py','providers/http_worker.py',
    'providers/factory.py','providers/routing.py','providers/local_compatible.py',
    'providers/dsh_account.py','providers/dsh_bridge.cjs')

COMMON = ('contracts','core/digest.py','core/jsonio.py','core/document_fields.py','pipeline/services.py',
          'core/prompt_assets.py','core/source.py','core/process_io.py','pipeline/execution_control.py',
          'pipeline/stage_io.py','pipeline/work_paths.py','pipeline/graph.py',
          'pipeline/gateway_client.py','providers/gateway.py','providers/llm.py','providers/review_failures.py',
          'providers/response.py','providers/response_evidence.py') + TRANSPORT_COMPONENTS
PREPARATION = ('vendor','pipeline/preparation.py','pipeline/preparation_checks.py','pipeline/snapshot.py',
               'core/identity.py','core/source_materials.py','core/source_boundaries.py',
               'blocks/parse','blocks/concepts','providers/mineru.py','providers/http_exchange.py')
MATH = ('blocks/math',)
AUDIT = ('blocks/audit/__init__.py','blocks/audit/contracts.py','blocks/audit/evidence.py',
         'blocks/audit/loop.py','pipeline/diagnostics.py','pipeline/repair.py')
CONTENT = ('core/source_boundaries.py','core/video_evidence.py','core/course_library.py','blocks/content',
           'blocks/media/author_requirements.py','blocks/media/author_diagnostics.py','blocks/media/media_requirements.py',
           'blocks/media/intent_review.py',
           'pipeline/content_stages.py','pipeline/content_revalidation.py','resources/teaching_profiles.json') + MATH + AUDIT
MEDIA = ('blocks/media','core/resource_files.py','pipeline/media_stages.py','pipeline/media.py','pipeline/resource_binding.py',
         'pipeline/media_review_recovery.py','blocks/figures','blocks/visual','providers/capabilities.py') + MATH + AUDIT
READER = ('blocks/render','core/source_materials.py','core/exam_analysis.py')
LIBRARY_ADAPTER_COMPONENTS = ('blocks/figures/__init__.py','blocks/figures/library.py',
    'blocks/figures/spec.py','blocks/figures/spec_extra.py','blocks/figures/verify_svg.py','blocks/figures/backends')
INTERACTION_ADAPTER_COMPONENTS = ('blocks/model/__init__.py','blocks/model/model_generate.py',
    'blocks/model/model_prompt.py','blocks/model/model_validate.py','lib/curriculum.py','lib/files.py')
STAGE_PACKAGE_PATHS = {
    **{s:PREPARATION for s in PREPARATION_STAGES},
    'source_format':('pipeline/source_format_stage.py','blocks/content/material_formatting.py',
                     'blocks/content/generation_corrections.py','blocks/content/content_contracts.py',
                     'blocks/content/content_policy.py','core/json_syntax.py'),
    'lesson':CONTENT,'text_audit':CONTENT,'media_plan':CONTENT+MEDIA,
    'figures':MEDIA,'audit':MEDIA+CONTENT,'interaction':MEDIA+('blocks/interaction',),
    'render':CONTENT+MEDIA+READER+('blocks/interaction','checks/browser_acceptance.cjs','checks/offline.py'),
    # Delivery checks frozen reader/source identities and member bytes instead
    # of trusting a render receipt, so its full consumer closure also matters.
    'delivery':('checks','delivery','pipeline/graph.py','pipeline/delivery_stage.py',
                'blocks/interaction')+CONTENT+MEDIA+READER,
}
STAGE_ADAPTER_PATHS = {
    **{s:('blocks/parse','lib/files.py','lib/text.py','lib/book.py','lib/non_teaching.py') for s in PREPARATION_STAGES},
    'source_format':(),
    'lesson':(),'text_audit':(),'media_plan':(),
    'figures':LIBRARY_ADAPTER_COMPONENTS+('blocks/render/build.py','lib/files.py','requirements-manim.lock'),
    'audit':(),
    'interaction':(),
    'render':('blocks/render/build.py','blocks/render/assets','blocks/render/vendor','blocks/render/templates','lib/files.py','lib/refs.py'),
    'delivery':('blocks/render/assets',),
}
# These stages make or validate a capability promise. The planner is consumed
# before interaction generation too, including the author's candidate identity.
INTERACTION_CAPABILITY_CONSUMERS = ('lesson','media_plan','figures','audit','interaction','render')
for _stage in INTERACTION_CAPABILITY_CONSUMERS:
    STAGE_PACKAGE_PATHS[_stage] += ('blocks/interaction/spec.py','pipeline/interaction_runtime.py')
    STAGE_ADAPTER_PATHS[_stage] = tuple(dict.fromkeys(
        STAGE_ADAPTER_PATHS[_stage] + INTERACTION_ADAPTER_COMPONENTS))
STAGE_ADAPTER_PATHS['delivery'] = tuple(dict.fromkeys(
    STAGE_ADAPTER_PATHS['delivery'] + STAGE_ADAPTER_PATHS['figures']
    + STAGE_ADAPTER_PATHS['render'] + INTERACTION_ADAPTER_COMPONENTS))
# Copy the final render closure after capability helpers have been appended.
STAGE_PACKAGE_PATHS['delivery'] = tuple(dict.fromkeys(
    STAGE_PACKAGE_PATHS['delivery'] + STAGE_PACKAGE_PATHS['render']))
STAGE_ROLES = {
    'source_format':('lesson',),
    'concepts':('concepts',),'lesson':('lesson','source_inventory'),
    'text_audit':('text_judge',),'media_plan':('figures','judge'),'figures':('figures','judge'),
    'audit':('judge',),'interaction':('interaction',),'toc':('toc_review',),'body':('source_repair',),
}
# Explicit resource components keep reviewer changes from regenerating media bytes.
MEDIA_AUTHORITY_COMPONENTS = (
    'blocks/media/math_context.py','blocks/math','blocks/content/content_contracts.py',
    'blocks/content/review_outcome.py',
    'blocks/content/content_approval.py','blocks/content/source_context.py','resources/teaching_profiles.json',
)
STATIC_GENERATION_COMPONENTS = (
    'blocks/media/svg_safety.py','core/resource_files.py',
    'blocks/media/execution.py','blocks/media/static_common.py',
    'blocks/media/plot.py','blocks/media/geometry.py','blocks/media/diagram.py',
    'blocks/media/media_patches.py',
) + MEDIA_AUTHORITY_COMPONENTS
CODE_GENERATION_COMPONENTS = (
    'core/prompt_assets.py','core/resource_files.py','blocks/media/svg_safety.py',
    'blocks/content/reader_fields.py',
    'blocks/media/code_contracts.py','blocks/media/code_generation.py',
    'blocks/media/static_example.py','blocks/media/animation_example.py',
    'blocks/media/code_runtime.py','blocks/media/code_execution.py',
    'blocks/media/media_requirements.py','blocks/media/presentation_policy.py',
    'blocks/visual/worker.py','blocks/visual/manim_adapter.py','providers/capabilities.py',
) + MEDIA_AUTHORITY_COMPONENTS
CODE_NARRATION_COMPONENTS = ('blocks/media/narration.py','blocks/media/narration_providers.py',
    'blocks/media/local_narration.py','blocks/media/local_speech_worker.py')
# Explicit worker assets used by code/probe mode. Manim additionally calls the
# shared font checks in scene_builder; render_fixture is never a code dependency.
CODE_WORKER_COMPONENTS = (
    'run_scene.py','policy.py','sandbox.py','bwrap.py','probe_env.py',
    'code_renderer.py','aifs_media_runtime.py',
)
STATIC_REVIEW_COMPONENTS = (
    'blocks/media/prompts/review-balanced.md',
    'core/prompt_assets.py','core/resource_files.py','blocks/media/svg_safety.py','blocks/render/display_policy.py',
    'blocks/media/validation.py','blocks/media/display_probe.cjs','blocks/media/evidence_digest.py','blocks/media/review_policy.py',
    'blocks/media/media_manifest.py','blocks/media/media_requirements.py','blocks/media/criteria.py',
    'blocks/media/presentation_policy.py','blocks/content/reader_document.py','blocks/content/reader_fields.py',
)
CODE_REVIEW_COMPONENTS = STATIC_REVIEW_COMPONENTS + ('checks/offline.py','providers/capabilities.py')

CONTENT_AUTHOR_ASSETS = ('blocks/content/prompts/common.md','blocks/content/prompts/author.md')
CONTENT_REPAIR_ASSETS = ('blocks/content/prompts/common.md','blocks/content/prompts/repair.md')


def media_prompt_components(kind: str, phase: str) -> tuple[str, ...]:
    """Only resources actually composed into this request's role/type prompt."""
    if phase not in ('code','review'): raise ValueError('unknown media prompt phase: '+phase)
    appendix='animation' if kind=='animation' else 'static'
    return (f'blocks/media/prompts/{phase}-common.md',f'blocks/media/prompts/{phase}-{appendix}.md')
