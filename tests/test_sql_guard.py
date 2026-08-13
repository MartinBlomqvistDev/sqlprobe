"""The guard is the load-bearing safety control, so it gets the most tests."""

from __future__ import annotations

import pytest

from nl2sql.sql_guard import enforce_limit, validate_select

ALLOWED = [
    ("plain select", "SELECT * FROM orders"),
    ("select with where", "SELECT * FROM orders WHERE status = 'completed'"),
    ("aggregate", "SELECT channel, SUM(order_total) FROM orders GROUP BY channel"),
    ("cte", "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent"),
    ("join", "SELECT o.order_id FROM orders o JOIN customers c ON c.customer_id = o.customer_id"),
    ("subquery", "SELECT * FROM orders WHERE customer_id IN (SELECT customer_id FROM customers)"),
    ("union", "SELECT country FROM customers UNION SELECT shipping_country FROM orders"),
    ("intersect", "SELECT country FROM customers INTERSECT SELECT shipping_country FROM orders"),
    ("except", "SELECT country FROM customers EXCEPT SELECT shipping_country FROM orders"),
]

BLOCKED = [
    ("multi statement injection", "SELECT 1; DROP TABLE orders"),
    ("trailing comment injection", "SELECT 1; DELETE FROM orders; --"),
    ("drop", "DROP TABLE customers"),
    ("insert", "INSERT INTO orders (order_id) VALUES (1)"),
    ("update", "UPDATE orders SET order_total = 0"),
    ("delete", "DELETE FROM orders"),
    ("create", "CREATE TABLE evil (id INTEGER)"),
    ("alter", "ALTER TABLE orders ADD COLUMN x INTEGER"),
    ("delete hidden in a cte", "WITH x AS (DELETE FROM orders WHERE 1=1) SELECT 1"),
    ("pragma command", "PRAGMA table_info(orders)"),
    ("attach command", "ATTACH DATABASE 'other.db' AS other"),
    ("empty", ""),
    ("whitespace only", "   \n  "),
]


@pytest.mark.parametrize("description,query", ALLOWED, ids=[d for d, _ in ALLOWED])
def test_read_only_queries_are_allowed(description: str, query: str) -> None:
    valid, reason = validate_select(query)
    assert valid, f"{description} should be allowed, got: {reason}"


@pytest.mark.parametrize("description,query", BLOCKED, ids=[d for d, _ in BLOCKED])
def test_writes_and_injection_are_blocked(description: str, query: str) -> None:
    valid, reason = validate_select(query)
    assert not valid, f"{description} should be blocked"
    assert reason, "a blocked query must explain why"


def test_limit_is_added_when_missing() -> None:
    result = enforce_limit("SELECT * FROM orders", max_rows=50)
    assert "LIMIT 50" in result.upper()


def test_limit_above_the_cap_is_lowered() -> None:
    result = enforce_limit("SELECT * FROM orders LIMIT 90000", max_rows=50)
    assert "LIMIT 50" in result.upper()
    assert "90000" not in result


def test_limit_below_the_cap_is_left_alone() -> None:
    query = "SELECT * FROM orders LIMIT 5"
    assert enforce_limit(query, max_rows=50) == query


def test_limit_applies_to_the_whole_union_not_one_branch() -> None:
    result = enforce_limit(
        "SELECT country FROM customers UNION SELECT shipping_country FROM orders",
        max_rows=30,
    )
    assert result.upper().count("LIMIT") == 1
    assert result.upper().rstrip().endswith("LIMIT 30")


def test_limit_is_applied_to_a_cte() -> None:
    result = enforce_limit(
        "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent", max_rows=25
    )
    assert "LIMIT 25" in result.upper()


def test_unparseable_query_is_returned_unchanged() -> None:
    # Validation runs first, so enforce_limit never has to block on its own.
    weird = "SELECT ((("
    assert enforce_limit(weird, max_rows=10) == weird
