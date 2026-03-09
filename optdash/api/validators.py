"""Shared FastAPI query-parameter type annotations for all API routers.

Centralises TradeDate and SnapTime so they are maintained in one place
and reused across market.py, micro.py, screener.py, ws.py, and ai.py.

FastAPI returns a structured 422 Unprocessable Entity with field-level
detail on any constraint violation -- replacing the silent 200+empty
that unvalidated string params produce for malformed dates/times.

Examples of rejected values:
  TradeDate: '2026-3-9' (missing zero-pad), '20260309' (no dashes)
  SnapTime:  '9:5'  (missing zero-pad), '25:00' (hour out of range)
"""
from typing import Annotated
from pydantic import StringConstraints


# YYYY-MM-DD with zero-padded month and day.
# Examples: '2026-03-09', '2025-12-31'
TradeDate = Annotated[
    str,
    StringConstraints(
        pattern=r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$",
        strip_whitespace=True,
    ),
]


# HH:MM 24-hour clock, zero-padded.
# Examples: '09:15', '15:30'  Valid range: 00:00 - 23:59
# (Identical to the SnapTime in ai.py -- both are defined here as the
# single source of truth; ai.py will migrate to this import in a future
# cleanup commit.)
SnapTime = Annotated[
    str,
    StringConstraints(
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$",
        strip_whitespace=True,
    ),
]
