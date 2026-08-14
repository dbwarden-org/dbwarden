from __future__ import annotations


def split_sql_statements(sql: str) -> list[str]:
    """Split SQL on semicolons outside quoted strings and SQL comments."""
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    line_comment = False
    block_comment = False
    index = 0
    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""
        if line_comment:
            current.append(char)
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            current.append(char)
            if char == "*" and next_char == "/":
                current.append(next_char)
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote:
            current.append(char)
            if char == quote:
                if next_char == quote:
                    current.append(next_char)
                    index += 2
                    continue
                quote = None
            elif char == "\\" and next_char:
                current.append(next_char)
                index += 2
                continue
            index += 1
            continue
        if char in ("'", '"', "`"):
            quote = char
        elif char == "-" and next_char == "-":
            line_comment = True
        elif char == "/" and next_char == "*":
            block_comment = True
        elif char == ";":
            if current_text := "".join(current).strip():
                statements.append(current_text)
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if current_text := "".join(current).strip():
        statements.append(current_text)
    return statements
