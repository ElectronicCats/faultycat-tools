"""Notebook plotting helpers (matplotlib + pandas).

All heavy deps (numpy/pandas/matplotlib) are imported lazily so the
core ``faultycat`` install stays lean; install the ``[notebook]`` extra
to use anything here. A ``plotly`` glitch-map is offered separately for
callers who want interactivity (``[interactive]`` extra).

The two signature visuals for fault injection:

  * ``plot_trace(trace)``  — the ADC capture from an EMFI shot.
  * ``glitch_map(df)``     — delay x width, colored by outcome. This is
    the classic "glitch map" that turns a campaign sweep into a picture
    of where the target faults.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

# Fields on faultycmd's CampaignResult dataclass we surface as columns.
_RESULT_FIELDS = (
    "step_n",
    "delay",
    "width",
    "power",
    "fire_status",
    "verify_status",
    "target_state",
    "ts_us",
)


def results_to_dataframe(results: Iterable[Any]) -> pd.DataFrame:
    """Turn a list of faultycmd ``CampaignResult`` into a DataFrame of the
    raw firmware fields (``fire_status``, ``verify_status``,
    ``target_state``, ...).

    There is deliberately **no ``success`` column** — whether a glitch
    "worked" is target-specific and only you can define it. Build it from
    the raw fields and pass it to :func:`glitch_map`, e.g.::

        success = df.verify_status != 0     # or df.target_state == 2, or your UART check
        fc.glitch_map(df, success)
    """
    import pandas as pd  # noqa: PLC0415 — optional [notebook] dep

    rows = [{f: getattr(r, f, None) for f in _RESULT_FIELDS} for r in results]
    return pd.DataFrame(rows, columns=list(_RESULT_FIELDS))


def plot_trace(trace: Sequence[int] | bytes, *, ax=None, title: str = "EMFI capture"):
    """Line-plot an ADC capture buffer. Returns the matplotlib Axes."""
    import matplotlib.pyplot as plt  # noqa: PLC0415 — optional [notebook] dep
    import numpy as np  # noqa: PLC0415

    y = np.frombuffer(trace, dtype=np.uint8) if isinstance(trace, (bytes, bytearray)) else np.asarray(trace)
    if ax is None:
        _fig, ax = plt.subplots(figsize=(9, 3))
    ax.plot(y, linewidth=0.8)
    ax.set_xlabel("sample")
    ax.set_ylabel("ADC (0-255)")
    ax.set_title(title)
    ax.margins(x=0)
    return ax


# Known outcome-group colours; anything else cycles through _CYCLE.
_GROUP_COLORS = {"success": "#2ca02c", "hit": "#2ca02c", "reset": "#d62728",
                 "normal": "#c9c9c9", "no effect": "#c9c9c9"}
_CYCLE = ["#1f77b4", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2", "#17becf"]
# Draw order: greys first (bottom), successes last (on top). Others middle.
_RANK = {"no effect": 0, "normal": 0, "reset": 1, "hit": 2, "success": 2}


def glitch_map(
    df: pd.DataFrame,
    group: Any,
    *,
    x: str = "delay",
    y: str = "width",
    ax=None,
    title: str = "Glitch map",
):
    """Scatter every attempt at (x, y), coloured by the outcome YOU assigned.

    ``group`` is your classification — the tool never guesses it. Pass:
      - a categorical Series / column name (``'group'`` with values like
        ``success`` / ``reset`` / ``normal``) → one colour per group, or
      - a boolean Series / column → success vs no-effect.

    ``x`` / ``y`` pick which two swept dimensions to view; a 3+ parameter
    sweep can be plotted as delay×width, delay×power, etc. by re-calling
    with different ``x`` / ``y``. Known group names get fixed colours
    (success=green, reset=red, normal=grey); others cycle.
    """
    import matplotlib.pyplot as plt  # noqa: PLC0415 — optional [notebook] dep

    g = df[group] if isinstance(group, str) else group
    if g.dtype == bool:
        g = g.map({True: "success", False: "no effect"})
    if ax is None:
        _fig, ax = plt.subplots(figsize=(7, 5))
    cyc = iter(_CYCLE)
    for val in sorted(g.unique(), key=lambda v: _RANK.get(v, 1)):
        sub = df[g == val]
        color = _GROUP_COLORS.get(val, next(cyc))
        ax.scatter(sub[x], sub[y], s=18, c=color, label=str(val), edgecolors="none")
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    ax.set_title(title)
    ax.legend(loc="upper right", frameon=False)
    return ax


def logic_channels(capture: Any):
    """Unpack a faultycmd ``LaCapture`` into an 8xN uint8 array.

    Row ``c`` is channel GP\\ *c* over time (1=high, 0=low); column ``i``
    is sample ``i``, which occurred at ``i * capture.interval_us`` us.
    Accepts a ``LaCapture``, or raw ``bytes`` (one byte per sample).
    """
    import numpy as np  # noqa: PLC0415 — optional [notebook] dep

    raw = getattr(capture, "samples", capture)
    buf = np.frombuffer(raw, dtype=np.uint8)
    return np.array([(buf >> c) & 1 for c in range(8)], dtype=np.uint8)


def plot_logic(capture: Any, channels: Sequence[int] | None = None, *, title: str = "Logic capture"):
    """Step-plot logic-analyzer channels stacked vertically (like a
    scope/PulseView). x-axis is time in microseconds."""
    import matplotlib.pyplot as plt  # noqa: PLC0415 — optional [notebook] dep
    import numpy as np  # noqa: PLC0415

    bits = logic_channels(capture)
    interval = getattr(capture, "interval_us", 1)
    chans = list(channels) if channels is not None else list(range(8))
    t = np.arange(bits.shape[1]) * interval
    fig, axes = plt.subplots(len(chans), 1, sharex=True, figsize=(9, 0.6 * len(chans) + 1))
    if len(chans) == 1:
        axes = [axes]
    for ax, c in zip(axes, chans):
        ax.step(t, bits[c], where="post", linewidth=0.9)
        ax.set_ylim(-0.2, 1.2)
        ax.set_yticks([])
        ax.set_ylabel(f"GP{c}", rotation=0, ha="right", va="center")
        ax.margins(x=0)
    axes[0].set_title(title)
    axes[-1].set_xlabel("time (us)")
    return axes


def glitch_map_plotly(
    df: pd.DataFrame,
    group: Any,
    *,
    x: str = "delay",
    y: str = "width",
):
    """Interactive glitch map (hover shows exact params). ``group`` is your
    classification (categorical or bool Series / column name), same as
    :func:`glitch_map`. Needs the ``[interactive]`` extra (plotly)."""
    import plotly.express as px  # noqa: PLC0415 — optional [interactive] dep

    g = df[group] if isinstance(group, str) else group
    color = g.map({True: "success", False: "no effect"}) if g.dtype == bool else g
    return px.scatter(
        df,
        x=x,
        y=y,
        color=color,
        color_discrete_map=_GROUP_COLORS,
        hover_data=[c for c in _RESULT_FIELDS if c in df.columns],
        title="Glitch map",
    )
