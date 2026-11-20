"""Single work-directory policy for Runner and direct stage invocation."""
from pathlib import Path


def store_root(workspace, store=None):
    root=getattr(store, 'work', None)
    return Path(root) if root is not None else Path(workspace.root)/'.cache'/'release'
