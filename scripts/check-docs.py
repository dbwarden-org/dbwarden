"""Check documentation against local commands, imports, and navigation."""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import re
import shlex
import sys
import textwrap
import warnings
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    import tomllib
except ImportError:
    import tomli as tomllib
from typer.main import get_command

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def documents():
    return sorted({ROOT / "README.md", *ROOT.glob("*.md"), *ROOT.glob("docs/**/*.md"), *ROOT.glob("examples/**/*.md")})


def blocks(text):
    return re.finditer(r"^```(?:python|py)\s*\n(.*?)^```", text, re.MULTILINE | re.DOTALL)


def module_name(path):
    return ".".join(path.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__")


def exported_modules():
    modules = {}
    for path in sorted((ROOT / "dbwarden").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets) for n in tree.body):
            module = importlib.import_module(module_name(path))
            modules[module.__name__] = module
    return modules


def command_inventory():
    from dbwarden.cli.main import app

    result = {}

    def visit(command, prefix):
        result[prefix] = command
        for name, child in getattr(command, "commands", {}).items():
            visit(child, f"{prefix} {name}")

    visit(get_command(app), "dbwarden")
    return result


def slug(text):
    text = re.sub(r"<[^>]*>", "", text).replace("&amp;", "")
    return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", text.lower())).strip("-")


def api_reference():
    lines = ["# Python API inventory", "",
             "This inventory covers declared package exports, configuration, and runtime handles.",
             "Signatures and declared fields come from the current source. Internal engine exports",
             "are contributor interfaces, not a compatibility promise. Use the",
             "[plugin development guide](../plugins/developing/overview.md) when choosing extension points.", "",
             "For usage, see [configuration](configuration-api.md), [models](../models.md),",
             "[plugin hooks](../plugins/reference/hook-catalog.md), and the backend guides.", "",
             "Regenerate this inventory with `python scripts/check-docs.py --api-reference`.", ""]
    modules = exported_modules()
    for name in ("dbwarden.config_registry", "dbwarden.config_schema", "dbwarden.db_handle", "dbwarden.models", "dbwarden.schema.table_meta"):
        modules[name] = importlib.import_module(name)
    seen = set()
    for name, module in sorted(modules.items()):
        names = getattr(module, "__all__", None)
        if names is None:
            names = [key for key, value in vars(module).items()
                     if not key.startswith("_") and getattr(value, "__module__", None) == name]
        lines += [f"## `{name}`", ""]
        for symbol in names:
            if symbol.startswith("_"):
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                value = getattr(module, symbol)
            lines += [f"### `{symbol}`", ""]
            canonical = getattr(value, "__module__", name) + "." + getattr(value, "__qualname__", symbol)
            if canonical in seen:
                lines += [f"Re-export of `{canonical}`.", ""]
                continue
            seen.add(canonical)
            try:
                signature = str(inspect.signature(value))
            except (ValueError, TypeError):
                signature = ""
            signature = re.sub(r" at 0x[0-9A-Fa-f]+", "", signature)
            lines += ["```text", symbol + signature, "```", ""]
            if inspect.isclass(value):
                fields = {}
                for cls in reversed(value.__mro__):
                    fields.update(getattr(cls, "__annotations__", {}))
                if fields:
                    lines += ["Declared fields: " + ", ".join(f"`{key}`" for key in fields if not key.startswith("_")) + ".", ""]
                methods = []
                for key in vars(value):
                    if key.startswith("_"):
                        continue
                    member = getattr(value, key)
                    if isinstance(member, property):
                        methods.append(key + " (property)")
                    elif callable(member):
                        try:
                            methods.append(key + str(inspect.signature(member)))
                        except (ValueError, TypeError):
                            pass
                if methods:
                    lines += ["Methods declared on this class:", "", "```text", *methods, "```", ""]
    return "\n".join(lines)


def cli_reference():
    lines = ["# CLI option inventory", "",
             "This page lists every built-in command, argument, and option from the current CLI.",
             "For workflows and exit codes, see the [CLI guide](../cli-reference.md).",
             "Plugin commands are added at startup by installed, approved plugins; inspect",
             "their help separately. Global options precede the command name.", "",
             "Regenerate with `python scripts/check-docs.py --cli-reference`.", ""]
    for name, command in command_inventory().items():
        lines += [f"## `{name}`", "", inspect.cleandoc(command.help or "Command group."), "",
                  "| Argument or option | Type | Default | Description |", "|---|---|---|---|"]
        for param in command.params:
            options = [*getattr(param, "opts", []), *getattr(param, "secondary_opts", [])]
            label = ", ".join(options) if options else param.name
            default = "required" if param.required else repr(param.default)
            help_text = " ".join((getattr(param, "help", None) or "").split())
            kind = "/".join(map(str, param.type.choices)) if hasattr(param.type, "choices") else param.type.name
            if param.multiple:
                kind += " (repeatable)"
            row = [label, kind, default, help_text]
            lines.append("| " + " | ".join(str(x).replace("|", "\\|") for x in row) + " |")
        lines += ["", "`--help` displays command help.", ""]
    return "\n".join(lines)


def audit():
    issues = []
    pages = {p: p.read_text(encoding="utf-8") for p in documents()}
    all_docs = "\n".join(pages.values())
    doc_only = "\n".join(text for p, text in pages.items() if p.is_relative_to(ROOT / "docs"))
    commands = command_inventory()
    for filename, renderer in (("python-api.md", api_reference), ("cli-options.md", cli_reference)):
        path = ROOT / "docs" / "reference" / filename
        if not path.exists() or path.read_text(encoding="utf-8").strip() != renderer().strip():
            issues.append(f"inventory: regenerate {path.relative_to(ROOT).as_posix()}")
    modules = exported_modules()
    symbols = {}
    for name, module in modules.items():
        for symbol in module.__all__:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="dbwarden.database is deprecated", category=DeprecationWarning)
                try:
                    value = getattr(module, symbol)
                except AttributeError:
                    issues.append(f"export: {name}.{symbol} does not resolve")
                    continue
            if not symbol.startswith("_"):
                symbols[f"{name}.{symbol}"] = value
    for name in symbols:
        if name.rsplit(".", 1)[1] not in doc_only:
            issues.append(f"coverage: undocumented export {name}")
    defaults = {}
    ambiguous = set()
    for name, value in symbols.items():
        short = name.rsplit(".", 1)[1]
        if short in defaults and defaults[short] is not value:
            ambiguous.add(short)
        defaults[short] = value
    defaults = {key: value for key, value in defaults.items() if key not in ambiguous
                and (inspect.isclass(value) or inspect.isfunction(value) or inspect.ismodule(value))}
    for name, command in commands.items():
        if name != "dbwarden" and name not in all_docs:
            issues.append(f"coverage: undocumented command {name}")
        for param in command.params:
            for option in [*getattr(param, "opts", []), *getattr(param, "secondary_opts", [])]:
                if option.startswith("--") and option not in all_docs:
                    issues.append(f"coverage: undocumented option {name} {option}")
    from dbwarden.config_registry import database_config
    for parameter in inspect.signature(database_config).parameters:
        if parameter != "plugin_config" and parameter not in doc_only:
            issues.append(f"coverage: undocumented database_config parameter {parameter}")
    nav = tomllib.loads((ROOT / "zensical.toml").read_text(encoding="utf-8"))["project"]["nav"]

    def nav_paths(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from nav_paths(item)
        elif isinstance(value, list):
            for item in value:
                yield from nav_paths(item)

    for path in nav_paths(nav):
        if not (ROOT / "docs" / path).exists():
            issues.append(f"navigation: missing {path}")
    documented_pages = {path.relative_to(ROOT / "docs").as_posix() for path in pages
                        if path.is_relative_to(ROOT / "docs")}
    for path in documented_pages - set(nav_paths(nav)):
        issues.append(f"navigation: unlisted page {path}")
    snippets = 0
    for path, text in pages.items():
        relative = path.relative_to(ROOT).as_posix()
        prose = re.sub(r"^```.*?^```[^\n]*", "", text, flags=re.MULTILINE | re.DOTALL)
        for match in re.finditer(r"\[[^\]\n]+\]\(([^\s)]+)(?:\s+[^)]*)?\)", prose):
            link = match.group(1).strip("<>")
            url = urlsplit(link)
            if url.netloc == "docs.dbwarden.org":
                route = url.path.strip("/")
                if not route:
                    target = ROOT / "docs" / "index.md"
                elif Path(route).suffix:
                    target = ROOT / "docs" / route
                else:
                    target = ROOT / "docs" / (route + ".md")
                    if not target.exists():
                        target = ROOT / "docs" / route / "index.md"
            elif url.scheme or url.netloc or link.startswith("/"):
                continue
            else:
                target = (path.parent / unquote(url.path)).resolve() if url.path else path
            if not target.exists():
                issues.append(f"link: {relative}: {link}")
            elif url.fragment and target.suffix == ".md":
                body = target.read_text(encoding="utf-8")
                headings = re.findall(r"^#{1,6}\s+(.+)$", re.sub(r"^```.*?^```[^\n]*", "", body, flags=re.MULTILINE | re.DOTALL), re.MULTILINE)
                anchors = {slug(h) for h in headings} | set(re.findall(r'(?:id|name)=["\']([^"\']+)', body))
                if unquote(url.fragment) not in anchors:
                    issues.append(f"anchor: {relative}: {link}")
        bindings = dict(defaults)
        for match in re.finditer(r"^\s*(?:\$ )?dbwarden ([^\n]+)", text, re.MULTILINE):
            if "`" in match.group(0):
                continue
            try:
                tokens = shlex.split(match.group(1), comments=True)
            except ValueError:
                continue
            command_name = "dbwarden"
            for token in tokens:
                if f"{command_name} {token}" in commands:
                    command_name += " " + token
            allowed = {opt for command in (commands['dbwarden'], commands[command_name])
                       for param in command.params for opt in getattr(param, 'opts', [])}
            allowed |= {opt for param in commands[command_name].params for opt in getattr(param, 'secondary_opts', [])}
            allowed.add("--help")
            for token in tokens:
                flag = token.split("=", 1)[0]
                if flag.startswith("--") and flag not in allowed and "..." not in flag:
                    issues.append(f"cli: {relative}: {command_name} has no {flag}")
        for match in blocks(text):
            snippets += 1
            line = text[:match.start()].count("\n") + 1
            location = f"{relative}:{line}"
            code = textwrap.dedent(match.group(1))
            try:
                tree = ast.parse(code)
            except SyntaxError as exc:
                issues.append(f"syntax: {location}: {exc.msg}")
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    bindings.pop(node.name, None)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            bindings.pop(target.id, None)
                elif isinstance(node, ast.ImportFrom) and not (node.module or "").startswith("dbwarden.") and node.module != "dbwarden":
                    for alias in node.names:
                        bindings.pop(alias.asname or alias.name, None)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and not node.level and (node.module == "dbwarden" or (node.module or "").startswith("dbwarden.")):
                    try:
                        module = importlib.import_module(node.module)
                    except ImportError as exc:
                        issues.append(f"import: {location}: {exc}")
                        continue
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        try:
                            bindings[alias.asname or alias.name] = getattr(module, alias.name)
                        except AttributeError:
                            issues.append(f"import: {location}: {node.module}.{alias.name}")
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
                    continue
                reference = node.func
                parts = []
                while isinstance(reference, ast.Attribute):
                    parts.insert(0, reference.attr)
                    reference = reference.value
                if not isinstance(reference, ast.Name):
                    continue
                parts.insert(0, reference.id)
                target = bindings.get(parts[0])
                if target is None:
                    continue
                try:
                    for part in parts[1:]:
                        target = getattr(target, part)
                    signature = inspect.signature(target)
                except AttributeError:
                    issues.append(f"attribute: {location}: {ast.unparse(node.func)}")
                    continue
                except (ValueError, TypeError):
                    continue
                try:
                    signature.bind(*[None for _ in node.args], **{k.arg: None for k in node.keywords})
                except TypeError as exc:
                    issues.append(f"signature: {location}: {ast.unparse(node.func)}: {exc}")
    return {"documents": len(pages), "python_snippets": snippets, "commands": len(commands), "exports": len(symbols), "issues": sorted(set(issues))}


if __name__ == "__main__":
    if "--api-reference" in sys.argv:
        print(api_reference(), end="")
        raise SystemExit(0)
    if "--cli-reference" in sys.argv:
        print(cli_reference(), end="")
        raise SystemExit(0)
    result = audit()
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result["issues"]))
