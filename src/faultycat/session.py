"""The top-level ``FaultyCat`` session and the ``connect()`` factory.

Unlike the CLI (where each command opens/closes a client for one shot),
a notebook wants ONE long-lived session that holds all the CDC ports
open. ``FaultyCat`` owns that lifecycle: it discovers/opens the engine
clients on connect and closes them on ``close()`` / context exit.
"""

from __future__ import annotations

from ._compat import (
    CampaignClient,
    CrowbarClient,
    EmfiClient,
    ScannerClient,
)
from .engines import CampaignRunner, CrowbarEngine, EmfiEngine
from .scanner import ScannerEngine
from .uart import UartTarget


class FaultyCat:
    """A live connection to a FaultyCat v3 board.

    Access engines as attributes:

        cat = fc.connect()
        cat.emfi          # EmfiEngine   (None if that CDC was absent)
        cat.crowbar       # CrowbarEngine
        cat.scanner       # raw faultycmd ScannerClient (SWD/I2C/LA)
        camp = cat.campaign("emfi")   # a fresh CampaignRunner

    Use as a context manager to auto-close, or call ``close()``.
    """

    def __init__(
        self,
        emfi: EmfiEngine | None = None,
        crowbar: CrowbarEngine | None = None,
        scanner: ScannerEngine | None = None,
        uart: UartTarget | None = None,
        *,
        _clients: list | None = None,
        allow_version_mismatch: bool = False,
        serial_factory=None,
        connect_cfg: dict | None = None,
    ) -> None:
        self.emfi = emfi
        self.crowbar = crowbar
        self.scanner = scanner
        self.uart = uart
        self._open_clients = _clients or []
        self._allow_version_mismatch = allow_version_mismatch
        self._serial_factory = serial_factory
        # The kwargs connect() was called with, so reconnect() can replay
        # the exact same discovery after the board re-enumerates.
        self._connect_cfg = connect_cfg or {}

    # -- campaigns ----------------------------------------------------

    def campaign(self, engine: str = "emfi") -> CampaignRunner:
        """Open a fresh campaign runner on the given engine ("emfi" or
        "crowbar"). Campaigns share the engine's CDC, so this opens its
        own client for the duration of the sweep."""
        if engine not in ("emfi", "crowbar"):
            raise ValueError(f"engine must be 'emfi' or 'crowbar', got {engine!r}")
        if self._serial_factory is not None:
            client = CampaignClient(
                f"sim://campaign-{engine}",
                engine=engine,
                check_firmware_version=False,
                serial_factory=self._serial_factory,
            )
        else:
            check = not self._allow_version_mismatch
            client = CampaignClient.discover(engine=engine, check_firmware_version=check)
        client.open()
        self._open_clients.append(client)
        return CampaignRunner(client)

    # -- target reset -------------------------------------------------

    def target_reset(self, gp: int, ms: int = 10) -> str:
        """Pulse the target's reset line (active-low) on GPIO ``gp`` for
        ``ms`` milliseconds, via the scanner shell.

        Call this between glitch attempts so a crashed/glitched target
        starts each try from a clean state — the same discipline
        ChipWhisperer's loops use. Wire FaultyCat ``gp`` to the target's
        nRST (a plain GPIO pulse; no SWD needed).
        """
        if self.scanner is None:
            raise RuntimeError("target_reset needs the scanner CDC (connect with scanner=True)")
        line = self.scanner.client.send_line(
            f"reset {int(gp)} {int(ms)}", accept_prefixes=("RESET:", "SHELL:")
        )
        if line.startswith("SHELL:") or "OK" not in line:
            raise RuntimeError(
                f"target reset unavailable: {line!r} — needs firmware with the "
                "'reset' verb (rebuild + reflash faultycat-firmware)"
            )
        return line

    # -- resilience ---------------------------------------------------

    def reconnect(self, *, attempts: int = 8, wait: float = 1.0) -> FaultyCat:
        """Re-discover and reopen every CDC by VID:PID after the board
        re-enumerated, then re-adopt the fresh handles in place.

        An EMFI shot's EMI can drop FaultyCat off USB and bring it back
        on *new* device nodes (e.g. EMFI Control ``ttyACM0`` -> ``ttyACM1``),
        leaving the handles this session holds dead — calls then error or
        hang and the campaign stalls with the board idle. Call this on
        failure to reopen on the new nodes and carry on. Engine settings
        (trigger/delay/width/repeat) and an open ``cat.uart`` are restored.

        Retries ``attempts`` times, ``wait`` seconds apart, to ride out
        the enumeration gap. Raises ``ConnectionError`` if it never
        comes back. A no-op for a simulator session.
        """
        import time  # noqa: PLC0415

        if self._serial_factory is not None:
            return self  # simulator: nothing to physically re-enumerate

        emfi_snap = _snapshot_engine(self.emfi)
        crowbar_snap = _snapshot_engine(self.crowbar)
        uart_was_open = self.uart is not None and self.uart._ser is not None
        uart_baud = self.uart._baud if self.uart is not None else 115200

        self.close()  # drop the dead handles before re-discovering

        last: Exception | None = None
        for _ in range(attempts):
            try:
                fresh = connect(**{**self._connect_cfg, "require": True})
            except Exception as exc:  # noqa: BLE001 — retry through the gap
                last = exc
                time.sleep(wait)
                continue
            self.emfi, self.crowbar = fresh.emfi, fresh.crowbar
            self.scanner, self.uart = fresh.scanner, fresh.uart
            self._open_clients = fresh._open_clients
            self._serial_factory = fresh._serial_factory
            _restore_engine(self.emfi, emfi_snap)
            _restore_engine(self.crowbar, crowbar_snap)
            if uart_was_open and self.uart is not None:
                try:
                    self.uart.open(baud=uart_baud)
                except Exception:  # noqa: BLE001 — bridge re-opens best-effort
                    pass
            return self
        raise ConnectionError(f"FaultyCat did not re-enumerate after {attempts} attempts: {last}")

    # -- lifecycle ----------------------------------------------------

    def close(self) -> None:
        """Close every open CDC handle this session holds."""
        if self.uart is not None:
            try:
                self.uart.close()
            except Exception:  # noqa: BLE001 — best-effort teardown
                pass
        for c in self._open_clients:
            try:
                c.close()
            except Exception:  # noqa: BLE001 — best-effort teardown
                pass
        self._open_clients.clear()
        self.emfi = self.crowbar = self.scanner = self.uart = None

    def __enter__(self) -> FaultyCat:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        present = [
            n for n in ("emfi", "crowbar", "scanner", "uart") if getattr(self, n) is not None
        ]
        return f"FaultyCat(connected={present or 'none'})"

    def _repr_html_(self) -> str:
        rows = []
        for name in ("emfi", "crowbar", "scanner", "uart"):
            rows.append((name, "✓ ready" if getattr(self, name) is not None else "— absent"))
        from ._html import rows_to_html

        return rows_to_html("FaultyCat session", rows)


def connect(
    *,
    emfi_port: str | None = None,
    crowbar_port: str | None = None,
    scanner_port: str | None = None,
    target_port: str | None = None,
    scanner: bool = True,
    uart: bool = True,
    simulator: bool = False,
    serial_factory=None,
    allow_version_mismatch: bool = False,
    require: bool = True,
) -> FaultyCat:
    """Discover and open a FaultyCat session.

    By default this auto-discovers each CDC by VID:PID (via faultycmd's
    ``.discover()``) and opens EMFI, crowbar, and scanner. Pass explicit
    ``*_port`` names to bypass discovery for a specific engine.

    Args:
        emfi_port / crowbar_port / scanner_port: explicit device names
            (e.g. "/dev/ttyACM0", "COM5"). None -> auto-discover.
        target_port: explicit data port for ``cat.uart`` (the target-UART
            data CDC). None -> auto-discover when first opened.
        scanner: open the scanner-shell CDC (SWD/I2C/logic-analyzer). The
            target UART shares this control shell, so ``uart`` needs it.
        uart: expose ``cat.uart`` (needs ``scanner``). Bytes only flow
            after ``cat.uart.open()``.
        simulator: drive an in-memory :class:`FaultyCatSimulator` instead
            of real serial ports — a whole session with no board. Forces
            the version check off and ignores the ``*_port`` args.
        serial_factory: advanced — inject your own ``(port, baud, timeout)
            -> serial-like`` factory (e.g. a custom mock). ``simulator=
            True`` is a shortcut that supplies one.
        allow_version_mismatch: skip the host<->firmware version check.
        require: raise if NO engine could be opened. Set False to get a
            partial (or empty) session, e.g. for docs without hardware.

    Returns:
        A live :class:`FaultyCat`.
    """
    if simulator and serial_factory is None:
        from .simulator import FaultyCatSimulator  # noqa: PLC0415

        serial_factory = FaultyCatSimulator().factory
    sim = serial_factory is not None
    check = not (allow_version_mismatch or sim)
    opened: list = []
    errors: dict[str, Exception] = {}

    emfi = _open(EmfiClient, emfi_port, check, opened, errors, "emfi", serial_factory)
    crowbar = _open(CrowbarClient, crowbar_port, check, opened, errors, "crowbar", serial_factory)

    scan_client = None
    if scanner:
        scan_client = _open(
            ScannerClient,
            scanner_port,
            check,
            opened,
            errors,
            "scanner",
            serial_factory,
        )

    if require and not opened:
        detail = "; ".join(f"{k}: {v}" for k, v in errors.items()) or "no FaultyCat found"
        raise ConnectionError(f"could not open any FaultyCat CDC ({detail})")

    # The target UART reuses the scanner client for its control shell and
    # opens its own data CDC lazily on cat.uart.open().
    uart_target = (
        UartTarget(scan_client, data_port=target_port, serial_factory=serial_factory)
        if (uart and scan_client)
        else None
    )

    return FaultyCat(
        emfi=EmfiEngine(emfi) if emfi else None,
        crowbar=CrowbarEngine(crowbar) if crowbar else None,
        scanner=ScannerEngine(scan_client) if scan_client else None,
        uart=uart_target,
        _clients=opened,
        allow_version_mismatch=allow_version_mismatch,
        serial_factory=serial_factory,
        connect_cfg={
            "emfi_port": emfi_port,
            "crowbar_port": crowbar_port,
            "scanner_port": scanner_port,
            "target_port": target_port,
            "scanner": scanner,
            "uart": uart,
            "simulator": simulator,
            "serial_factory": serial_factory,
            "allow_version_mismatch": allow_version_mismatch,
        },
    )


def _snapshot_engine(engine) -> dict | None:
    """Capture an engine's user-set attributes (everything but its client
    ``_c``) so they survive a reconnect that swaps in a fresh client."""
    if engine is None:
        return None
    return {k: v for k, v in engine.__dict__.items() if k != "_c"}


def _restore_engine(engine, snap: dict | None) -> None:
    if engine is None or not snap:
        return
    for k, v in snap.items():
        setattr(engine, k, v)


def _open(client_cls, port, check, opened, errors, name, serial_factory=None):
    """Discover-or-construct a client and open it, recording failures
    instead of raising so one missing interface doesn't sink the whole
    session."""
    try:
        if serial_factory is not None:
            client = client_cls(
                port or f"sim://{name}",
                check_firmware_version=check,
                serial_factory=serial_factory,
            )
        elif port is not None:
            client = client_cls(port, check_firmware_version=check)
        else:
            client = client_cls.discover(check_firmware_version=check)
        client.open()
        opened.append(client)
        return client
    except Exception as exc:  # noqa: BLE001 — per-engine best effort
        errors[name] = exc
        return None
