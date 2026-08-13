"""Tool dispatch, chart state and the token-economy reduction."""

from __future__ import annotations

from nl2sql.config import Settings, get_settings
from nl2sql.tools import TOOLS, ToolBox, summarise_for_model


def test_every_tool_has_a_usable_schema() -> None:
    for tool in TOOLS:
        assert tool.name
        assert len(tool.description) > 40, f"{tool.name} needs a fuller description"
        assert tool.input_schema["type"] == "object"
        for name, spec in tool.input_schema["properties"].items():
            assert spec.get("description"), f"{tool.name}.{name} needs a description"


async def test_execute_sql_runs_and_remembers_rows(settings: Settings) -> None:
    box = ToolBox()
    result = await box.run(
        "execute_sql", {"query": "SELECT country, COUNT(*) AS n FROM customers GROUP BY country"}
    )
    assert "error" not in result
    assert box.last_rows == result["rows"]


async def test_execute_sql_clears_rows_on_failure(settings: Settings) -> None:
    box = ToolBox()
    await box.run("execute_sql", {"query": "SELECT country FROM customers"})
    await box.run("execute_sql", {"query": "DROP TABLE customers"})
    assert box.last_rows == []


async def test_chart_uses_the_last_result(settings: Settings) -> None:
    box = ToolBox()
    await box.run(
        "execute_sql",
        {"query": "SELECT channel, COUNT(*) AS orders FROM orders GROUP BY channel"},
    )
    result = await box.run("generate_chart", {"chart_type": "bar", "title": "Orders"})
    assert "error" not in result
    assert result["figure_json"].startswith("{")


async def test_chart_without_a_query_explains_itself(settings: Settings) -> None:
    box = ToolBox()
    result = await box.run("generate_chart", {"chart_type": "bar", "title": "Nothing"})
    assert "error" in result
    assert "execute_sql" in result["error"]


async def test_unknown_tool_is_reported_not_raised(settings: Settings) -> None:
    result = await ToolBox().run("drop_everything", {})
    assert "error" in result


def test_rows_are_capped_before_going_back_to_the_model(
    settings: Settings, monkeypatch
) -> None:
    monkeypatch.setenv("LLM_MAX_ROWS", "5")
    get_settings.cache_clear()
    try:
        full = {"rows": [{"i": i} for i in range(40)], "row_count": 40}
        reduced = summarise_for_model(full)
        assert len(reduced["rows"]) == 5
        assert "40" in reduced["note"]
        # The original is untouched, so the client still gets everything.
        assert len(full["rows"]) == 40
    finally:
        get_settings.cache_clear()


def test_figure_json_is_stripped_before_going_back_to_the_model(
    settings: Settings,
) -> None:
    reduced = summarise_for_model(
        {"figure_json": "{" + "x" * 50_000 + "}", "chart_type": "line"}
    )
    assert len(reduced["figure_json"]) < 100
    assert "line" in reduced["figure_json"]


def test_small_results_pass_through_unchanged(settings: Settings) -> None:
    small = {"rows": [{"a": 1}], "row_count": 1}
    assert summarise_for_model(small) == small
