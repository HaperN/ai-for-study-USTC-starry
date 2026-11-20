"""One reversible review policy, bound to the run rather than changing mid-run."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path

from ..errors import ConfigError

_mode = ContextVar('aiforstudy_review_mode', default=None)


def configured_review_mode(workspace=None):
    if workspace is None:
        from .workspace import find_workspace
        try:
            workspace = find_workspace()
        except ConfigError:
            return 'full'
    root = Path(workspace if isinstance(workspace, (str, Path)) else workspace.root)
    path = root / 'config/review_mode.json'
    mode = json.loads(path.read_text(encoding='utf-8')).get('mode', 'full') if path.is_file() else 'full'
    if mode not in ('json_only', 'balanced', 'full'):
        raise ConfigError('review_mode.mode must be json_only, balanced or full')
    return mode


def current_review_mode():
    return _mode.get() or configured_review_mode()


def reviews_frozen():
    """Legacy machine gates remain frozen in both limited modes."""
    return current_review_mode() in ('json_only', 'balanced')


def substantive_reviews_enabled():
    return current_review_mode() == 'balanced'


@contextmanager
def review_mode(mode):
    if mode not in ('json_only', 'balanced', 'full'):
        raise ConfigError('review mode must be json_only, balanced or full')
    token = _mode.set(mode)
    try:
        yield
    finally:
        _mode.reset(token)


def frozen_review():
    if substantive_reviews_enabled():
        return {'status': 'unverified', 'reasons': ['本项旧审核已冻结；正文、成品媒体和页面实质审核另行记录。'],
                'review_mode': 'balanced', 'review_status': 'not_run'}
    return {'status': 'unverified', 'reasons': ['审核已冻结（json_only），仅检查 JSON 语法。'],
            'review_mode': 'json_only', 'review_status': 'not_run'}
