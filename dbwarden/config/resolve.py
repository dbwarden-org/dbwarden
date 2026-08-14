from __future__ import annotations

import ast
import importlib
import os
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

from dbwarden.config.state import (
    DatabaseType,
    _IGNORE_DIRS,
    _ResolvedSource,
)
from dbwarden.config_registry import register_reset_hook, registered_entries, reset_registry
from dbwarden.config_schema import DatabaseEntry
from dbwarden.constants import TOML_FILE
from dbwarden.exceptions import ConfigurationError

_USE_DEV_DATABASE = False
_STRICT_TRANSLATION = False
_DISABLE_SKIP = False
_RESOLVED_SOURCE_CACHE: _ResolvedSource | None = None
_RESOLVED_CWD: str | None = None
_MULTI_DB_CONFIG_CACHE: "MultiDbConfig | None" = None
_MULTI_DB_CONFIG_CWD: str | None = None


def set_dev_mode(enabled: bool) -> None:
    global _USE_DEV_DATABASE
    _USE_DEV_DATABASE = enabled


def is_dev_mode() -> bool:
    return _USE_DEV_DATABASE


def set_disable_skip(enabled: bool) -> None:
    global _DISABLE_SKIP
    _DISABLE_SKIP = enabled


def is_skip_disabled() -> bool:
    return _DISABLE_SKIP


def set_strict_translation(enabled: bool) -> None:
    global _STRICT_TRANSLATION
    _STRICT_TRANSLATION = enabled


def is_strict_translation() -> bool:
    return _STRICT_TRANSLATION


def _clear_source_cache() -> None:
    global _RESOLVED_SOURCE_CACHE, _RESOLVED_CWD, _MULTI_DB_CONFIG_CACHE, _MULTI_DB_CONFIG_CWD
    _RESOLVED_SOURCE_CACHE = None
    _RESOLVED_CWD = None
    _MULTI_DB_CONFIG_CACHE = None
    _MULTI_DB_CONFIG_CWD = None


register_reset_hook(_clear_source_cache)


def get_toml_path() -> Path | None:
    current = Path.cwd().resolve()
    while True:
        toml_path = current / TOML_FILE
        if toml_path.exists():
            return toml_path
        if current.parent == current:
            break
        current = current.parent
    return None


def _infer_database_type(sqlalchemy_url: str) -> DatabaseType:
    url_lower = sqlalchemy_url.lower()
    if url_lower.startswith("sqlite"):
        return "sqlite"
    if url_lower.startswith("postgresql") or url_lower.startswith("postgres"):
        return "postgresql"
    if url_lower.startswith("mysql"):
        return "mysql"
    if url_lower.startswith("mariadb"):
        return "mariadb"
    if url_lower.startswith("clickhouse"):
        return "clickhouse"
    raise ConfigurationError(
        f"Cannot infer database type from URL '{sqlalchemy_url}'. "
        "Set 'database_type' explicitly in your config."
    )


def _normalized_url(url: str) -> str:
    try:
        parsed = make_url(url)
        return parsed.render_as_string(hide_password=False)
    except Exception as exc:
        raise ConfigurationError(
            f"Invalid database URL: '{url}'. "
            "Check the 'database_url_sync' or 'database_url_async' setting in your config."
        ) from exc


def _build_database_target_key(url: str, db_type: str, base_dir: Path) -> str:
    parsed = make_url(url)

    if db_type == "sqlite":
        db_path = parsed.database or ""
        if db_path == ":memory:" or not db_path:
            return f"sqlite::{db_path or ':memory:'}"

        normalized_path = Path(db_path)
        if not normalized_path.is_absolute():
            normalized_path = (base_dir / normalized_path).resolve()
        else:
            normalized_path = normalized_path.resolve()

        return f"sqlite::{normalized_path.as_posix()}"

    host = parsed.host or ""
    port = str(parsed.port or "")
    database = parsed.database or ""
    return f"{db_type}::{host}:{port}/{database}"


def _discover_dbwarden_files(root: Path) -> list[Path]:
    matches: list[Path] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [
            d for d in dirs
            if d not in _IGNORE_DIRS and not (Path(current) / d).is_symlink()
        ]
        if "dbwarden.py" in files:
            candidate = Path(current) / "dbwarden.py"
            if not candidate.is_symlink() and candidate.is_file():
                matches.append(candidate)
    return sorted(matches)


def _workspace_root() -> Path:
    current = Path.cwd().resolve()
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return Path.cwd().resolve()
        current = current.parent


def _file_has_database_config_call(path: Path) -> bool:
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except Exception:
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "database_config":
                return True
    declarative_names = {"DbwardenDatabase"}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "dbwarden":
            for alias in node.names:
                if alias.name == "DbwardenDatabase":
                    declarative_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "dbwarden":
                    declarative_names.add(f"{alias.asname or 'dbwarden'}.DbwardenDatabase")

    class_names: set[str] = set()
    class_bases: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        class_names.add(node.name)
        bases: list[str] = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
                bases.append(f"{base.value.id}.{base.attr}")
        class_bases[node.name] = bases

        attributes: set[str] = set()
        for statement in node.body:
            if isinstance(statement, ast.Assign):
                attributes.update(
                    target.id for target in statement.targets if isinstance(target, ast.Name)
                )
            elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                attributes.add(statement.target.id)
        if {"database_name", "database_url_sync"}.issubset(attributes) or {
            "database_name", "database_url_async"
        }.issubset(attributes):
            return True

    known = set(declarative_names)
    while True:
        derived = {
            name for name, bases in class_bases.items() if any(base in known for base in bases)
        }
        if not derived - known:
            break
        known.update(derived)
    if class_names & known:
        return True
    return False


def _full_scan_database_config_calls(root: Path) -> list[Path]:
    matches: list[Path] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [
            d for d in dirs
            if d not in _IGNORE_DIRS and not (Path(current) / d).is_symlink()
        ]
        for filename in files:
            if not filename.endswith(".py"):
                continue
            path = Path(current) / filename
            if not path.is_symlink() and path.is_file() and _file_has_database_config_call(path):
                matches.append(path)
    return sorted(matches)


_IMPORT_ROOTS: tuple[str, ...] = ("src", "")


def _resolve_import_root(path: Path, project_root: Path) -> tuple[str, str] | None:
    path = path.resolve()
    root = project_root.resolve()

    for rel_root in _IMPORT_ROOTS:
        candidate = root / rel_root if rel_root else root
        try:
            rel = path.relative_to(candidate)
        except ValueError:
            continue
        dotted = rel.with_suffix("").as_posix().replace("/", ".")
        return (str(candidate), dotted)

    return None


def _import_as_package_module(module_name: str, import_root: str) -> Path:
    root = Path(import_root).resolve()
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    if module_name in sys.modules:
        del sys.modules[module_name]

    importlib.import_module(module_name)
    return root


def _is_literal_node(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_literal_node(el) for el in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            (k is None or _is_literal_node(k)) and _is_literal_node(v)
            for k, v in zip(node.keys, node.values)
        )
    return False


def _extract_variable_value_expressions(path: Path) -> list[dict[str, str]]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception:
        return []

    call_metadata: list[dict[str, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Name) or call.func.id != "database_config":
            continue

        variable_kwargs: dict[str, str] = {}
        for kw in call.keywords:
            if kw.arg is None:
                continue
            if _is_literal_node(kw.value):
                continue
            expr = ast.get_source_segment(source, kw.value)
            if expr is None:
                expr = ast.unparse(kw.value)
            variable_kwargs[kw.arg] = expr.strip()
        call_metadata.append(variable_kwargs)
    return call_metadata


def _resolve_source() -> _ResolvedSource:
    global _RESOLVED_SOURCE_CACHE, _RESOLVED_CWD
    current_cwd = str(Path.cwd().resolve())
    if _RESOLVED_SOURCE_CACHE is not None and _RESOLVED_CWD == current_cwd:
        return _RESOLVED_SOURCE_CACHE
    _RESOLVED_CWD = current_cwd

    root = _workspace_root()

    dbwarden_files = _discover_dbwarden_files(root)
    if len(dbwarden_files) > 1:
        rel_paths = [str(p.relative_to(root)) for p in dbwarden_files]
        paths = "\n".join(rel_paths)
        raise ConfigurationError(
            "Multiple dbwarden.py files found. Keep exactly one.\n" + paths
        )
    if len(dbwarden_files) == 1:
        _RESOLVED_SOURCE_CACHE = _ResolvedSource("file", str(dbwarden_files[0]), classification="isolated")
        return _RESOLVED_SOURCE_CACHE

    module_name = os.getenv("DBWARDEN_CONFIG_MODULE")
    if module_name:
        _RESOLVED_SOURCE_CACHE = _ResolvedSource("module", module_name)
        return _RESOLVED_SOURCE_CACHE

    callsite_files = _full_scan_database_config_calls(root)
    if len(callsite_files) > 1:
        rel_paths = [str(p.relative_to(root)) for p in callsite_files]
        paths = "\n".join(rel_paths)
        raise ConfigurationError(
            "Multiple database_config(...) call sites found. "
            "Keep exactly one source, or set DBWARDEN_CONFIG_MODULE to choose explicitly.\n"
            + paths
        )
    if len(callsite_files) == 1:
        discovered = callsite_files[0]
        if discovered.parent == root:
            _RESOLVED_SOURCE_CACHE = _ResolvedSource(
                "file", str(discovered), classification="isolated",
            )
        else:
            import_info = _resolve_import_root(discovered, root)
            if import_info is not None:
                import_root, module_name = import_info
                _RESOLVED_SOURCE_CACHE = _ResolvedSource(
                    "file", str(discovered),
                    classification="in_package",
                    import_root=import_root,
                    module_name=module_name,
                )
            else:
                _RESOLVED_SOURCE_CACHE = _ResolvedSource(
                    "file", str(discovered), classification="isolated",
                )
        return _RESOLVED_SOURCE_CACHE

    raise ConfigurationError(
        "No configuration found. Add database_config(...) call(s), create dbwarden.py with dbwarden init, "
        "or set DBWARDEN_CONFIG_MODULE."
    )


def get_settings_source_file() -> Path:
    source = _resolve_source()
    if source.kind != "file":
        raise ConfigurationError(
            "Settings mutator commands require a file-based config source. "
            "Use a local dbwarden.py or full-scan-resolved file source."
        )
    return Path(source.value)


def _import_source(source: _ResolvedSource) -> Path:
    if source.kind == "module":
        if source.value in sys.modules:
            del sys.modules[source.value]
        importlib.import_module(source.value)
        return Path.cwd().resolve()

    path = Path(source.value)

    if source.classification == "in_package":
        return _import_as_package_module(source.module_name, source.import_root)

    from dbwarden.plugin import HookRegistry

    base_dir = path.parent.resolve()
    if HookRegistry.is_registered("load_config_module"):
        HookRegistry.execute_single("load_config_module", path, base_dir)
        return base_dir

    _unique_name = f"_dbwarden_config_{path.stem}"
    spec = importlib.util.spec_from_file_location(_unique_name, path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[_unique_name] = mod
        spec.loader.exec_module(mod)
    return base_dir
