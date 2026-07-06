"""Shared ``START:END:STEP`` sweep-axis parser.

Used by both the CLI (`core.cli._parse_axis`, campaign/config options)
and the TUI (`tui.modals.parse_triplet`, `CampaignFormState`) so the
two front-ends can't drift on what counts as a valid axis spec.
"""

from __future__ import annotations


def parse_triplet(spec: str) -> tuple[int, int, int]:
    """Accept ``"START:END:STEP"`` or a single ``"N"`` (collapses
    axis). Integers accept any base ``int(x, 0)`` understands (plain
    decimal, or ``0x``/``0o``/``0b`` prefixed). Returns
    ``(start, end, step)``; raises ``ValueError`` on a malformed
    input or a non-monotonic / non-positive-step span.
    """
    parts = spec.strip().split(":")
    if len(parts) == 1:
        n = int(parts[0], 0)
        return (n, n, 0)
    if len(parts) != 3:
        raise ValueError(
            f"triplet must be 'START:END:STEP' or single 'N', got {spec!r}"
        )
    start, end, step = (int(p, 0) for p in parts)
    if start > end:
        raise ValueError(f"triplet start ({start}) must be <= end ({end})")
    if start != end and step <= 0:
        raise ValueError(
            f"triplet step must be > 0 when start ({start}) != end ({end})"
        )
    return (start, end, step)
