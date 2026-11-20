"""Packaged prompt prose and identities of the messages actually transported."""
from dataclasses import dataclass
from importlib.resources import files
import json

from .digest import digest_of, digest_text


@dataclass(frozen=True)
class PromptAsset:
    name: str
    text: str
    sha256: str


PROMPT_ASSET_NAMES = (
    'content/common.md', 'content/author.md', 'content/repair.md',
    'media/code-common.md', 'media/code-static.md', 'media/code-animation.md',
    'media/review-common.md', 'media/review-static.md', 'media/review-animation.md',
    'media/review-balanced.md',
    # D16-3：作者媒体需求返修改为整份重写（与正文返修同一条路）。
    'media/author_media_repair.md',
    # 修改计划 D6：出图前技术异常的轻量修复，只带原程序与异常末尾。
    'media/code-fix.md',
)


def load_prompt_asset(name: str) -> PromptAsset:
    """Read one registered package resource, independent of cwd; never cache edits."""
    if name not in PROMPT_ASSET_NAMES:
        raise ValueError('unknown prompt asset: '+str(name))
    domain, filename = name.split('/')
    text = files('aiforstudy').joinpath('blocks', domain, 'prompts', filename).read_text(encoding='utf-8')
    return PromptAsset(name, text, digest_text(text))


def compose_prompt_assets(*names: str) -> PromptAsset:
    text = '\n\n'.join(load_prompt_asset(name).text for name in names)
    return PromptAsset('+'.join(names), text, digest_text(text))


def message_versions(messages: list[dict]) -> tuple[str, str]:
    """Hash final messages and the host rules embedded in their JSON payloads.

    Deriving at the transport boundary includes append-only repair instructions.
    Only hashes are returned; no message snapshots or credentials are persisted.
    """
    rule_keys = ('policy_digest', 'criteria_digest', 'quality_contract', 'review_items',
                 'acceptance_criteria', 'output_json_schema', 'output_contract',
                 'patch_contract', 'display_profiles')
    rules = []
    for message in messages:
        try:
            payload = json.loads(message.get('content', ''))
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            selected = {key: payload[key] for key in rule_keys if key in payload}
            context = payload.get('frozen_context')
            if isinstance(context, dict) and 'patch_contract' in context:
                selected['patch_contract'] = context['patch_contract']
            if selected:
                rules.append(selected)
    return digest_of(messages), digest_of(rules, check=False)
