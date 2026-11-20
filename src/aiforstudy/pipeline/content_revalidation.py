"""Keep immutable repaired candidates when the actual author request is unchanged.

This grants only a fresh validation attempt, never an inherited audit approval.
The original generation journal and all repair counters remain untouched.
"""
import json
from pathlib import Path

from ..contracts.artifact import ArtifactRef
from ..core.jsonio import read_json
from ..errors import ContractError
from ..blocks.content.content_contracts import content_digest
from ..blocks.content.generation_corrections import MAX_SYNTAX_CORRECTIONS


def request_matches_published_candidate(ctx, context, policy):
    store = ctx.services.store
    if not callable(getattr(store, 'load_state', None)):
        return False
    state = store.load_state()
    previous = (state.get('nodes') or {}).get(ctx.node.node_id) or {}
    if previous.get('execution_state') not in ('succeeded', 'cached'):
        return False
    repair = (state.get('repair_states') or {}).get(ctx.node.scope_key) or {}
    if repair.get('active_batch_id'):
        return False
    base = Path(store.work) / 'products/content' / ctx.node.scope_key
    journal_path = base / 'generation-journal.json'
    if not journal_path.is_file():
        return False  # Imported/legacy candidates need their existing explicit path.
    outputs = previous.get('outputs') or []
    required = {'lesson.content', 'lesson.content_head', 'lesson.author_media', 'lesson.correction_journal'}
    if not required <= {row.get('kind') for row in outputs}:
        return False
    # Invalid immutable evidence must block; do not replace it by replaying an
    # earlier model response and silently losing authorized downstream repairs.
    for row in outputs:
        ArtifactRef.from_dict(row).verify()
    from ..blocks.content.prompt import build_content_messages
    from ..blocks.interaction.spec import source_interaction_capabilities
    from .interaction_runtime import interaction_runtime_inputs
    caps = source_interaction_capabilities(context, **interaction_runtime_inputs(ctx))
    prompt_context = {**context, 'interaction_capabilities': caps}
    input_path = journal_path.with_suffix('.input.json')
    frozen_input = read_json(input_path) if input_path.is_file() else {}
    for key in ('available_theorem_names', 'available_theorem_explanations'):
        if frozen_input.get(key) is not None:
            prompt_context[key] = frozen_input[key]
    messages = build_content_messages('author', prompt_context, policy)
    journal = read_json(journal_path)
    # The author still has no inner field/content corrections. New journals may
    # contain the bounded syntax-only repairs before their accepted candidate.
    if (journal.get('schema_version') != 'GenerationCorrectionsV1'
            or journal.get('max_corrections') != 0):
        return False
    identity = {'messages': messages, 'tag': 'content-author:' + ctx.node.scope_key,
                'max_corrections': 0}
    syntax_limit = journal.get('max_syntax_corrections', 0)
    if 'max_syntax_corrections' in journal:
        if type(syntax_limit) is not int or syntax_limit != MAX_SYNTAX_CORRECTIONS:
            return False
        identity['max_syntax_corrections'] = syntax_limit
    expected = content_digest(identity)
    if journal.get('input_hash') != expected:
        return False
    attempts = journal.get('attempts') or []
    if not 1 <= len(attempts) <= syntax_limit + 1 or attempts[-1].get('status') != 'accepted':
        return False
    for index, attempt in enumerate(attempts):
        if (attempt.get('index') != index or 'response' not in attempt
                or attempt.get('response_hash') != content_digest({'response': attempt['response']})):
            raise ContractError('published candidate generation evidence is invalid')
        if index < len(attempts) - 1:
            if not isinstance(attempt['response'], str):
                return False
            try:
                json.loads(attempt['response'])
            except json.JSONDecodeError:
                pass
            else:
                return False  # A field/content correction cannot gain this path.
    return True


def validate_published_candidate(content, author_media, context):
    """Run today's complete author contract without changing the stored value."""
    from ..blocks.content.source_context import SourceContext
    from ..blocks.content.generation import _validate_author_output
    checked = _validate_author_output({'schema_version': 'ContentAuthoringV1',
        'content': content, 'media_requirements': author_media['requirements']},
        SourceContext.model_validate(context))
    if (checked['content'] != content
            or checked['media_requirements'] != author_media['requirements']):
        raise ContractError('candidate revalidation requires an explicit new revision; no silent migration')
