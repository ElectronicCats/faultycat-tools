"""faultycat — a notebook-first control layer for the FaultyCat v3 board.

    import faultycat as fc

    cat = fc.connect()                 # discover + open all CDC interfaces
    cat.emfi.trigger = "ext_rising"
    cat.emfi.delay_us = 100
    cat.emfi.width_us = 10
    cat.emfi.glitch()                  # apply -> arm -> fire
    fc.plot_trace(cat.emfi.capture())

    camp = cat.campaign("emfi").configure(delay=(0, 200, 10), width=(1, 50, 1))
    results = camp.run()               # tqdm progress in Jupyter
    fc.glitch_map(camp.results_df())   # the classic glitch map

This package is a thin ergonomics layer. The wire protocol, framing,
and USB discovery all live in ``faultycmd`` (the host CLI/TUI), which
this package reuses — there is exactly one source of truth for the
FaultyCat protocol.
"""

from __future__ import annotations

from pathlib import Path

# Re-export the enums so notebook users can `fc.EmfiTrigger.EXT_RISING`
# without importing faultycmd directly.
from ._compat import (
    CrowbarOutput,
    CrowbarTrigger,
    EmfiState,
    EmfiTrigger,
    EngineError,
    ProtocolError,
)
from .control import GlitchController
from .engines import CampaignRunner, CrowbarEngine, EmfiEngine
from .plotting import (
    glitch_map,
    glitch_map_plotly,
    logic_channels,
    plot_logic,
    plot_trace,
    results_to_dataframe,
)
from .scanner import I2cScanResult, ScannerEngine, SwdScanResult
from .session import FaultyCat, connect
from .uart import UartTarget


def _read_version() -> str:
    try:
        return (
            (Path(__file__).resolve().parent.parent.parent / "VERSION")
            .read_text()
            .strip()
        )
    except OSError:
        return "0.0.0"


__version__ = _read_version()

__all__ = [
    "connect",
    "FaultyCat",
    "EmfiEngine",
    "CrowbarEngine",
    "CampaignRunner",
    "GlitchController",
    "ScannerEngine",
    "SwdScanResult",
    "I2cScanResult",
    "UartTarget",
    "EmfiTrigger",
    "EmfiState",
    "CrowbarTrigger",
    "CrowbarOutput",
    "EngineError",
    "ProtocolError",
    "plot_trace",
    "glitch_map",
    "glitch_map_plotly",
    "logic_channels",
    "plot_logic",
    "results_to_dataframe",
    "__version__",
]
