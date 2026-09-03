"""Target-UART passthrough object — ``cat.uart``.

Closes the attack -> observe loop: after a glitch, read what the target
says back. The FaultyCat UART bridge is split across two CDCs:

  * **control** rides the scanner shell (``uart enter/exit/status/baud``)
    — reused from the same ScannerClient ``cat.scanner`` uses.
  * **data** is its own raw CDC (the "target" role). We open a plain
    ``serial.Serial`` on it for byte traffic.

Typical use::

    cat.uart.open(baud=115200)
    cat.emfi.glitch()
    print(cat.uart.read_until(b"\\n", timeout=1.0))
    cat.uart.close()
"""

from __future__ import annotations

from ._compat import ScannerClient, has_method, resolve_cdc_for

# Fixed CDC3 host-side line-coding. USB CDC ignores it for throughput (the
# real wire baud is set on-device via the shell), but 1200 baud is the
# RP2040's BOOTSEL magic-touch — so we pin it to a safe value and never set
# it from the wire baud.
_CDC_LINE_CODING = 115200


class UartTarget:
    """Bidirectional byte channel to the target's UART.

    ``control`` is the ScannerClient that owns the scanner shell;
    ``data_port`` overrides auto-discovery of the data CDC.
    """

    def __init__(
        self, control: ScannerClient, data_port: str | None = None, serial_factory=None
    ) -> None:
        self._control = control
        self._data_port = data_port
        self._factory = serial_factory  # inject a fake data CDC (simulator)
        self._ser = None  # lazily-opened serial.Serial on the data CDC
        self._baud = 115200
        self._enabled_by_us = False

    # -- lifecycle ----------------------------------------------------

    def open(self, baud: int = 115200, parity: str = "n", stop_bits: int = 1):
        """Enable the firmware bridge and open the data port.

        Idempotent w.r.t. the firmware bridge: if it is already enabled
        (``uart status`` says so), we attach without re-entering and
        leave it running on ``close()``.
        """
        if not has_method(self._control, "uart_enter"):
            raise NotImplementedError(
                "target UART is not available in the installed faultycmd "
                "(ScannerClient has no 'uart_enter'). Upgrade faultycmd."
            )
        already = self._control.uart_status().startswith("UART: enabled")
        if not already:
            self._control.uart_enter(baud=baud, parity=parity, stop_bits=stop_bits)
            self._enabled_by_us = True

        port = self._data_port or self._discover_data_port()
        self._baud = baud
        # The CDC3 line-coding is irrelevant to the wire speed (USB CDC —
        # the wire baud is set by uart_enter above / the shell). It MUST
        # stay off 1200: the RP2040 treats a 1200-baud CDC open as the
        # BOOTSEL magic touch and drops to the bootloader. Keep it fixed.
        if self._factory is not None:
            self._ser = self._factory(port, _CDC_LINE_CODING, 0.2)
        else:
            import serial  # noqa: PLC0415 — pyserial is a core dep

            self._ser = serial.Serial(port, _CDC_LINE_CODING, timeout=0.2)
        return self

    def close(self) -> None:
        """Close the data port; disable the bridge only if we enabled it."""
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None
        if self._enabled_by_us and has_method(self._control, "uart_exit"):
            try:
                self._control.uart_exit()
            except Exception:  # noqa: BLE001 — best-effort teardown
                pass
            self._enabled_by_us = False

    def __enter__(self) -> UartTarget:
        if self._ser is None:
            self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- control ------------------------------------------------------

    def status(self) -> str:
        """Firmware bridge status line (``UART: disabled`` / ``enabled ...``)."""
        return self._control.uart_status()

    def set_baud(self, baud: int) -> str:
        # Only the wire baud (shell command). Do NOT touch the CDC3
        # line-coding (self._ser.baudrate): it doesn't affect the USB-CDC
        # wire, and setting it to 1200 triggers the RP2040 BOOTSEL touch.
        reply = self._control.uart_set_baud(baud)
        self._baud = baud
        return reply

    # -- data I/O -----------------------------------------------------

    def write(self, data: bytes | str) -> int:
        """Write bytes (or a str, UTF-8 encoded) to the target UART."""
        if isinstance(data, str):
            data = data.encode()
        return self._ser_or_raise().write(data)

    def read(self, size: int = 1) -> bytes:
        return self._ser_or_raise().read(size)

    def read_all(self) -> bytes:
        ser = self._ser_or_raise()
        return ser.read(ser.in_waiting or 1)

    def read_until(
        self, expected: bytes = b"\n", *, timeout: float | None = None
    ) -> bytes:
        """Read until ``expected`` is seen or the read timeout elapses.

        ``timeout`` temporarily overrides the port timeout for this call.
        """
        ser = self._ser_or_raise()
        if timeout is not None:
            old, ser.timeout = ser.timeout, timeout
            try:
                return ser.read_until(expected)
            finally:
                ser.timeout = old
        return ser.read_until(expected)

    def readline(self, *, timeout: float | None = None) -> bytes:
        return self.read_until(b"\n", timeout=timeout)

    def reset_input(self) -> None:
        """Discard any buffered inbound bytes — call before a glitch so
        the response you read back is only the post-glitch output."""
        self._ser_or_raise().reset_input_buffer()

    # -- internals ----------------------------------------------------

    def _discover_data_port(self) -> str:
        if self._factory is not None:
            return "sim://target"
        cdc_for = resolve_cdc_for()
        if cdc_for is None:
            raise RuntimeError(
                "cannot locate faultycmd's cdc_for() to auto-discover the "
                "target UART data port; pass data_port= explicitly."
            )
        return cdc_for("target")

    def _ser_or_raise(self):
        if self._ser is None:
            raise RuntimeError("UART data port not open — call cat.uart.open() first")
        return self._ser

    def __repr__(self) -> str:
        state = "open" if self._ser is not None else "closed"
        return f"UartTarget({state}, baud={self._baud})"

    def _repr_html_(self) -> str:
        from ._html import rows_to_html

        rows = [
            ("data port", "open" if self._ser is not None else "closed"),
            ("baud", str(self._baud)),
        ]
        try:
            rows.append(("bridge", self.status()))
        except Exception:  # noqa: BLE001
            pass
        return rows_to_html("target uart", rows)
