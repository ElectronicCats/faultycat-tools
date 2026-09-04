"""Thin, version-tolerant bridge to the ``faultycmd`` protocol layer.

Everything the notebook API needs from the host tool is imported here,
in ONE place, so the rest of ``faultycat`` never reaches into
``faultycmd`` internals directly. We only ever import from:

  * ``faultycmd.protocols``           (stable client re-exports)
  * ``faultycmd.protocols.{emfi,crowbar,campaign,scanner}`` (enums)

These paths exist in every faultycmd we support. We deliberately avoid
``faultycmd.core.*`` / ``faultycmd.usb`` etc. because they are internal
and a refactor can move them (a build may lay them out flat under
``faultycmd.*`` or nested under ``faultycmd.core.*``); depending on them
would risk breaking ``faultycat`` on a faultycmd upgrade.
"""

from __future__ import annotations

from faultycmd.protocols import (  # noqa: F401 — re-exported for the package
    CampaignClient,
    CampaignResult,
    CampaignStatus,
    CrowbarClient,
    CrowbarStatus,
    EmfiClient,
    EmfiStatus,
    EngineError,
    ProtocolError,
    ScannerClient,
)
from faultycmd.protocols.crowbar import CrowbarOutput, CrowbarTrigger
from faultycmd.protocols.emfi import EmfiState, EmfiTrigger

# ``parse_scan_swd_match`` exists in every faultycmd; the I2C/probe
# parsers only in newer ones. Import defensively so an older faultycmd
# still loads (the ScannerEngine feature-gates the methods that need
# them).
from faultycmd.protocols.scanner import parse_scan_swd_match  # noqa: F401

try:  # optional (newer faultycmd)
    from faultycmd.protocols.scanner import (  # noqa: F401
        parse_i2c_probe_ok,
        parse_scan_i2c_match,
    )
except ImportError:  # pragma: no cover - depends on installed faultycmd
    parse_scan_i2c_match = None  # type: ignore[assignment]
    parse_i2c_probe_ok = None  # type: ignore[assignment]


def resolve_cdc_for():
    """Return faultycmd's ``cdc_for(role)`` helper, tolerating either
    module layout (``faultycmd.core.usb`` or ``faultycmd.usb``). Returns
    ``None`` if neither is importable."""
    import importlib  # noqa: PLC0415

    for modname in ("faultycmd.core.usb", "faultycmd.usb"):
        try:
            mod = importlib.import_module(modname)
        except ImportError:
            continue
        fn = getattr(mod, "cdc_for", None)
        if fn is not None:
            return fn
    return None


def resolve_framing():
    """Return faultycmd's framing module (``build_frame``, ``SOF``, ...),
    tolerating either module layout (``faultycmd.core.framing`` or
    ``faultycmd.framing``). Used by the simulator to build valid CRC
    frames. Returns ``None`` if neither is importable."""
    import importlib  # noqa: PLC0415

    for modname in ("faultycmd.core.framing", "faultycmd.framing"):
        try:
            return importlib.import_module(modname)
        except ImportError:
            continue
    return None


def has_method(obj: object, name: str) -> bool:
    """True if ``obj`` exposes a callable ``name`` (feature-detection for
    ScannerClient methods absent in older faultycmd)."""
    return callable(getattr(obj, name, None))


__all__ = [
    "CampaignClient",
    "CampaignResult",
    "CampaignStatus",
    "CrowbarClient",
    "CrowbarStatus",
    "EmfiClient",
    "EmfiStatus",
    "EngineError",
    "ProtocolError",
    "ScannerClient",
    "CrowbarOutput",
    "CrowbarTrigger",
    "EmfiState",
    "EmfiTrigger",
    "parse_scan_swd_match",
    "parse_scan_i2c_match",
    "parse_i2c_probe_ok",
    "resolve_cdc_for",
    "has_method",
    "coerce_enum",
]


def coerce_enum(value: object, enum_cls: type, *, field: str) -> int:
    """Accept an enum member, an int, or a case-insensitive name string.

    Lets a notebook user write ``cat.emfi.trigger = "ext_rising"`` (or
    ``EmfiTrigger.EXT_RISING`` or ``1``) interchangeably. Raises a
    ``ValueError`` listing the valid names on a typo — much friendlier
    than a firmware BAD_CONFIG three calls later.
    """
    if isinstance(value, enum_cls):
        return int(value)
    if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
        raise ValueError(f"{field} cannot be a bool")
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        key = value.strip().upper().replace("-", "_").replace(" ", "_")
        members = enum_cls.__members__
        if key in members:
            return int(members[key])
        valid = ", ".join(m.lower() for m in members)
        raise ValueError(f"unknown {field} {value!r}; expected one of: {valid}")
    raise TypeError(
        f"{field} must be a str, int, or {enum_cls.__name__}, got {type(value).__name__}"
    )
