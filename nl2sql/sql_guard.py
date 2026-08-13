"""Read-only enforcement for model-generated SQL.

An LLM writing SQL against a live database is only safe if something other than
the prompt decides what may run. This module is that something. It parses the
query into an abstract syntax tree with sqlglot and applies four rules:

    1. One statement only, which stops semicolon-injected payloads.
    2. The root must be SELECT, or a WITH that resolves to a SELECT.
    3. The whole tree is walked, so a DELETE hidden inside a CTE or a subquery
       is caught rather than merely the top-level verb being inspected.
    4. A LIMIT is added or capped, so no single question can pull a whole table
       into the process.

String matching on keywords is not enough here: comments, casing and nested
statements all defeat it. Parsing is the point.

The guard is a second line of defence, not the only one. Point the application
at a database user that holds SELECT and nothing else.
"""

from __future__ import annotations

import logging

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

# Statement types that must never execute, checked across the entire tree.
_FORBIDDEN: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
)

# Parser dialect. Change this alongside DATABASE_URL when moving off SQLite.
DIALECT = "sqlite"


def validate_select(query: str) -> tuple[bool, str]:
    """Check that a query is a single, read-only SELECT.

    Args:
        query: The SQL to inspect.

    Returns:
        A tuple of (is_valid, reason). The reason is an empty string when the
        query is valid, and a short human-readable explanation otherwise.
    """
    stripped = query.strip()
    if not stripped:
        return False, "the query is empty"

    try:
        statements = sqlglot.parse(stripped, dialect=DIALECT)
    except sqlglot.errors.ParseError as exc:
        return False, f"the query could not be parsed ({exc})"

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return False, f"exactly one statement is allowed, got {len(statements)}"

    statement = statements[0]

    # Select covers plain queries and CTEs, which sqlglot attaches to the
    # SELECT rather than wrapping it. SetOperation covers UNION, INTERSECT and
    # EXCEPT, which are read-only but are not Select nodes. With is the root
    # only on older sqlglot versions, and is kept so a pinned older release
    # does not silently start rejecting every CTE.
    if isinstance(statement, exp.With):
        if not isinstance(statement.this, (exp.Select, exp.SetOperation)):
            return False, "a WITH clause must end in a SELECT"
    elif not isinstance(statement, (exp.Select, exp.SetOperation)):
        return False, f"only SELECT is allowed, got {type(statement).__name__}"

    for node in statement.walk():
        if isinstance(node, _FORBIDDEN):
            return False, f"forbidden operation in the query: {type(node).__name__}"

    return True, ""


def enforce_limit(query: str, max_rows: int) -> str:
    """Ensure the query carries a LIMIT no larger than max_rows.

    A query with no LIMIT gets one. A query whose LIMIT exceeds the cap is
    rewritten down to it. A query already within bounds is returned untouched,
    so the model sees back the SQL it wrote.

    Args:
        query: A query that has already passed validate_select.
        max_rows: The largest number of rows any single query may return.

    Returns:
        The query with an enforced LIMIT, or the original query if it could not
        be rewritten. Validation has already passed at this point, so falling
        back to the original is safe.
    """
    try:
        statement = sqlglot.parse_one(query, dialect=DIALECT)
    except sqlglot.errors.ParseError:
        return query

    # Query is the shared base of Select and the set operations, and its
    # limit() applies to the whole statement. Applying a limit to the first
    # inner SELECT instead would cap only one branch of a UNION.
    if not isinstance(statement, exp.Query):
        return query

    limit_node = statement.args.get("limit")
    if limit_node is not None:
        current = _literal_limit(limit_node)
        if current is not None and current <= max_rows:
            return query

    return statement.limit(max_rows).sql(dialect=DIALECT)


def _literal_limit(limit_node: exp.Expression) -> int | None:
    """Return the row count of a literal LIMIT, or None if it is not literal."""
    expression = limit_node.expression or limit_node.this
    try:
        return int(expression.name)
    except (AttributeError, TypeError, ValueError):
        return None
