"""A small question bank for exercising the agent end to end.

These are the questions a new schema should be able to answer before anyone
calls the integration finished. They cover the shapes that break first: simple
aggregation, grouping over time, joins across the grain boundary, ratios,
filters that must be applied, and questions the data deliberately cannot
answer.

Each entry is (number, role, question). The role is a label for reading the
results, not something the agent sees.

Replace these wholesale when you point the template at a real database. A
question bank is only useful if it asks what your users ask.
"""

from __future__ import annotations

QUESTIONS: list[tuple[int, str, str]] = [
    # --- Simple aggregation ------------------------------------------------
    (1, "analyst", "How many customers do we have?"),
    (2, "analyst", "What is our total revenue?"),
    (3, "analyst", "How many orders were placed in total?"),
    (4, "analyst", "What is the average order value?"),
    # --- Grouping over time ------------------------------------------------
    (5, "manager", "How has revenue developed month by month?"),
    (6, "manager", "Which month had the highest revenue?"),
    (7, "manager", "Show me units sold per month over the last year."),
    # --- Grouping by dimension ---------------------------------------------
    (8, "analyst", "Which sales channel brings in the most revenue?"),
    (9, "analyst", "How does revenue break down by country?"),
    (10, "manager", "Which product categories sell best?"),
    (11, "analyst", "How many customers are on the plus tier versus standard?"),
    # --- Joins across the grain boundary -----------------------------------
    (12, "analyst", "Which five products sell the most units?"),
    (13, "manager", "Which products have the best margin?"),
    (14, "analyst", "What is the average number of items per order?"),
    # --- Ranking and filtering ---------------------------------------------
    (15, "manager", "Who are our ten highest spending customers?"),
    (16, "analyst", "Which countries do we ship to most often?"),
    (17, "manager", "What share of orders is cancelled or returned, by channel?"),
    (18, "analyst", "How many orders had a discount applied?"),
    # --- Questions the data cannot answer ----------------------------------
    # A good answer here says so plainly. A bad one invents a table.
    (19, "manager", "What is the email address of our best customer?"),
    (20, "manager", "How many people visited the website last week?"),
]
