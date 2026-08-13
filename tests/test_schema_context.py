"""The system prompt must describe the database that actually exists.

A schema description that has drifted from the schema is the single most
common cause of a text-to-SQL system quietly getting worse, and it fails
silently: the model writes plausible SQL against tables that are not there.
These tests fail loudly instead.
"""

from __future__ import annotations

import pytest

from nl2sql.config import Settings
from nl2sql.db import run_select
from nl2sql.schema_context import build_system_prompt

EXPECTED_TABLES = {"customers", "products", "orders", "order_items", "daily_sales"}


async def test_the_prompt_names_every_table_that_exists(settings: Settings) -> None:
    result = await run_select(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%'"
    )
    actual = {row["name"] for row in result["rows"]}
    assert actual == EXPECTED_TABLES

    prompt = build_system_prompt()
    for table in actual:
        assert table in prompt, f"{table} exists but is not described in the prompt"


@pytest.mark.parametrize("table", sorted(EXPECTED_TABLES))
async def test_every_column_is_described(settings: Settings, table: str) -> None:
    result = await run_select(f"SELECT name FROM pragma_table_info('{table}')")
    prompt = build_system_prompt()
    for row in result["rows"]:
        assert row["name"] in prompt, (
            f"{table}.{row['name']} exists but is not described in the prompt"
        )


async def test_the_worked_examples_actually_run(settings: Settings) -> None:
    """Every example in the prompt is executed against the demo database.

    An example that does not run teaches the model a query shape that fails.
    """
    from nl2sql.schema_context import QUERY_EXAMPLES

    statements = [
        block.strip().rstrip(";")
        for block in QUERY_EXAMPLES.split("SQL:")[1:]
    ]
    statements = [s.split("Q:")[0].strip().rstrip(";") for s in statements]
    assert len(statements) >= 4, "keep at least four worked examples"

    for statement in statements:
        result = await run_select(statement)
        assert "error" not in result, f"example failed: {result.get('error')}"


def test_the_prompt_is_stable_between_calls() -> None:
    """Prompt caching is a prefix match, so the prompt must not vary."""
    assert build_system_prompt() == build_system_prompt()


def test_the_prompt_states_the_revenue_rule() -> None:
    prompt = build_system_prompt().lower()
    assert "completed" in prompt, "the prompt must say which orders count as revenue"
