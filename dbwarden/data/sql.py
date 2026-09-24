from sqlalchemy import text
from sqlglot import tokenize


def statement_text(statement):
    if ":" not in statement:
        return text(statement)
    spans = [
        (token.start, token.end + 1)
        for token in tokenize(statement, read="mysql")
        if ":" in token.text and statement[token.start] in {"'", '"', "`"}
    ]
    for start, end in reversed(spans):
        statement = (
            statement[:start]
            + statement[start:end].replace(":", "\\:")
            + statement[end:]
        )
    return text(statement)
