"""GlitchController — tie a fault-injection sweep together.

Bundles the three things every glitch loop repeats: iterate the parameter
grid, classify each attempt into a group, collect the results. Mirrors
ChipWhisperer's ``GlitchController`` so the notebook stays thin — the
per-attempt glitch+observe stays yours (it is target-specific); this owns
the bookkeeping.

    gc = fc.GlitchController(["delay", "width"])
    gc.set_range("delay", range(0, 500, 20)).set_range("width", [10])
    for p in gc.glitch_values():
        # ... glitch at p["delay"] / p["width"], read the target ...
        gc.add("success" if hit else "normal")
    gc.counts()
    gc.plot(x="delay", y="width")
"""

from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Iterable, Iterator
from typing import Any


class GlitchController:
    """Iterate parameters, classify attempts, collect + plot results."""

    def __init__(
        self,
        parameters: Iterable[str],
        groups: Iterable[str] = ("success", "reset", "normal"),
    ) -> None:
        self.parameters = list(parameters)
        self.groups = list(groups)
        self._ranges: dict[str, list] = {p: [0] for p in self.parameters}
        self._records: list[dict] = []
        self._current: dict[str, Any] = {}

    def set_range(self, parameter: str, values: Iterable) -> GlitchController:
        """Set the values swept for ``parameter`` (any iterable: a
        ``range``, a list, ...). Returns self so calls chain."""
        if parameter not in self.parameters:
            raise KeyError(
                f"unknown parameter {parameter!r}; declared: {self.parameters}"
            )
        self._ranges[parameter] = list(values)
        return self

    def glitch_values(self) -> Iterator[dict]:
        """Yield one dict per point in the cartesian product of the ranges.
        Also stashes it as the 'current' point so :meth:`add` can tag the
        result without you repeating the params."""
        for combo in itertools.product(*(self._ranges[n] for n in self.parameters)):
            self._current = dict(zip(self.parameters, combo))
            yield dict(self._current)

    __iter__ = glitch_values

    def add(self, group: str, **extra: Any) -> None:
        """Record the current attempt's outcome ``group`` (plus any extra
        columns, e.g. the raw target response)."""
        self._records.append({**self._current, **extra, "group": group})

    def results_df(self):
        """All recorded attempts as a DataFrame (columns: the parameters,
        any extras, and ``group``)."""
        import pandas as pd  # noqa: PLC0415 — optional [notebook] dep

        return pd.DataFrame(self._records)

    def counts(self) -> Counter:
        """Tally of attempts per group."""
        return Counter(r["group"] for r in self._records)

    def plot(self, x: str | None = None, y: str | None = None, **kw):
        """Glitch map of the results, coloured by group. ``x``/``y`` default
        to the first two parameters; pass others to view a different plane
        of a 3+ parameter sweep."""
        from .plotting import glitch_map  # noqa: PLC0415

        x = x or self.parameters[0]
        y = y or (
            self.parameters[1] if len(self.parameters) > 1 else self.parameters[0]
        )
        return glitch_map(self.results_df(), "group", x=x, y=y, **kw)

    def __len__(self) -> int:
        return len(self._records)

    def __repr__(self) -> str:
        return (
            f"GlitchController(parameters={self.parameters}, "
            f"attempts={len(self._records)}, counts={dict(self.counts())})"
        )
