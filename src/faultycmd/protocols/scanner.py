"""CDC2 text-shell wrapper.

Consolidates ``tools/{swd,jtag,scanner}_diag.py`` into one client.
The CDC2 shell hosts F8-2 pinout scanner (``scan swd``) +
F8-4 BusPirate entry + F8-5 serprog entry + F9-3 campaign demo,
all line-buffered text. The F6 SWD sub-shell and F8-1 JTAG
sub-shell + ``scan jtag`` are WIP and hidden from this release's
public surface; the firmware responds with ``ERR wip`` on those
verbs (see ``apps/faultycat_fw/main.c``). The Python wrappers for
those WIP verbs survive as underscored methods (``_swd_*`` /
``_jtag_*`` / ``_scan_jtag``) so the v3.1 unblock can re-expose
them without re-writing the layer. Reply prefixes:

    SHELL:    top-level help / unknown command
    SWD:      F6 swd_* commands (WIP — internal use only)
    JTAG:     F8-1 jtag_* commands (WIP — internal use only)
    SCAN:     F8-2 ``scan swd`` (public); ``scan jtag`` (WIP/internal)
    BPIRATE:  F8-4 binary-mode entry confirmation
    SERPROG:  F8-5 binary-mode entry confirmation
    CAMPAIGN: F9-3 demo crowbar + status + drain + stop
    UART:     Target UART passthrough control (enter/baud/parity/
              stopbits/status/exit) — see ``uart_*`` below

This module wraps that line shell with one method per command.
Once a binary mode is entered (``buspirate_enter`` / ``serprog_enter``)
the operator is expected to switch the underlying port to
OpenOCD / flashrom / similar — this client does NOT try to drive
the binary protocol from inside Python; the F10-2 campaign module
handles its own binary surface and the BusPirate / serprog modes
have native external clients (OpenOCD, flashrom).

The Target UART passthrough (``uart enter`` et al.) is different
from BusPirate/serprog: control stays on this CDC2 text shell, but
the actual UART traffic flows on a separate CDC (CDC3, "target
uart" — see ``apps/faultycat_fw/main.c::uart_passthrough``). This
client only owns the control verbs; bridging the data CDC is the
caller's job (see ``faultycmd.core.cli``'s ``uart console``).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from ..core.usb import cdc_for


class _SerialLike(Protocol):
    def write(self, data: bytes) -> int: ...
    def read(self, size: int = 1) -> bytes: ...
    def reset_input_buffer(self) -> None: ...
    def close(self) -> None: ...


SerialFactory = Callable[[str, int, float], _SerialLike]


def _default_serial_factory(
    port: str, baud: int, per_byte_timeout: float
) -> _SerialLike:
    import serial  # noqa: PLC0415

    return serial.Serial(port, baud, timeout=per_byte_timeout)


# Prefixes the firmware uses for shell replies (frozen at F8-3).
ACCEPTED_PREFIXES: tuple[str, ...] = (
    "SHELL:",
    "SWD:",
    "JTAG:",
    "SCAN:",
    "BPIRATE:",
    "SERPROG:",
    "CAMPAIGN:",
    "UART:",
    "I2C:",
)


class ScannerError(Exception):
    """Raised when a shell reply parses cleanly but the firmware
    reports ``ERR ...`` as the verb. ``.line`` holds the full reply
    so the caller can see the error tail."""

    def __init__(self, line: str) -> None:
        self.line = line
        super().__init__(line)


@dataclass
class LaCapture:
    """Raw logic-analyzer capture from the protocol-agnostic ``la`` command.

    The firmware always snapshots the full 8-channel bank GP0..GP7 and
    streams the raw bytes verbatim — it never interprets them (see
    ``faultycat-firmware/docs/LOGIC_ANALYZER.md``). ``samples`` is one
    byte per sample, each bit a channel (bit 0 = GP0 = CH0, bit 1 = GP1
    = CH1, …; 1=high, 0=low). There's no per-sample timestamp — sample
    ``i`` occurred at ``i * interval_us``. Which channel carries which
    signal is purely a wiring convention; host-side decoders (I2C/UART/…)
    interpret whichever channels the operator wired.
    """

    interval_us: int
    samples: bytes
    overflow: bool = False
    """True if the firmware's capture ring (``LA_CAPTURE_BUFFER_BYTES``)
    lapped the read cursor while streaming — the full sample count still
    arrived, but some samples were silently skipped ahead in time, so
    ``samples`` may not be contiguous from that point on. Seen at fast
    ``--interval-us`` (e.g. the 1us default) when USB CDC can't drain the
    ring as fast as it fills, especially in hex mode (2 bytes/sample on
    the wire). Raise ``--interval-us``, pass ``binary=True``, or lower
    ``max_samples`` to avoid it."""


class ScannerClient:
    """Line-buffered CDC2 text-shell client.

    Lifecycle is the same context-manager shape as
    :class:`BinaryProtoClient`. The reply matcher is tunable per-call
    via ``accept_prefixes`` so a SWD command can ignore stray JTAG
    snapshot lines from the diag stream and vice-versa.
    """

    DEFAULT_BAUD = 115200
    DEFAULT_TIMEOUT = 3.0
    PER_BYTE_TIMEOUT = 0.2

    # Bound on the extra read-past-`needed` grace window `_capture_la_stream`
    # gives the firmware's trailing OVERFLOW marker to arrive (see there) —
    # short because the marker is only ~11 bytes written immediately after
    # the last sample, never a real wait for more capture data.
    LA_OVERFLOW_GRACE_S = 0.5

    def __init__(
        self,
        port: str,
        *,
        baud: int = DEFAULT_BAUD,
        timeout: float = DEFAULT_TIMEOUT,
        serial_factory: SerialFactory | None = None,
        check_firmware_version: bool = True,
    ) -> None:
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._factory = serial_factory or _default_serial_factory
        self._ser: _SerialLike | None = None
        self._check_firmware_version = check_firmware_version
        self.firmware_version: tuple[int, int, int, int] | None = None

    # -- lifecycle ---------------------------------------------------

    def open(self) -> None:
        if self._ser is None:
            self._ser = self._factory(self.port, self.baud, self.PER_BYTE_TIMEOUT)
            if self._check_firmware_version:
                self._probe_and_check_firmware_version()

    def _probe_and_check_firmware_version(self) -> None:
        """Send `version` to the shell and validate against host.

        Firmware emits ``SHELL: VERSION X.Y.Z.W``. Closes the serial
        on failure to keep the client in a consistent state.

        A timeout here usually means the firmware is still armed in
        the binary SUMP shell from a previous ``la sump enter`` (see
        ``la_sump_arm``) whose session was never cleanly released —
        e.g. ``faultycmd la pulseview`` was killed before it could run
        its own ``force_exit_sump()`` cleanup, or PulseView crashed.
        Under normal use ``la pulseview`` handles the SUMP hand-off and
        release itself, so hitting this means that cleanup didn't run;
        the only reliable recovery left from here is a power cycle /
        USB replug of the board, or re-running ``force_exit_sump()``
        directly against the port.
        """
        from ..utils.version_check import (  # noqa: PLC0415 — avoid import cycle
            assert_version_match,
            parse_shell_version,
        )

        try:
            try:
                line = self.send_line("version", accept_prefixes=("SHELL:",))
            except TimeoutError as e:
                raise TimeoutError(
                    "no shell reply for 'version' — the scanner port is likely "
                    "still armed in SUMP/PulseView mode from a `la pulseview` "
                    "session that didn't get to release it cleanly (e.g. "
                    "faultycmd was interrupted, or PulseView crashed). Re-run "
                    "`faultycmd la pulseview` and close PulseView normally so "
                    "the automatic release can run, or power-cycle / replug "
                    "the board to reset it."
                ) from e
            self.firmware_version = parse_shell_version(line)
            assert_version_match(self.firmware_version)
        except Exception:
            if self._ser is not None:
                self._ser.close()
                self._ser = None
            raise

    # CMD_FORCE_EXIT in the firmware's sump_ols.c — a top-level SUMP
    # command byte, never sent by sigrok's ols driver, that the parser
    # treats as "leave SUMP mode now" (mirrors buspirate_compat's own
    # 0x0F BP_CMD_USER_TERM convention). Kept in sync manually — bump
    # both if the firmware constant changes.
    SUMP_FORCE_EXIT_BYTE = 0x0F

    def force_exit_sump(self, timeout_s: float = 3.0) -> None:
        """Force the firmware out of SUMP/PulseView mode.

        Writes the SUMP exit-escape byte directly on the wire and
        waits for the firmware's "OK exited" confirmation. This
        replaces an older DTR-pulse-based implementation: closing the
        port, or otherwise dropping DTR, used to be the only way to
        signal this, but that depended on how each host OS's CDC-ACM
        driver handles DTR on close — unreliable on Windows in
        particular (see docs/WINDOWS_SUMP_DTR_ISSUE.md). Sending an
        explicit protocol byte instead works identically on every
        platform and is near-instant rather than a ~minute-long wait.

        Call this on a client opened with ``check_firmware_version=
        False`` — the port is still speaking binary SUMP, not the text
        shell, so the normal ``open()`` version probe would just time
        out.
        """
        ser = self._require_serial()
        ser.write(bytes([self.SUMP_FORCE_EXIT_BYTE]))
        deadline = time.time() + timeout_s
        buf = ""
        while time.time() < deadline:
            chunk = ser.read(64)
            if not chunk:
                continue
            buf += chunk.decode(errors="replace")
            if "SUMP: OK exited" in buf:
                return
        raise TimeoutError(
            "no exit confirmation from the firmware after CMD_FORCE_EXIT — "
            "the port may already be back in text mode, or still stuck in "
            "SUMP; power-cycle / replug the board if `verify` keeps failing."
        )

    def close(self) -> None:
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    def __enter__(self) -> ScannerClient:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @classmethod
    def discover(cls, **kw: object) -> ScannerClient:
        return cls(cdc_for("scanner"), **kw)  # type: ignore[arg-type]

    def _require_serial(self) -> _SerialLike:
        if self._ser is None:
            raise RuntimeError(
                "client not open — use as a context manager or call open() first"
            )
        return self._ser

    # -- low-level send / receive -----------------------------------

    def send_line(
        self,
        line: str,
        *,
        accept_prefixes: Iterable[str] = ACCEPTED_PREFIXES,
        timeout: float | None = None,
    ) -> str:
        """Send ``line\\r\\n``, return the first reply line whose
        prefix matches ``accept_prefixes``.

        The CDC2 stream interleaves periodic diag snapshots ("ADC=..."
        etc.). The prefix filter throws those away.
        """
        ser = self._require_serial()
        prefixes = tuple(accept_prefixes)
        ser.reset_input_buffer()
        ser.write((line + "\r\n").encode())
        deadline = time.time() + (timeout if timeout is not None else self.timeout)
        buf = ""
        while time.time() < deadline:
            chunk = ser.read(64)
            if not chunk:
                continue
            buf += chunk.decode(errors="replace")
            while "\n" in buf:
                line_text, _, buf = buf.partition("\n")
                stripped = line_text.strip()
                if any(stripped.startswith(p) for p in prefixes):
                    return stripped
        raise TimeoutError(f"no shell reply for {line!r}")

    def send_line_collect(
        self,
        line: str,
        *,
        accept_prefixes: Iterable[str] = ACCEPTED_PREFIXES,
        terminal_substrings: Iterable[str] = (),
        quiet_ms: int = 200,
        timeout: float | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> list[str]:
        """Send ``line``, collect every prefix-matching reply line.

        Stops when (a) any reply line contains a substring in
        ``terminal_substrings``, OR (b) ``quiet_ms`` of silence after
        at least one matching line was seen, OR (c) ``timeout``
        elapses (raises TimeoutError).
        """
        ser = self._require_serial()
        prefixes = tuple(accept_prefixes)
        terminals = tuple(terminal_substrings)
        ser.reset_input_buffer()
        ser.write((line + "\r\n").encode())
        deadline = time.time() + (timeout if timeout is not None else self.timeout)
        out: list[str] = []
        buf = ""
        last_match_at: float | None = None
        while time.time() < deadline:
            chunk = ser.read(64)
            if chunk:
                buf += chunk.decode(errors="replace")
                while "\n" in buf:
                    line_text, _, buf = buf.partition("\n")
                    stripped = line_text.strip()
                    if any(stripped.startswith(p) for p in prefixes):
                        out.append(stripped)
                        last_match_at = time.time()
                        if on_line is not None:
                            on_line(stripped)
                        if any(t in stripped for t in terminals):
                            return out
                continue
            # No new bytes — apply the quiet-timeout if we already
            # collected at least one line.
            if (
                last_match_at is not None
                and (time.time() - last_match_at) * 1000 > quiet_ms
            ):
                return out
            time.sleep(0.01)
        if out:
            return out
        raise TimeoutError(f"no shell reply for {line!r}")

    # -- SWD (F6) — WIP, hidden from public surface (F11). The shell
    #    verb itself responds `SWD: ERR wip ...` in this release, so
    #    these wrappers stay as scaffolding for the v3.1 unblock
    #    (HW gate F6 + CMSIS-DAP path F7). Names are underscored so
    #    they don't show up via tab-completion or `dir(client)` as
    #    if they were stable API.
    # ---------------------------------------------------------------

    def _swd_init(
        self, swclk_gp: int = 0, swdio_gp: int = 1, nrst_gp: int | None = 2
    ) -> str:
        if nrst_gp is None:
            cmd = f"swd init {swclk_gp} {swdio_gp}"
        else:
            cmd = f"swd init {swclk_gp} {swdio_gp} {nrst_gp}"
        return self._expect_ok("SWD:", cmd)

    def _swd_deinit(self) -> str:
        return self._expect_ok("SWD:", "swd deinit")

    def _swd_freq(self, khz: int) -> str:
        return self._expect_ok("SWD:", f"swd freq {khz}")

    def _swd_idcode(self) -> tuple[str, int | None]:
        """Run generic SWD bus detection without TARGETSEL.

        The firmware prints the SWD IDCODE using the historical
        ``dpidr=`` label because ADIv5 names DP address 0 DPIDR.
        """
        line = self.send_line("swd idcode", accept_prefixes=("SWD:",))
        return line, _parse_hex_after(line, "dpidr=")

    def _swd_connect(self) -> tuple[str, int | None]:
        """Run firmware's targeted TARGETSEL connect path."""
        line = self.send_line("swd connect", accept_prefixes=("SWD:",))
        return line, _parse_hex_after(line, "dpidr=")

    def _swd_read32(self, addr: int) -> tuple[str, int | None]:
        line = self.send_line(f"swd read32 0x{addr:08X}", accept_prefixes=("SWD:",))
        return line, _parse_hex_after(line, "]=")

    def _swd_write32(self, addr: int, value: int) -> str:
        return self._expect_ok("SWD:", f"swd write32 0x{addr:08X} 0x{value:08X}")

    def _swd_reset(self, asserted: bool) -> str:
        return self._expect_ok("SWD:", f"swd reset {1 if asserted else 0}")

    # -- JTAG (F8-1) — WIP, hidden from public surface (F11). ---------

    def _jtag_init(
        self,
        tdi: int,
        tdo: int,
        tms: int,
        tck: int,
        trst: int | None = None,
    ) -> str:
        parts = ["jtag", "init", str(tdi), str(tdo), str(tms), str(tck)]
        if trst is not None:
            parts.append(str(trst))
        return self._expect_ok("JTAG:", " ".join(parts))

    def _jtag_deinit(self) -> str:
        return self._expect_ok("JTAG:", "jtag deinit")

    def _jtag_reset(self) -> str:
        return self._expect_ok("JTAG:", "jtag reset")

    def _jtag_trst(self) -> str:
        return self._expect_ok("JTAG:", "jtag trst")

    def _jtag_chain(self) -> tuple[str, int | None]:
        line = self.send_line("jtag chain", accept_prefixes=("JTAG:",))
        return line, _parse_int_after(line, "devices=")

    def _jtag_idcode(self) -> list[str]:
        """Returns every JTAG: line emitted by the multi-line response.

        First line is ``JTAG: OK idcodes count=N``; the next N lines
        each describe one IDCODE.
        """
        return self.send_line_collect(
            "jtag idcode",
            accept_prefixes=("JTAG:",),
            quiet_ms=300,
            timeout=5.0,
        )

    # -- SCAN — `scan jtag` is WIP (hidden); `scan swd` is the only
    #    public scanner verb in this release.
    # ---------------------------------------------------------------

    def _scan_jtag(
        self,
        timeout_s: float = 30.0,
        on_progress: Callable[[str], None] | None = None,
    ) -> list[str]:
        return self.send_line_collect(
            "scan jtag",
            accept_prefixes=("SCAN:",),
            terminal_substrings=("MATCH", "NO_MATCH", "ERR"),
            timeout=timeout_s,
            on_line=on_progress,
        )

    def scan_swd(
        self,
        targetsel_hex: str | None = None,
        timeout_s: float = 30.0,
        on_progress: Callable[[str], None] | None = None,
    ) -> list[str]:
        cmd = "scan swd" if targetsel_hex is None else f"scan swd {targetsel_hex}"
        return self.send_line_collect(
            cmd,
            accept_prefixes=("SCAN:",),
            terminal_substrings=("MATCH", "NO_MATCH", "ERR"),
            timeout=timeout_s,
            on_line=on_progress,
        )

    def scan_i2c(
        self,
        timeout_s: float = 30.0,
        on_progress: Callable[[str], None] | None = None,
    ) -> list[str]:
        # NB: unlike scan_swd, a MATCH here is followed by N
        # `SCAN:   addr=0x%02X` lines — "MATCH" can't be a terminal
        # substring (it would cut off those lines, and it's also a
        # substring of "NO_MATCH"). Rely on the quiet-period fallback
        # in send_line_collect to know the address list is done.
        return self.send_line_collect(
            "scan i2c",
            accept_prefixes=("SCAN:",),
            terminal_substrings=("NO_MATCH", "ERR"),
            timeout=timeout_s,
            on_line=on_progress,
        )

    # -- Mode switches (F8-4 / F8-5) — confirmation-only -----------

    def buspirate_enter(
        self,
        tdi: int = 0,
        tdo: int = 1,
        tms: int = 2,
        tck: int = 3,
    ) -> str:
        """Send `buspirate enter`. After this the operator points
        OpenOCD at the same port; the Python client should NOT keep
        sending text-shell commands until the BusPirate session
        ends with 0x0F."""
        return self.send_line(
            f"buspirate enter {tdi} {tdo} {tms} {tck}",
            accept_prefixes=("BPIRATE:",),
        )

    def serprog_enter(
        self,
        cs: int = 0,
        mosi: int = 1,
        miso: int = 2,
        sck: int = 3,
    ) -> str:
        """Send `serprog enter`. After this point flashrom at the
        same port. The firmware exits the binary mode automatically
        on DTR drop (host disconnect)."""
        return self.send_line(
            f"serprog enter {cs} {mosi} {miso} {sck}",
            accept_prefixes=("SERPROG:",),
        )

    # -- Target UART passthrough — control verbs only. Data flows on a
    #    separate CDC (the "target" role in core.usb); see the module
    #    docstring and `faultycmd.core.cli`'s `uart console`.
    # ---------------------------------------------------------------

    def uart_enter(
        self,
        baud: int = 115200,
        parity: str = "n",
        stop_bits: int = 1,
    ) -> str:
        """Enable the bridge (CH0=TX/CH1=RX on the scanner header)."""
        return self._expect_ok("UART:", f"uart enter {baud} {parity} {stop_bits}")

    def uart_exit(self) -> str:
        return self._expect_ok("UART:", "uart exit")

    def uart_status(self) -> str:
        """Returns ``UART: disabled`` or ``UART: enabled baud=... ...``."""
        return self.send_line("uart status", accept_prefixes=("UART:",))

    def uart_set_baud(self, baud: int) -> str:
        return self._expect_ok("UART:", f"uart baud {baud}")

    def uart_set_parity(self, parity: str) -> str:
        return self._expect_ok("UART:", f"uart parity {parity}")

    def uart_set_stopbits(self, stop_bits: int) -> str:
        return self._expect_ok("UART:", f"uart stopbits {stop_bits}")

    # -- I2C manual probe — rescan known SDA/SCL pins without re-running
    #    the full `scan i2c` P(8,2)=56 sweep. Replies under `I2C:`,
    #    not `SCAN:` (see apps/faultycat_fw/main.c::cmd_i2c_probe).
    # ---------------------------------------------------------------

    def i2c_probe(
        self,
        sda: int,
        scl: int,
        timeout_s: float = 5.0,
    ) -> list[str]:
        """Rescan addresses on known SDA/SCL pins.

        Returns every ``I2C:`` line: the summary line (``OK probe
        ...`` / ``NO_MATCH ...`` / ``ERR ...``) followed by one
        ``I2C:   addr=0x%02X`` line per ACKed address.
        """
        # Same reasoning as scan_i2c: an "OK probe" summary line is
        # followed by N address lines, so it can't be a terminal
        # substring without truncating them — only NO_MATCH/ERR end
        # the reply with nothing more to collect.
        return self.send_line_collect(
            f"i2c probe {sda} {scl}",
            accept_prefixes=("I2C:",),
            terminal_substrings=("NO_MATCH", "ERR"),
            timeout=timeout_s,
        )

    def _capture_la_stream(
        self,
        ser: _SerialLike,
        cmd: str,
        prefix: str,
        ok_re: re.Pattern[str],
        binary: bool,
        timeout_s: float,
        label: str,
    ) -> tuple[re.Match[str], bytes, bool]:
        """Shared reader for the ``la`` raw-capture command.

        Sends `cmd`, waits for the one `prefix` summary line (filtered the
        usual way), then reads exactly the sample count promised by that
        line's ``stream n=`` field off the raw stream that follows it —
        without the `prefix` (see module docstring / firmware
        ``main.c::cmd_la``). ``send_line``/
        ``send_line_collect`` would silently drop those lines, so this
        reads the raw stream itself; the sample count gives a
        deterministic stop condition, unlike the quiet-period heuristic
        used for variable-length replies like ``scan_i2c``.

        `binary` selects the wire format: raw sample bytes (1 byte/sample,
        firmware's ``bin`` argument) instead of a hex dump (2 chars/
        sample, the default) — halves the bytes-on-wire, which matters at
        fast ``--interval-us`` where USB CDC throughput is the limiting
        factor, not the firmware's capture ring. `label` is used in error
        messages only.

        Returns ``(match, samples, overflow)`` — see ``LaCapture.overflow``
        for what the flag means.
        """
        ser.reset_input_buffer()
        ser.write(f"{cmd}\r\n".encode())
        start = time.time()
        deadline = start + timeout_s

        raw = b""
        summary_line: str | None = None
        while time.time() < deadline:
            chunk = ser.read(64)
            if not chunk:
                continue
            raw += chunk
            while b"\n" in raw:
                line_bytes, _, raw = raw.partition(b"\n")
                stripped = line_bytes.decode(errors="replace").strip()
                if stripped.startswith(prefix):
                    summary_line = stripped
                    break
            if summary_line is not None:
                break
        if summary_line is None:
            raise TimeoutError(
                f"{label}: no {prefix} summary line within {timeout_s:.1f}s "
                "(device unresponsive or wrong pins?)"
            )
        if " ERR " in f" {summary_line} ":
            raise ScannerError(summary_line)

        m = ok_re.search(summary_line)
        if m is None:
            raise ScannerError(summary_line)
        n_samples = int(m.group("samples"))
        needed = n_samples if binary else n_samples * 2

        if binary:
            data = bytearray(raw)
            while (
                len(data) < needed and b"TRUNC" not in data and time.time() < deadline
            ):
                chunk = ser.read(64)
                if chunk:
                    data.extend(chunk)
            truncated = b"TRUNC" in data
            got = len(data)
        else:
            text_tail = raw.decode(errors="replace")
            hex_chars = [c for c in text_tail if c in _HEX_DIGITS]
            truncated = "TRUNC" in text_tail
            while len(hex_chars) < needed and not truncated and time.time() < deadline:
                chunk = ser.read(64)
                if not chunk:
                    continue
                text = chunk.decode(errors="replace")
                text_tail += text
                if "TRUNC" in text:
                    truncated = True
                hex_chars.extend(c for c in text if c in _HEX_DIGITS)
            got = len(hex_chars)

        if got < needed:
            elapsed = time.time() - start
            pct = 100.0 * got / needed
            unit = "bytes" if binary else "hex chars"
            if truncated:
                raise ScannerError(
                    f"{label}: firmware promised {n_samples} samples "
                    f"({needed} {unit}) but gave up after sending only "
                    f"{got} ({pct:.0f}%) — the host wasn't draining the USB "
                    "CDC buffer fast enough; retry or try fewer --samples"
                )
            raise TimeoutError(
                f"{label}: firmware promised {n_samples} samples "
                f"({needed} {unit}) but only {got} ({pct:.0f}%) arrived in "
                f"{elapsed:.1f}s of {timeout_s:.1f}s budget — transfer "
                "stalled or was truncated; retry, try fewer --samples, or "
                "increase --timeout-s"
            )

        samples = (
            bytes(data[:needed])
            if binary
            else bytes.fromhex("".join(hex_chars[:needed]))
        )

        # `la_stream_and_finish` (firmware) writes a trailing `\nOVERFLOW\n`
        # right after the last sample byte/hex-char if the capture ring
        # (LA_CAPTURE_BUFFER_BYTES, 32768 samples) lapped the read cursor
        # during streaming — unlike TRUNC this doesn't stop the transfer
        # short (all `needed` bytes/chars above are genuine), it just means
        # some samples were silently skipped ahead in time, so `samples`
        # may not be contiguous past the lap point. The marker usually
        # rides in on the same read(s) that satisfied `needed` above (it's
        # only ~11 bytes right behind the last sample); a bounded top-up
        # read catches it if it hadn't hit the wire yet. Stop at the first
        # empty read rather than spinning for the full grace window — an
        # empty read means nothing is pending right now (the real serial's
        # own per-byte timeout already bounds how long that single read
        # blocked), not that the marker is still in flight.
        overflow = (
            (b"OVERFLOW" in data[needed:]) if binary else ("OVERFLOW" in text_tail)
        )
        grace_deadline = min(deadline, time.time() + self.LA_OVERFLOW_GRACE_S)
        while not overflow and time.time() < grace_deadline:
            chunk = ser.read(64)
            if not chunk:
                break
            if binary:
                overflow = b"OVERFLOW" in chunk
            else:
                overflow = "OVERFLOW" in chunk.decode(errors="replace")

        return m, samples, overflow

    def la(
        self,
        interval_us: int,
        max_samples: int,
        timeout_s: float = 10.0,
        binary: bool = False,
        trigger_ch: int | None = None,
        trigger_timeout_ms: int | None = None,
    ) -> LaCapture:
        """Capture a raw GP0..GP7 trace via the firmware logic analyzer.

        Protocol-agnostic: the firmware always samples the full
        8-channel bank and never interprets it (see
        ``faultycat-firmware/docs/LOGIC_ANALYZER.md``). See
        ``_capture_la_stream`` for the reply-framing details and the
        meaning of `binary`.

        `trigger_ch`, if given, blocks the capture window's start until
        that channel goes low, then backs it up with pre-trigger history
        (firmware ``cmd_la``'s ``trig=<ch>[:<timeout_ms>]``) — see
        ``LA_CAPTURE_TRIGGER_IMPLEMENTATION_PLAN.md``. `trigger_timeout_ms`
        bounds that wait; a firmware-side default applies if omitted. A
        firmware ``LA: ERR trigger_timeout`` reply surfaces as a
        ``ScannerError`` via the existing `` ERR `` check below — no
        special-casing needed here.
        """
        ser = self._require_serial()
        cmd = f"la {interval_us} {max_samples}"
        if binary:
            cmd += " bin"
        if trigger_ch is not None:
            cmd += f" trig={trigger_ch}"
            if trigger_timeout_ms is not None:
                cmd += f":{trigger_timeout_ms}"
        m, samples, overflow = self._capture_la_stream(
            ser,
            cmd,
            "LA:",
            _LA_OK_RE,
            binary,
            timeout_s,
            label=f"la {interval_us}us n={max_samples}",
        )
        return LaCapture(
            interval_us=int(m.group("interval_us")),
            samples=samples,
            overflow=overflow,
        )

    def la_sump_arm(self) -> str:
        """Arm the firmware's SUMP/OLS mode for a PulseView/sigrok
        ("ols" driver) capture of the full GP0..GP7 bank.

        Unlike every other command on this client, the caller is
        expected to close this connection (the ``with`` block) right
        after this returns, so PulseView/sigrok-cli can open the *same*
        port next. The armed session no longer depends on DTR staying
        up across that handoff — the firmware only leaves SUMP mode on
        an explicit ``force_exit_sump()`` call (see there), so this
        close can happen however the OS wants to handle it.
        """
        return self._expect_ok("LA:", "la sump enter")

    # -- internals --------------------------------------------------

    def _expect_ok(self, prefix: str, cmd: str) -> str:
        line = self.send_line(cmd, accept_prefixes=(prefix,))
        if " ERR " in f" {line} " or " OK " not in f" {line} ":
            raise ScannerError(line)
        return line


# Firmware emits the SWD MATCH line as
#     SCAN: swd MATCH swclk=GP<n> swdio=GP<n>
# (apps/faultycat_fw/main.c::cmd_scan_swd, F8-2). The companion
#     SCAN:   dpidr=0x... targetsel_compat=0x...
# line carries the bus-detection hex but no pin data; the scanner
# does NOT probe NRST, so the caller is expected to leave NRST
# unset when re-initialising SWD from a scan result.
_SCAN_SWD_MATCH_RE = re.compile(
    r"\bswd\s+MATCH\s+swclk=GP(?P<swclk>\d+)\s+swdio=GP(?P<swdio>\d+)\b",
    re.IGNORECASE,
)


def parse_scan_swd_match(lines: Iterable[str]) -> tuple[int, int] | None:
    """Return ``(swclk_gp, swdio_gp)`` if a SWD MATCH line is present
    in the scan output; ``None`` if NO_MATCH / ERR / unrecognised.

    Accepts the raw list returned by :meth:`ScannerClient.scan_swd`
    or any iterable of strings (including a single text block split
    by ``\\n``)."""
    for line in lines:
        m = _SCAN_SWD_MATCH_RE.search(line)
        if m:
            return int(m.group("swclk")), int(m.group("swdio"))
    return None


# Firmware emits the I2C MATCH line as
#     SCAN: i2c MATCH sda=GP<n> scl=GP<n> found=<n>
# (apps/faultycat_fw/main.c::cmd_scan_i2c), followed by `found` lines
# of `SCAN:   addr=0x<hex>`. `i2c probe` (cmd_i2c_probe) emits the
# analogous summary under the `I2C:` prefix instead:
#     I2C: OK probe sda=GP<n> scl=GP<n> found=<n>
# followed by the same `I2C:   addr=0x<hex>` shape.
_SCAN_I2C_MATCH_RE = re.compile(
    r"\bi2c\s+MATCH\s+sda=GP(?P<sda>\d+)\s+scl=GP(?P<scl>\d+)\s+found=(?P<n>\d+)\b",
    re.IGNORECASE,
)
_I2C_PROBE_OK_RE = re.compile(
    r"\bOK\s+probe\s+sda=GP(?P<sda>\d+)\s+scl=GP(?P<scl>\d+)\s+found=(?P<n>\d+)\b",
    re.IGNORECASE,
)
_I2C_ADDR_RE = re.compile(r"\baddr=0x(?P<addr>[0-9A-Fa-f]{2})\b")

# `la` summary line (apps/faultycat_fw/main.c::cmd_la):
#     LA: OK capture ch=GP0..GP7 stream n=<n> interval_us=<n>
# Protocol-agnostic — always the full GP0..GP7 bank, so there are no
# per-pin fields (see faultycat-firmware/docs/LOGIC_ANALYZER.md).
_LA_OK_RE = re.compile(
    r"\bstream\s+n=(?P<samples>\d+)\s+interval_us=(?P<interval_us>\d+)\b",
    re.IGNORECASE,
)
_HEX_DIGITS = "0123456789abcdefABCDEF"


def parse_scan_i2c_match(lines: Iterable[str]) -> tuple[int, int, list[int]] | None:
    """Return ``(sda_gp, scl_gp, addrs)`` if an I2C MATCH line is
    present in ``scan_i2c()`` output; ``None`` if NO_MATCH / ERR /
    unrecognised. ``addrs`` is parsed from the trailing
    ``addr=0x..`` lines that follow the MATCH summary."""
    lines = list(lines)
    for i, line in enumerate(lines):
        m = _SCAN_I2C_MATCH_RE.search(line)
        if m:
            addrs = [
                int(am.group("addr"), 16)
                for later in lines[i + 1 :]
                if (am := _I2C_ADDR_RE.search(later))
            ]
            return int(m.group("sda")), int(m.group("scl")), addrs
    return None


def parse_i2c_probe_ok(lines: Iterable[str]) -> list[int] | None:
    """Return the list of ACKed addresses if ``i2c_probe()`` output
    contains an ``OK probe`` summary line; ``None`` on NO_MATCH/ERR."""
    lines = list(lines)
    for i, line in enumerate(lines):
        m = _I2C_PROBE_OK_RE.search(line)
        if m:
            return [
                int(am.group("addr"), 16)
                for later in lines[i + 1 :]
                if (am := _I2C_ADDR_RE.search(later))
            ]
    return None


def _parse_hex_after(line: str, marker: str) -> int | None:
    """Find ``marker`` in ``line`` and parse the hex token that follows."""
    idx = line.find(marker)
    if idx < 0:
        return None
    rest = line[idx + len(marker) :].strip()
    token = rest.split()[0] if rest else ""
    if not token:
        return None
    try:
        return int(token, 16)
    except ValueError:
        return None


def _parse_int_after(line: str, marker: str) -> int | None:
    """Find ``marker`` and parse the decimal token that follows."""
    idx = line.find(marker)
    if idx < 0:
        return None
    rest = line[idx + len(marker) :].strip()
    token = rest.split()[0] if rest else ""
    if not token:
        return None
    try:
        return int(token, 10)
    except ValueError:
        return None


__all__ = [
    "ACCEPTED_PREFIXES",
    "LaCapture",
    "ScannerClient",
    "ScannerError",
    "parse_scan_swd_match",
    "parse_scan_i2c_match",
    "parse_i2c_probe_ok",
]
