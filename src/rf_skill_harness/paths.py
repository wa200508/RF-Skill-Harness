"""Explicit new output locations; existing nonempty results are never overwritten."""
from pathlib import Path


def new_directory(path):
    root = Path(path).expanduser().resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise FileExistsError("Choose a new or empty output directory: " + str(root))
    root.mkdir(parents=True, exist_ok=True)
    return root
