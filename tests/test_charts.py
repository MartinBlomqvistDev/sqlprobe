"""Column inference and figure construction."""

from __future__ import annotations

import json

import pytest

from nl2sql.charts import ChartError, build_figure, pick_columns

ROWS = [
    {"month": "2026-01", "revenue": 12000.0, "orders": 140},
    {"month": "2026-02", "revenue": 13100.5, "orders": 151},
    {"month": "2026-03", "revenue": 11890.25, "orders": 133},
]


def test_first_non_numeric_and_first_numeric_are_chosen() -> None:
    assert pick_columns(ROWS) == ("month", "revenue")


def test_explicit_columns_win() -> None:
    assert pick_columns(ROWS, x_column="month", y_column="orders") == ("month", "orders")


def test_unknown_column_falls_back_to_inference() -> None:
    assert pick_columns(ROWS, y_column="not_a_column") == ("month", "revenue")


def test_empty_rows_are_rejected() -> None:
    with pytest.raises(ChartError):
        pick_columns([])


def test_all_text_rows_are_rejected() -> None:
    with pytest.raises(ChartError):
        pick_columns([{"a": "x", "b": "y"}])


@pytest.mark.parametrize("chart_type", ["line", "bar", "pie"])
def test_every_chart_type_produces_a_figure(chart_type: str) -> None:
    figure = json.loads(build_figure(ROWS, chart_type, "Revenue by month"))
    assert figure["data"], "the figure must carry a trace"
    assert figure["layout"]["title"]["text"] == "Revenue by month"


def test_unknown_chart_type_is_rejected() -> None:
    with pytest.raises(ChartError):
        build_figure(ROWS, "sunburst", "Nope")  # type: ignore[arg-type]


def test_pie_groups_small_slices() -> None:
    rows = [{"label": f"item-{i}", "value": 100 - i} for i in range(12)]
    figure = json.loads(build_figure(rows, "pie", "Shares"))
    labels = figure["data"][0]["labels"]
    assert len(labels) == 7
    assert labels[-1] == "Other"
    # No value is lost when slices are grouped.
    assert sum(figure["data"][0]["values"]) == pytest.approx(sum(r["value"] for r in rows))
