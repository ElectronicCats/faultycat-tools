"""Jupyter rendering helpers.

The faultycmd status dataclasses (``EmfiStatus``, ``CrowbarStatus``,
``CampaignStatus``) already expose ``.as_rows()`` -> list[(field, value)]
for the CLI's Rich tables. We reuse that exact shape to render an HTML
table in Jupyter, so the notebook and the CLI show identical fields
from a single source of truth.
"""

from __future__ import annotations

from collections.abc import Iterable


def rows_to_html(title: str, rows: Iterable[tuple[str, str]]) -> str:
    """Render (field, value) rows as a compact HTML table for Jupyter."""
    body = "".join(
        f"<tr><td style='padding:2px 10px 2px 0;color:#888'>{k}</td>"
        f"<td style='padding:2px 0;font-family:monospace'>{v}</td></tr>"
        for k, v in rows
    )
    return (
        f"<div><b style='font-family:monospace'>{title}</b>"
        f"<table style='border-collapse:collapse;margin-top:4px'>{body}</table></div>"
    )


def status_html(title: str, status: object) -> str:
    """Render a faultycmd status dataclass via its ``.as_rows()``."""
    as_rows = getattr(status, "as_rows", None)
    if callable(as_rows):
        return rows_to_html(title, as_rows())
    return rows_to_html(title, [("repr", repr(status))])
