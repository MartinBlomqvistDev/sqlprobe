"""The read path: validation, limits and error reporting against real data."""

from __future__ import annotations

from nl2sql.config import Settings
from nl2sql.db import run_select


async def test_select_returns_rows_and_columns(settings: Settings) -> None:
    result = await run_select("SELECT customer_ref, country FROM customers LIMIT 3")
    assert "error" not in result
    assert result["row_count"] == 3
    assert result["columns"] == ["customer_ref", "country"]
    assert set(result["rows"][0]) == {"customer_ref", "country"}


async def test_write_is_refused_before_reaching_the_database(settings: Settings) -> None:
    result = await run_select("DELETE FROM orders")
    assert "error" in result
    assert result["rows"] == []
    # The table must still be there.
    check = await run_select("SELECT COUNT(*) AS n FROM orders")
    assert check["rows"][0]["n"] > 0


async def test_row_cap_is_enforced(settings: Settings, monkeypatch) -> None:
    from nl2sql.config import get_settings

    monkeypatch.setenv("MAX_ROWS", "7")
    get_settings.cache_clear()
    try:
        result = await run_select("SELECT * FROM orders")
        assert result["row_count"] <= 7
        assert "LIMIT 7" in result["query"].upper()
    finally:
        get_settings.cache_clear()


async def test_bad_column_returns_a_usable_error(settings: Settings) -> None:
    result = await run_select("SELECT no_such_column FROM orders")
    assert "error" in result
    assert "no_such_column" in result["error"]
    # The message must carry the query so the model can see what it wrote.
    assert "Failed query" in result["error"]


async def test_referential_integrity_of_the_seeded_data(settings: Settings) -> None:
    orphan_items = await run_select(
        "SELECT COUNT(*) AS n FROM order_items oi "
        "LEFT JOIN orders o ON o.order_id = oi.order_id WHERE o.order_id IS NULL"
    )
    orphan_orders = await run_select(
        "SELECT COUNT(*) AS n FROM orders o "
        "LEFT JOIN customers c ON c.customer_id = o.customer_id "
        "WHERE c.customer_id IS NULL"
    )
    assert orphan_items["rows"][0]["n"] == 0
    assert orphan_orders["rows"][0]["n"] == 0


async def test_daily_sales_agrees_with_completed_orders(settings: Settings) -> None:
    """The aggregate table must match what it claims to aggregate."""
    from_orders = await run_select(
        "SELECT ROUND(SUM(order_total), 2) AS total FROM orders "
        "WHERE status = 'completed'"
    )
    from_daily = await run_select(
        "SELECT ROUND(SUM(net_revenue), 2) AS total FROM daily_sales"
    )
    assert from_orders["rows"][0]["total"] == from_daily["rows"][0]["total"]
