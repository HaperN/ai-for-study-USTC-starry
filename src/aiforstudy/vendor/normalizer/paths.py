"""Shared resolution of saved command paths, independent of the caller cwd."""
from pathlib import Path
import os


def io_path(path):
    """Windows extended path for I/O only; keep callers' semantic paths intact."""
    path = Path(path)
    if os.name != "nt":
        return path
    text = str(path.resolve())
    if text.startswith("\\\\?\\"):
        return Path(text)
    return Path("\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text)


