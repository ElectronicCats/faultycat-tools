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

Resume after a power loss
-------------------------
Pass ``checkpoint=`` a file path and every recorded attempt is appended to
that file on disk (flushed + ``fsync``'d, so it survives a power cut of the
host *or* the FaultyCat). Re-running the same cell/script reloads what was
done and :meth:`glitch_values` skips the points already completed, so the
sweep carries on where it stopped instead of starting over.

    gc = fc.GlitchController(["x", "y", "delay"], checkpoint="runs/map.jsonl")
    gc.set_range("x", range(0, 10)).set_range("y", range(0, 10)).set_range("delay", range(0, 500, 20))
    for p in gc.glitch_values():       # already-done points are skipped on resume
        ...
        gc.add("success" if hit else "normal", count=cnt)

The checkpoint file is JSON-lines: a first ``{"_meta": ...}`` line with the
sweep definition, then one line per attempt (the same dict :meth:`add`
records). A half-written final line (power lost mid-append) is detected and
ignored on load.
"""

from __future__ import annotations

import itertools
import json
import os
import time
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

CHECKPOINT_VERSION = 1


class GlitchController:
    """Iterate parameters, classify attempts, collect + plot results."""

    def __init__(
        self,
        parameters: Iterable[str],
        groups: Iterable[str] = ("success", "reset", "normal"),
        checkpoint: str | os.PathLike | None = None,
        resume: bool = True,
        fsync: bool = True,
    ) -> None:
        self.parameters = list(parameters)
        self.groups = list(groups)
        self._ranges: dict[str, list] = {p: [0] for p in self.parameters}
        self._records: list[dict] = []
        self._current: dict[str, Any] = {}
        # --- checkpoint / resume state ---
        self._ckpt_path: Path | None = None
        self._ckpt_fh = None
        self._fsync = fsync
        self._done: set[tuple] = set()   # param-value tuples already recorded
        self._meta_written = False
        if checkpoint is not None:
            self.open_checkpoint(checkpoint, resume=resume)

    def set_range(self, parameter: str, values: Iterable) -> GlitchController:
        """Set the values swept for ``parameter`` (any iterable: a
        ``range``, a list, ...). Returns self so calls chain."""
        if parameter not in self.parameters:
            raise KeyError(
                f"unknown parameter {parameter!r}; declared: {self.parameters}"
            )
        self._ranges[parameter] = list(values)
        return self

    # -- checkpoint / resume --------------------------------------------------

    def open_checkpoint(
        self, path: str | os.PathLike, resume: bool = True
    ) -> GlitchController:
        """Persist every attempt to ``path`` (JSON-lines) as it is recorded.

        If ``resume`` and the file already holds attempts, they are loaded
        back (so results/plots include them) and their points are marked done
        so :meth:`glitch_values` skips them. Returns self so calls chain."""
        self._ckpt_path = Path(path)
        self._ckpt_path.parent.mkdir(parents=True, exist_ok=True)
        if resume and self._ckpt_path.exists():
            self._load_checkpoint()
        # Append mode: keep anything already there, add new attempts after.
        self._ckpt_fh = open(self._ckpt_path, "a", encoding="utf-8")
        self._meta_written = self._ckpt_path.stat().st_size > 0
        return self

    def _load_checkpoint(self) -> None:
        loaded, done = [], set()
        with open(self._ckpt_path, encoding="utf-8") as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # Only tolerate a corrupt *last* line (power lost mid-write).
                if i == len(lines) - 1:
                    break
                raise
            if isinstance(obj, dict) and "_meta" in obj:
                meta = obj["_meta"]
                saved = list(meta.get("parameters", []))
                if saved and saved != self.parameters:
                    raise ValueError(
                        f"checkpoint {self._ckpt_path} is for parameters {saved}, "
                        f"not {self.parameters}; use a different file"
                    )
                # Restore the grid so resume reproduces the original sweep even
                # before set_range() is called again (set_range may still override).
                for name, vals in meta.get("ranges", {}).items():
                    if name in self._ranges:
                        self._ranges[name] = list(vals)
                continue
            loaded.append(obj)
            done.add(self._key(obj))
        self._records.extend(loaded)
        self._done |= done

    def _key(self, point: dict) -> tuple:
        """Stable identity of a sweep point: its parameter values in order."""
        return tuple(point[p] for p in self.parameters)

    def _ensure_meta(self) -> None:
        if self._ckpt_fh is None or self._meta_written:
            return
        meta = {
            "_meta": {
                "version": CHECKPOINT_VERSION,
                "parameters": self.parameters,
                "groups": self.groups,
                "ranges": {n: list(v) for n, v in self._ranges.items()},
                "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        }
        self._write_line(meta)
        self._meta_written = True

    def _write_line(self, obj: dict) -> None:
        self._ckpt_fh.write(json.dumps(obj) + "\n")
        self._ckpt_fh.flush()
        if self._fsync:
            os.fsync(self._ckpt_fh.fileno())  # survive a power cut, not just a crash

    def close(self) -> None:
        """Flush and close the checkpoint file (safe to call repeatedly)."""
        if self._ckpt_fh is not None:
            try:
                self._ckpt_fh.flush()
                if self._fsync:
                    os.fsync(self._ckpt_fh.fileno())
            finally:
                self._ckpt_fh.close()
                self._ckpt_fh = None

    def __enter__(self) -> GlitchController:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- sweep ----------------------------------------------------------------

    def glitch_values(self) -> Iterator[dict]:
        """Yield one dict per point in the cartesian product of the ranges,
        skipping points already recorded (so a checkpointed run resumes where
        it stopped). Also stashes the point as 'current' so :meth:`add` can
        tag the result without you repeating the params."""
        for combo in itertools.product(*(self._ranges[n] for n in self.parameters)):
            if combo in self._done:
                continue
            self._current = dict(zip(self.parameters, combo))
            yield dict(self._current)

    __iter__ = glitch_values

    def total_points(self) -> int:
        """Size of the full parameter grid (done + remaining)."""
        n = 1
        for name in self.parameters:
            n *= len(self._ranges[name])
        return n

    def done_count(self) -> int:
        """Points already recorded (loaded from the checkpoint + this run)."""
        return len(self._done)

    def remaining_points(self) -> int:
        """Points :meth:`glitch_values` will still yield (for a progress bar's
        ``total`` on resume)."""
        remaining = 0
        for combo in itertools.product(*(self._ranges[n] for n in self.parameters)):
            if combo not in self._done:
                remaining += 1
        return remaining

    def add(self, group: str, **extra: Any) -> None:
        """Record the current attempt's outcome ``group`` (plus any extra
        columns, e.g. the raw target response). If a checkpoint is open the
        record is appended to disk (flushed + fsync'd) before returning."""
        record = {**self._current, **extra, "group": group}
        self._records.append(record)
        if self._current:
            self._done.add(self._key(self._current))
        if self._ckpt_fh is not None:
            self._ensure_meta()
            self._write_line(record)

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
        ckpt = f", checkpoint={self._ckpt_path.name!r}" if self._ckpt_path else ""
        return (
            f"GlitchController(parameters={self.parameters}, "
            f"attempts={len(self._records)}, counts={dict(self.counts())}{ckpt})"
        )
