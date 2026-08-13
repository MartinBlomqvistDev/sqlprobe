"""Tool definitions and their implementations.

Two tools are exposed to the model:

    execute_sql     Run a read-only query and return the rows.
    generate_chart  Chart the rows the most recent query returned.

The second one deliberately takes no data. Asking the model to echo a result
set back so it can be charted costs the same tokens twice and gets slower the
more useful the answer is. Instead a ToolBox holds the last result for the
duration of one request, and the chart tool refers to it.

Adding a tool means adding one Tool entry and one branch in ToolBox.run. The
schemas are provider-neutral; each provider adapter translates them into its
own wire format.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from nl2sql import charts
from nl2sql.config import get_settings
from nl2sql.db import run_select

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tool:
    """A provider-neutral tool definition."""

    name: str
    description: str
    input_schema: dict[str, Any]


EXECUTE_SQL = Tool(
    name="execute_sql",
    description=(
        "Run a read-only SQL SELECT against the analytics database and return "
        "the resulting rows. Only SELECT is permitted; anything else is "
        "rejected before it reaches the database. Results are capped, so "
        "aggregate in SQL rather than pulling raw rows and counting yourself."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A single valid SQL SELECT statement.",
            },
            "explanation": {
                "type": "string",
                "description": "One sentence on what this query is meant to show.",
            },
        },
        "required": ["query"],
    },
)

GENERATE_CHART = Tool(
    name="generate_chart",
    description=(
        "Chart the rows returned by the most recent execute_sql call. Call it "
        "straight after a query whose result is easier to see than to read. "
        "Use 'line' for a value over time, 'bar' to compare categories, and "
        "'pie' for shares of a whole. Do not call it for a single value, or "
        "for results with more than about fifty rows."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "chart_type": {
                "type": "string",
                "enum": ["line", "bar", "pie"],
                "description": "The kind of chart to draw.",
            },
            "title": {
                "type": "string",
                "description": "Title shown above the chart.",
            },
            "x_column": {
                "type": "string",
                "description": (
                    "Column for the category axis. Omit to use the first "
                    "non-numeric column."
                ),
            },
            "y_column": {
                "type": "string",
                "description": (
                    "Column for the value axis. Omit to use the first numeric "
                    "column."
                ),
            },
        },
        "required": ["chart_type", "title"],
    },
)

TOOLS: tuple[Tool, ...] = (EXECUTE_SQL, GENERATE_CHART)


@dataclass
class ToolBox:
    """Executes tools and carries the small amount of state they share.

    One instance per chat request. Holding the last result here rather than in
    a module-level variable keeps concurrent requests from reading each other's
    rows.
    """

    last_rows: list[dict[str, Any]] = field(default_factory=list)

    async def run(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a tool call by name.

        Args:
            name: The tool the model asked for.
            arguments: The arguments it supplied, already parsed from JSON.

        Returns:
            The tool result as a JSON-serialisable mapping. Failures are
            returned as an `error` key rather than raised, so the model can
            read the message and correct itself.
        """
        if name == EXECUTE_SQL.name:
            return await self._execute_sql(arguments)
        if name == GENERATE_CHART.name:
            return self._generate_chart(arguments)
        return {"error": f"Unknown tool: {name}"}

    async def _execute_sql(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run a query and remember its rows for a possible chart."""
        result = await run_select(str(arguments.get("query", "")))
        self.last_rows = result.get("rows", []) if "error" not in result else []
        return result

    def _generate_chart(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Chart the rows from the most recent query."""
        if not self.last_rows:
            return {
                "error": (
                    "There is nothing to chart. Run execute_sql first, then "
                    "call generate_chart on its result."
                )
            }
        try:
            figure_json = charts.build_figure(
                rows=self.last_rows,
                chart_type=arguments.get("chart_type", "bar"),
                title=str(arguments.get("title", "")),
                x_column=arguments.get("x_column"),
                y_column=arguments.get("y_column"),
            )
        except charts.ChartError as exc:
            return {"error": f"Could not draw the chart: {exc}"}
        except Exception as exc:  # noqa: BLE001 - a chart must never end a turn
            logger.error("chart generation failed: %s", exc)
            return {"error": f"Could not draw the chart: {exc}"}

        return {
            "figure_json": figure_json,
            "chart_type": arguments.get("chart_type", "bar"),
            "row_count": len(self.last_rows),
        }


def summarise_for_model(result: dict[str, Any]) -> dict[str, Any]:
    """Shrink a tool result before it is fed back into the model's context.

    Two reductions, both of which pay for themselves on any real dataset:

    1. The Plotly figure is replaced by a short confirmation. The model only
       needs to know a chart exists; the client already has the full JSON from
       the event stream.
    2. Rows are capped at the configured limit. A model cannot reason usefully
       over a thousand raw rows, and paying to put them in context every turn
       makes the whole conversation more expensive. The client still receives
       the complete result over SSE.

    Args:
        result: The full tool result, as sent to the client.

    Returns:
        A reduced copy safe to include in the conversation history.
    """
    settings = get_settings()
    reduced = dict(result)

    if "figure_json" in reduced:
        chart_type = reduced.get("chart_type", "chart")
        reduced["figure_json"] = f"[{chart_type} chart rendered for the user]"

    rows = reduced.get("rows")
    if isinstance(rows, list) and len(rows) > settings.llm_max_rows:
        total = reduced.get("row_count", len(rows))
        reduced["rows"] = rows[: settings.llm_max_rows]
        reduced["note"] = (
            f"Showing the first {settings.llm_max_rows} of {total} rows. The "
            "user has the full result. Summarise from these rows, or run a "
            "more specific aggregation."
        )

    return reduced
