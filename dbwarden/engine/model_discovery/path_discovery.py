import os
import time
from pathlib import Path
from types import ModuleType
from typing import List, Optional


_auto_discover_cache: dict[str, tuple[float, list[str]]] = {}
_AUTO_DISCOVER_CACHE_TTL = 1.0


def load_model_from_path(filepath: str) -> Optional[ModuleType]:
    from dbwarden.plugin import HookRegistry, HookNotRegisteredError

    base_dir = Path.cwd().resolve()
    if HookRegistry.is_registered("load_model_module"):
        return HookRegistry.execute_single("load_model_module", Path(filepath), base_dir)

    import hashlib
    import importlib.util
    import sys

    try:
        module_name = "_dbwarden_loaded_model_" + hashlib.md5(str(filepath).encode()).hexdigest()[:12]
        spec = importlib.util.spec_from_file_location(module_name, str(filepath))
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    except FileNotFoundError:
        return None


def discover_models_in_directory(directory: str) -> List[str]:
    model_files = []
    directory_path = Path(directory)

    if not directory_path.exists() or not directory_path.is_dir():
        return []

    for filepath in directory_path.rglob("*.py"):
        if filepath.name.startswith("_"):
            continue
        model_files.append(str(filepath))

    return model_files


def _collect_model_files(model_paths: list[str]) -> list[str]:
    model_files: list[str] = []
    for model_path in model_paths:
        if not os.path.exists(model_path):
            continue
        if os.path.isdir(model_path):
            model_files.extend(discover_models_in_directory(model_path))
        else:
            model_files.append(model_path)
    return model_files


def _model_files_signature(model_files: list[str]) -> tuple[tuple[str, int, int], ...]:
    signature: list[tuple[str, int, int]] = []
    for model_file in sorted(set(model_files)):
        try:
            stat = Path(model_file).resolve().stat()
            signature.append((str(Path(model_file).resolve()), stat.st_mtime_ns, stat.st_size))
        except OSError:
            signature.append((str(Path(model_file).resolve()), -1, -1))
    return tuple(signature)


def _find_project_root(start: Path) -> Path:
    PROJECT_ROOT_MARKERS = {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        ".git",
        ".hg",
        ".svn",
    }
    for candidate in [start] + list(start.parents):
        if any((candidate / marker).exists() for marker in PROJECT_ROOT_MARKERS):
            return candidate
    return start


def auto_discover_model_paths() -> List[str]:
    cache_key = str(Path.cwd().resolve())
    cached = _auto_discover_cache.get(cache_key)
    if cached is not None and time.time() - cached[0] < _AUTO_DISCOVER_CACHE_TTL:
        return list(cached[1])

    model_paths = []
    current = Path.cwd().resolve()
    project_root = _find_project_root(current)

    IGNORED_DIRS = {
        ".venv",
        "node_modules",
        "__pycache__",
        ".git",
        ".hg",
        ".svn",
        "build",
        "dist",
        "egg-info",
        ".tox",
        ".nox",
        "venv",
        "ENV",
        ".egg",
        ".cache",
        "coverage",
        ".pytest_cache",
        "site-packages",
        "Lib",
        "Scripts",
        "bin",
        "include",
    }

    def find_model_dirs_in(directory: Path) -> List[str]:
        found = []
        try:
            if not directory.exists() or not directory.is_dir():
                return found

            for item in directory.iterdir():
                try:
                    if item.is_dir() and item.name not in IGNORED_DIRS:
                        for model_name in ["models", "model"]:
                            model_dir = item / model_name
                            if model_dir.exists() and model_dir.is_dir():
                                found.append(str(model_dir))
                except PermissionError:
                    continue
        except PermissionError:
            pass
        return found

    while True:
        for dirname in ["models", "model"]:
            model_dir = current / dirname
            if model_dir.exists() and model_dir.is_dir():
                if str(model_dir) not in model_paths:
                    model_paths.append(str(model_dir))

        for subdir in find_model_dirs_in(current):
            if subdir not in model_paths:
                model_paths.append(subdir)

        if current == project_root or current.parent == current:
            break
        current = current.parent

    _auto_discover_cache[cache_key] = (time.time(), model_paths)
    return model_paths
