from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table


console = Console(force_terminal=False, no_color=False, width=160, color_system="standard")


def info(message: str) -> None:
    console.print(message, style="cyan")


def success(message: str) -> None:
    console.print(message, style="green")


def warning(message: str) -> None:
    console.print(message, style="yellow")


def error(message: str) -> None:
    console.print(message, style="bold red")


def plain(message: str) -> None:
    console.print(message, markup=False, highlight=False)


def sql(message: str) -> None:
    console.print(Syntax(message, "sql", theme="ansi_dark", word_wrap=True))


def section(title: str) -> None:
    console.print(Rule(f"[bold cyan]{title}[/bold cyan]"))


def subsection(title: str) -> None:
    console.print(f"\n[bold cyan]{title}[/bold cyan]")


def empty_state(message: str) -> None:
    console.print(Panel(message, style="yellow", border_style="yellow"))


def success_panel(title: str, message: str) -> None:
    console.print(Panel(message, title=title, style="green", border_style="green"))


def error_panel(title: str, message: str) -> None:
    console.print(Panel(message, title=title, style="bold red", border_style="red"))


def info_panel(title: str, message: str) -> None:
    console.print(Panel(message, title=title, style="cyan", border_style="cyan"))


def kv_table(title: str | None, rows: Mapping[str, Any] | Sequence[tuple[str, Any]]) -> Table:
    table = Table(title=title, show_header=False, box=None)
    table.add_column("Key", style="bold cyan", no_wrap=True)
    table.add_column("Value", overflow="fold")
    items = rows.items() if isinstance(rows, Mapping) else rows
    for key, value in items:
        table.add_row(str(key), _format_value(value))
    return table


def data_table(
    title: str | None,
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
) -> Table:
    table = Table(title=title)
    for column in columns:
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(*[_format_value(value) for value in row])
    return table


def render(renderable: Any) -> None:
    console.print(renderable)


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(str(item) for item in value) or "-"
    return str(value)
