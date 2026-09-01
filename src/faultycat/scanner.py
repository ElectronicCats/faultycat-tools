"""Typed facade over faultycmd's ScannerClient — ``cat.scanner``.

The scanner CDC is a text shell (not the binary framing the EMFI/crowbar
engines use), so the underlying client returns lists of raw reply lines.
This facade turns those into small result dataclasses (parsed pins /
addresses) while keeping the raw lines available.

Methods are feature-gated: older faultycmd builds ship a leaner
ScannerClient (SWD only), so ``i2c()`` / ``i2c_probe()`` / ``logic()``
raise a clear error naming the missing capability instead of an opaque
AttributeError.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ._compat import (
    ScannerClient,
    has_method,
    parse_i2c_probe_ok,
    parse_scan_i2c_match,
    parse_scan_swd_match,
)
from ._html import rows_to_html


@dataclass
class SwdScanResult:
    matched: bool
    swclk_gp: int | None = None
    swdio_gp: int | None = None
    lines: list[str] = field(default_factory=list)

    def _repr_html_(self) -> str:
        rows = [
            ("matched", str(self.matched)),
            ("SWCLK", f"GP{self.swclk_gp}" if self.swclk_gp is not None else "-"),
            ("SWDIO", f"GP{self.swdio_gp}" if self.swdio_gp is not None else "-"),
        ]
        return rows_to_html("scan · swd", rows)


@dataclass
class I2cScanResult:
    matched: bool
    sda_gp: int | None = None
    scl_gp: int | None = None
    addresses: list[int] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    @property
    def addresses_hex(self) -> list[str]:
        return [f"0x{a:02X}" for a in self.addresses]

    def _repr_html_(self) -> str:
        rows = [
            ("matched", str(self.matched)),
            ("SDA", f"GP{self.sda_gp}" if self.sda_gp is not None else "-"),
            ("SCL", f"GP{self.scl_gp}" if self.scl_gp is not None else "-"),
            ("addresses", ", ".join(self.addresses_hex) or "-"),
        ]
        return rows_to_html("scan · i2c", rows)


class ScannerEngine:
    """SWD / I2C / logic-analyzer probing over the scanner shell."""

    def __init__(self, client: ScannerClient) -> None:
        self._c = client

    @property
    def client(self) -> ScannerClient:
        """The underlying faultycmd ScannerClient, for advanced verbs
        (buspirate_enter, serprog_enter, raw send_line, ...)."""
        return self._c

    # -- SWD ----------------------------------------------------------

    def swd(
        self,
        targetsel_hex: str | None = None,
        timeout_s: float = 30.0,
        on_progress: Callable[[str], None] | None = None,
    ) -> SwdScanResult:
        """Scan for the target's SWD pinout (SWCLK/SWDIO)."""
        lines = self._c.scan_swd(targetsel_hex=targetsel_hex, timeout_s=timeout_s, on_progress=on_progress)
        parsed = parse_scan_swd_match(lines)
        if parsed is None:
            return SwdScanResult(matched=False, lines=lines)
        swclk, swdio = parsed
        return SwdScanResult(matched=True, swclk_gp=swclk, swdio_gp=swdio, lines=lines)

    # -- I2C ----------------------------------------------------------

    def i2c(
        self,
        timeout_s: float = 30.0,
        on_progress: Callable[[str], None] | None = None,
    ) -> I2cScanResult:
        """Auto-discover SDA/SCL across the header and list ACKed
        addresses. Requires a faultycmd build with I2C scanning."""
        self._require("scan_i2c", "I2C scanning")
        lines = self._c.scan_i2c(timeout_s=timeout_s, on_progress=on_progress)
        parsed = parse_scan_i2c_match(lines) if parse_scan_i2c_match else None
        if parsed is None:
            return I2cScanResult(matched=False, lines=lines)
        sda, scl, addrs = parsed
        return I2cScanResult(matched=True, sda_gp=sda, scl_gp=scl, addresses=addrs, lines=lines)

    def i2c_probe(self, sda: int, scl: int, timeout_s: float = 5.0) -> list[int]:
        """Rescan known SDA/SCL pins; return the list of ACKed addresses."""
        self._require("i2c_probe", "I2C probe")
        lines = self._c.i2c_probe(sda, scl, timeout_s=timeout_s)
        return parse_i2c_probe_ok(lines) or [] if parse_i2c_probe_ok else []

    # -- Logic analyzer ----------------------------------------------

    def logic(
        self,
        interval_us: int = 1,
        max_samples: int = 1024,
        *,
        binary: bool = True,
        trigger_ch: int | None = None,
        trigger_timeout_ms: int | None = None,
        timeout_s: float = 10.0,
    ):
        """Capture a raw GP0..GP7 logic trace. Returns faultycmd's
        ``LaCapture`` (``.interval_us``, ``.samples``, ``.overflow``).
        Use :func:`faultycat.logic_channels` to unpack per channel."""
        self._require("la", "logic-analyzer capture")
        return self._c.la(
            interval_us,
            max_samples,
            timeout_s=timeout_s,
            binary=binary,
            trigger_ch=trigger_ch,
            trigger_timeout_ms=trigger_timeout_ms,
        )

    # -- internals ----------------------------------------------------

    def _require(self, method: str, label: str) -> None:
        if not has_method(self._c, method):
            raise NotImplementedError(
                f"{label} is not available in the installed faultycmd "
                f"(ScannerClient has no '{method}'). Upgrade faultycmd."
            )

    def __repr__(self) -> str:
        caps = [m for m in ("scan_swd", "scan_i2c", "i2c_probe", "la", "uart_enter") if has_method(self._c, m)]
        return f"ScannerEngine(capabilities={caps})"

    def _repr_html_(self) -> str:
        rows = [
            ("swd scan", "✓" if has_method(self._c, "scan_swd") else "—"),
            ("i2c scan", "✓" if has_method(self._c, "scan_i2c") else "—"),
            ("i2c probe", "✓" if has_method(self._c, "i2c_probe") else "—"),
            ("logic analyzer", "✓" if has_method(self._c, "la") else "—"),
            ("target uart", "✓" if has_method(self._c, "uart_enter") else "—"),
        ]
        return rows_to_html("scanner", rows)
