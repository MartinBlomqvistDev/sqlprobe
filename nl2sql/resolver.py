"""The pseudonymisation boundary: identifiers to people, resolved locally.

The analytics database holds no names and no e-mail addresses, only a stable
pseudonymous `customer_ref`. That is not an accident of the demo data, it is
the design: the model can only ever see what the analytics database contains,
so no amount of prompt injection or model misbehaviour can send a real person's
identity to a third-party API.

The flow for a question like "who is our best customer?":

    1. The model runs SQL and gets back customer_ref = "cus_00042".
    2. The model answers using that reference. The API sees only the reference.
    3. The client, if the user is entitled to it, calls
       GET /api/resolve/{customer_ref}.
    4. This module looks the reference up in a separate local directory
       database and returns the name.

Step 4 never touches the LLM. The lookup is a plain indexed read, so it is also
the natural place to hang an authorisation check, an audit log entry, and a
retention policy. See the README for the GDPR rationale in full.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import text

from nl2sql.config import get_settings
from nl2sql.db import get_engine

logger = logging.getLogger(__name__)

# References are machine generated, so a strict pattern is both a validation
# rule and a cheap guard against anything odd reaching the directory query.
REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,64}$")


class Person(dict):
    """A resolved directory entry. A plain dict so it serialises directly."""


async def resolve_reference(customer_ref: str) -> dict[str, str] | None:
    """Look up the person behind a pseudonymous reference.

    Args:
        customer_ref: The reference as it appears in the analytics database.

    Returns:
        A mapping with `customer_ref`, `full_name` and `contact_email`, or None
        when the reference is malformed or unknown.
    """
    if not REFERENCE_PATTERN.match(customer_ref or ""):
        return None

    settings = get_settings()
    try:
        engine = get_engine(settings.directory_url)
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT customer_ref, full_name, contact_email "
                    "FROM directory_entries WHERE customer_ref = :ref LIMIT 1"
                ),
                {"ref": customer_ref},
            )
            row = result.mappings().first()
    except Exception as exc:  # noqa: BLE001 - never leak directory internals
        logger.error(
            "directory lookup failed: %s", exc, extra={"operation": "resolve_error"}
        )
        return None

    if row is None:
        return None

    logger.info("reference resolved", extra={"operation": "resolve_ok"})
    return dict(row)
