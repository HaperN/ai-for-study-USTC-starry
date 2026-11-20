"""Portable media file boundaries shared by all consuming stages."""
from pathlib import Path
import stat

from .digest import digest_file
from ..errors import ContractError

INVALID_PATH_SEGMENTS = frozenset({'', '.', '..'})


def safe_relative_path(value):
    return (isinstance(value, str) and bool(value) and value == value.strip()
            and not any(c in value for c in ('\\', ':', '\x00'))
            and not value.startswith('/')
            and not any(part in INVALID_PATH_SEGMENTS for part in value.split('/')))


def local_resource_file(root, relative, *, nonempty=False):
    """Resolve one regular file, refusing links in every relative path component."""
    if not safe_relative_path(relative):
        raise ContractError('unsafe media resource path: ' + str(relative))
    base=Path(root).resolve()
    path=base
    for part in relative.split('/'):
        path=path/part
        if path.is_symlink():
            raise ContractError('media resources cannot be symlinks: ' + relative)
    try:
        info=path.stat()
        if (not path.resolve().is_relative_to(base) or not stat.S_ISREG(info.st_mode)
                or nonempty and info.st_size == 0):
            raise ContractError('media resource is empty or not a regular file: ' + relative)
    except OSError as exc:
        raise ContractError('media resource is missing: ' + relative) from exc
    return path


def verified_resource_file(root, resource):
    if not isinstance(resource, dict):
        raise ContractError('media resource descriptor must be an object')
    path=local_resource_file(root,resource.get('relative_path'))
    size=resource.get('bytes')
    if (digest_file(path) != resource.get('sha256')
            or 'bytes' in resource and (type(size) is not int or size != path.stat().st_size)):
        raise ContractError('invalid media resource bytes/hash: ' + resource['relative_path'])
    return path


# ── 修改计划 F1：运行目录被复制后，旧的绝对 source_dir 仍能挂载 ─────────────
#: The run directory of the run executing in this process (one run per process,
#: guarded by the book's run lease). Readers without an explicit run use it.
_CURRENT_RUN_ROOT = None


def set_current_run_root(path):
    global _CURRENT_RUN_ROOT
    _CURRENT_RUN_ROOT = Path(path) if path else None


def relocated_source_dir(source_dir, resources=(), *, run_root=None):
    """Resolve a recorded media directory against the current run.

    A relative value is relative to the run directory. An absolute value that no
    longer exists (the run was copied) is re-mounted from its part after
    ``/work/`` under the current run, and accepted only when every resource's
    sha256 matches. Boundary checks of the callers are unchanged.
    """
    raw = str(source_dir or '')
    root = Path(run_root) if run_root else _CURRENT_RUN_ROOT
    if not raw:
        return Path(raw)
    path = Path(raw)
    if not path.is_absolute():
        return root / path if root is not None else path
    if path.exists() or root is None:
        return path
    pieces = raw.split('/work/')
    for index in range(len(pieces) - 1, 0, -1):
        candidate = root / 'work' / '/work/'.join(pieces[index:])
        if not candidate.is_dir():
            continue
        try:
            for resource in resources or ():
                verified_resource_file(candidate, resource)
        except (ContractError, OSError):
            continue
        return candidate
    return path
