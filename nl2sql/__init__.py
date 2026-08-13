"""A reusable natural language to SQL chat agent.

The package is deliberately small and each module owns one concern:

    config          Environment-backed settings (12-factor).
    logging_setup   Structured JSON logging with a per-request correlation id.
    events          Pydantic contracts for the server-sent event stream.
    db              Async SQLAlchemy engine for the analytics database.
    sql_guard       SELECT-only validation and row-limit enforcement.
    schema_context  The system prompt: schema, rules and query examples.
    charts          Plotly figure JSON for client-side rendering.
    resolver        The pseudonymisation boundary (ids to names, locally).
    tools           Tool definitions and their implementations.
    providers       One interface over four LLM providers.
    agent           The tool-use loop that ties it together.
    api             The FastAPI surface.
"""

__version__ = "0.1.0"
