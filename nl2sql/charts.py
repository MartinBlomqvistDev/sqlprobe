"""Plotly figure JSON for client-side rendering.

The server returns a figure as JSON rather than an image. The browser hands it
to Plotly.js, which keeps the chart interactive and avoids a headless-browser
dependency on the server, which is the usual source of deployment pain.

Column selection is deliberately simple and documented: the first non-numeric
column is the category axis, the first numeric column is the value axis, and
the model can override either. A keyword-ranked heuristic guesses wrong in ways
that are hard to explain to a user, so the model gets to decide instead.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Literal

import plotly.graph_objects as go

logger = logging.getLogger(__name__)

ChartType = Literal["line", "bar", "pie"]

# A neutral palette. Swap these for your own brand colours.
PRIMARY = "#3f6b5c"
CATEGORICAL = [
    "#3f6b5c",
    "#c9772f",
    "#4a6d8c",
    "#8a6ba8",
    "#b5533f",
    "#7d8c4a",
    "#8c8c8c",
]

# Slices beyond this are grouped into a single "Other" wedge so pie charts stay
# readable. A config default, not a measurement.
MAX_PIE_SLICES = 7

_LAYOUT: dict[str, Any] = {
    "template": "plotly_white",
    "font": {"family": "Inter, system-ui, sans-serif", "size": 13},
    "margin": {"l": 70, "r": 30, "t": 60, "b": 60},
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
}


class ChartError(ValueError):
    """Raised when the given rows cannot be charted as requested."""


def is_numeric(value: Any) -> bool:
    """Return True for numbers, excluding bool which is technically an int."""
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def pick_columns(
    rows: list[dict[str, Any]],
    x_column: str | None = None,
    y_column: str | None = None,
) -> tuple[str, str]:
    """Choose the category and value columns for a chart.

    Args:
        rows: Result rows, each a mapping of column name to value.
        x_column: Explicit category column, or None to infer.
        y_column: Explicit value column, or None to infer.

    Returns:
        A tuple of (x_column, y_column).

    Raises:
        ChartError: If the rows are empty, or no suitable column pair exists.
    """
    if not rows:
        raise ChartError("there is no data to chart")

    columns = list(rows[0].keys())
    numeric = [c for c in columns if is_numeric(rows[0].get(c))]
    non_numeric = [c for c in columns if c not in numeric]

    if x_column and x_column not in columns:
        logger.warning("x_column %r is not in the result, inferring instead", x_column)
        x_column = None
    if y_column and y_column not in columns:
        logger.warning("y_column %r is not in the result, inferring instead", y_column)
        y_column = None

    resolved_x = x_column or (non_numeric[0] if non_numeric else columns[0])
    if y_column:
        resolved_y = y_column
    else:
        candidates = [c for c in numeric if c != resolved_x]
        if not candidates:
            raise ChartError("the result has no numeric column to plot")
        resolved_y = candidates[0]

    return resolved_x, resolved_y


def build_figure(
    rows: list[dict[str, Any]],
    chart_type: ChartType,
    title: str,
    x_column: str | None = None,
    y_column: str | None = None,
) -> str:
    """Build a Plotly figure and return it as a JSON string.

    Args:
        rows: Result rows to plot.
        chart_type: One of "line", "bar" or "pie".
        title: Title shown above the chart.
        x_column: Category column, inferred when omitted.
        y_column: Value column, inferred when omitted.

    Returns:
        The figure serialised with Plotly's own JSON encoder.

    Raises:
        ChartError: If the data or the requested chart type is unusable.
    """
    x_col, y_col = pick_columns(rows, x_column, y_column)
    x_values = [row.get(x_col) for row in rows]
    y_values = [row.get(y_col) for row in rows]

    layout = go.Layout(title={"text": title, "x": 0.5}, **_LAYOUT)

    if chart_type == "line":
        figure = go.Figure(
            go.Scatter(
                x=x_values,
                y=y_values,
                mode="lines+markers",
                line={"color": PRIMARY, "width": 2.5},
                marker={"size": 7, "color": PRIMARY},
                name=y_col,
            ),
            layout=layout,
        )
        figure.update_xaxes(title_text=x_col)
        figure.update_yaxes(title_text=y_col, tickformat=",")

    elif chart_type == "bar":
        # Horizontal bars, so long category labels stay readable without being
        # rotated. Height grows with the number of rows.
        labels = [_truncate(str(v), 34) for v in x_values]
        height = max(320, len(rows) * 34 + 130)
        bar_layout = go.Layout(
            title={"text": title, "x": 0.5},
            height=height,
            **{**_LAYOUT, "margin": {"l": 170, "r": 30, "t": 60, "b": 60}},
        )
        figure = go.Figure(
            go.Bar(
                x=y_values,
                y=labels,
                orientation="h",
                marker_color=PRIMARY,
                name=y_col,
            ),
            layout=bar_layout,
        )
        figure.update_yaxes(title_text=x_col, autorange="reversed")
        figure.update_xaxes(title_text=y_col, tickformat=",")

    elif chart_type == "pie":
        labels, values = _group_small_slices(x_values, y_values)
        figure = go.Figure(
            go.Pie(
                labels=labels,
                values=values,
                marker={"colors": CATEGORICAL},
                textinfo="label+percent",
            ),
            layout=layout,
        )

    else:
        raise ChartError(
            f"unknown chart type {chart_type!r}, expected line, bar or pie"
        )

    return figure.to_json()


def _truncate(value: str, limit: int) -> str:
    """Shorten a label to limit characters, with an ellipsis when cut."""
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _group_small_slices(
    labels: list[Any], values: list[Any]
) -> tuple[list[str], list[float]]:
    """Keep the largest slices and collapse the remainder into "Other"."""
    pairs = [
        (_truncate(str(label), 28), float(value))
        for label, value in zip(labels, values)
        if is_numeric(value)
    ]
    if not pairs:
        raise ChartError("the result has no numeric values to chart")

    pairs.sort(key=lambda pair: pair[1], reverse=True)
    if len(pairs) <= MAX_PIE_SLICES:
        return [p[0] for p in pairs], [p[1] for p in pairs]

    head = pairs[: MAX_PIE_SLICES - 1]
    other = sum(value for _, value in pairs[MAX_PIE_SLICES - 1 :])
    return [p[0] for p in head] + ["Other"], [p[1] for p in head] + [other]
