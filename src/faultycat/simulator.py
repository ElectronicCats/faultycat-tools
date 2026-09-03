"""An in-memory FaultyCat that needs no board.

The faultycmd clients open their serial port through an injectable
``serial_factory``. This module provides fake serial endpoints that
speak the real wire protocols (CRC framing for the binary engines, the
text shell for the scanner), so a whole notebook — connect, configure,
glitch, campaign, glitch-map — runs end to end with no hardware.

Use it via the facade::

    import faultycat as fc
    cat = fc.connect(simulator=True)     # <- no board required
    cat.emfi.glitch()
    camp = cat.campaign("emfi").configure(delay=(0,180,20), width=(1,25,3))
    camp.run(); fc.glitch_map(camp.results_df())

The numbers are synthetic (a plausible "success region" in the sweep),
so it is for developing examples / tests, not for real results.
"""

from __future__ import annotations

import math
import struct

from ._compat import resolve_framing

_SOF = 0xFA


def _build_reply(cmd: int, payload: bytes) -> bytes:
    """Wrap a reply payload in the wire frame. Prefer faultycmd's own
    ``build_frame`` (single source of truth for the CRC); fall back to a
    local CRC16-CCITT if the module layout hides it."""
    fr = resolve_framing()
    if fr is not None and hasattr(fr, "build_frame"):
        return fr.build_frame(cmd, payload)
    body = bytes([cmd]) + struct.pack("<H", len(payload)) + payload
    crc = _crc16(body)
    return bytes([_SOF]) + body + struct.pack("<H", crc)


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = (
                ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
            )
    return crc


def _axis(a: tuple[int, int, int]) -> list[int]:
    start, end, step = a
    if step <= 0 or end <= start:
        return [start]
    return list(range(start, end + 1, step))


def _band(values: list[int], lo_frac: float, hi_frac: float) -> tuple[float, float]:
    """A sub-range of ``values``' span, given as fractions. Used to plant a
    unit-agnostic synthetic success region. A collapsed axis (single
    value) yields a band that still contains it."""
    lo, hi = values[0], values[-1]
    span = hi - lo
    return lo + lo_frac * span, lo + hi_frac * span


class _FrameSim:
    """Base for the binary (framed) engine sims. Subclasses implement
    ``handle(cmd, payload) -> reply_payload``."""

    def __init__(self) -> None:
        self._out = bytearray()

    # serial-like surface -------------------------------------------
    def write(self, data: bytes) -> int:
        # A request is one full frame: SOF, cmd, len(LE u16), payload, crc.
        if len(data) >= 5 and data[0] == _SOF:
            cmd = data[1]
            plen = data[2] | (data[3] << 8)
            payload = bytes(data[4 : 4 + plen])
            reply = self.handle(cmd, payload)
            if reply is not None:
                self._out += _build_reply(cmd | 0x80, reply)
        return len(data)

    def read(self, size: int = 1) -> bytes:
        chunk = bytes(self._out[:size])
        del self._out[:size]
        return chunk

    def reset_input_buffer(self) -> None:
        self._out.clear()

    def close(self) -> None:
        self._out.clear()

    def handle(self, cmd: int, payload: bytes) -> bytes | None:  # pragma: no cover
        raise NotImplementedError


class EmfiSim(_FrameSim):
    """F4 emfi_proto: PING / CONFIGURE / ARM / FIRE / DISARM / STATUS / CAPTURE."""

    def __init__(self) -> None:
        super().__init__()
        self.state = 0  # IDLE
        self.width = 5
        self.delay = 0

    def handle(self, cmd, payload):
        if cmd == 0x01:  # PING
            return b"F4\x00\x00"
        if cmd == 0x10:  # CONFIGURE: trigger + 3xu32
            _trig, self.delay, self.width, _to = struct.unpack("<BIII", payload[:13])
            return b"\x00"
        if cmd == 0x11:  # ARM -> CHARGED
            self.state = 2
            return b"\x00"
        if cmd == 0x12:  # FIRE -> FIRED
            self.state = 4
            return b"\x00"
        if cmd == 0x13:  # DISARM
            self.state = 0
            return b"\x00"
        if cmd == 0x14:  # STATUS: state,err + 4xu32
            return bytes([self.state, 0]) + struct.pack(
                "<IIII", 12345, 512, self.width, self.delay
            )
        if cmd == 0x15:  # CAPTURE: return a synthetic ADC trace
            off, length = struct.unpack("<HH", payload[:4])
            return _synthetic_trace(off, length)
        return b"\x00"


class CrowbarSim(_FrameSim):
    """F5 crowbar_proto: PING / CONFIGURE / ARM / FIRE / DISARM / STATUS."""

    def __init__(self) -> None:
        super().__init__()
        self.state = 0
        self.output = 1
        self.width_ns = 100
        self.delay = 0

    def handle(self, cmd, payload):
        if cmd == 0x01:
            return b"F5\x00\x00"
        if cmd == 0x10:  # CONFIGURE: trigger, output, delay, width_ns
            _trig, self.output, self.delay, self.width_ns = struct.unpack(
                "<BBII", payload[:10]
            )
            return b"\x00"
        if cmd == 0x11:
            self.state = 2
            return b"\x00"
        if cmd == 0x12:
            self.state = 4
            return b"\x00"
        if cmd == 0x13:
            self.state = 0
            return b"\x00"
        if cmd == 0x14:  # STATUS: state,err + 3xu32 + output byte (15 B)
            return (
                bytes([self.state, 0])
                + struct.pack("<III", 6789, self.width_ns, self.delay)
                + bytes([self.output])
            )
        return b"\x00"


class CampaignSim(_FrameSim):
    """F9-4 campaign_proto: CONFIG / START / STOP / STATUS / DRAIN.

    Pre-generates a synthetic sweep on START with a plausible success
    region so the glitch map has a green cluster to show.
    """

    def __init__(self) -> None:
        super().__init__()
        self.state = 0  # IDLE
        self.total = 0
        self.queue: list[bytes] = []
        self._delay = self._width = self._power = (0, 0, 0)

    def handle(self, cmd, payload):
        if cmd == 0x01:
            return b"F4\x00\x00"
        if cmd == 0x20:  # CONFIG: 10 u32
            vals = struct.unpack("<10I", payload[:40])
            self._delay = tuple(vals[0:3])
            self._width = tuple(vals[3:6])
            self._power = tuple(vals[6:9])
            self.state = 1  # CONFIGURING
            return b"\x00"  # ProtoStatus.OK
        if cmd == 0x21:  # START
            self._generate()
            self.state = 2  # SWEEPING
            return b"\x00"
        if cmd == 0x22:  # STOP
            self.state = 4  # STOPPED
            self.queue.clear()
            return b"\x00"
        if cmd == 0x23:  # STATUS (20 B)
            if not self.queue and self.state == 2:
                self.state = 3  # DONE
            step_n = self.total - len(self.queue)
            return bytes([self.state, 0, 0, 0]) + struct.pack(
                "<4I", step_n, self.total, step_n, 0
            )
        if cmd == 0x24:  # DRAIN: request 1 byte max_count -> [n][records..]
            max_count = payload[0] if payload else 1
            take = self.queue[:max_count]
            del self.queue[:max_count]
            return bytes([len(take)]) + b"".join(take)
        return b"\x00"

    def _generate(self) -> None:
        delays = _axis(self._delay)
        widths = _axis(self._width)
        powers = _axis(self._power)
        # Plant the synthetic success region RELATIVE to each swept axis'
        # range, so a plausible cluster appears whatever the units are
        # (EMFI width in µs, crowbar width in ns, any delay range).
        d_lo, d_hi = _band(delays, 0.30, 0.60)
        w_lo, w_hi = _band(widths, 0.35, 0.60)
        recs: list[bytes] = []
        step = 0
        for d in delays:
            for w in widths:
                for p in powers:
                    hit = d_lo <= d <= d_hi and w_lo <= w <= w_hi
                    fire_status = 1
                    verify_status = 1 if hit else 0
                    target_state = 2 if hit else 0
                    recs.append(
                        struct.pack("<4I", step, d, w, p)
                        + bytes([fire_status, verify_status, 0, 0])
                        + struct.pack("<II", target_state, step * 1000)
                    )
                    step += 1
        self.queue = recs
        self.total = len(recs)


class ScannerSim:
    """Text-shell sim: SWD / I2C / UART-control verbs. Logic-analyzer
    capture is not simulated (it needs the streaming binary reply);
    ``cat.scanner.logic(...)`` will time out under the simulator."""

    def __init__(self) -> None:
        self._out = bytearray()
        self._uart_on = False

    def write(self, data: bytes) -> int:
        line = data.decode(errors="replace").strip()
        for reply in self._respond(line):
            self._out += (reply + "\n").encode()
        return len(data)

    def read(self, size: int = 1) -> bytes:
        chunk = bytes(self._out[:size])
        del self._out[:size]
        return chunk

    def reset_input_buffer(self) -> None:
        self._out.clear()

    def close(self) -> None:
        self._out.clear()

    def _respond(self, line: str) -> list[str]:
        if line == "version":
            return ["SHELL: VERSION 1.0.2.0"]
        if line.startswith("scan swd"):
            return ["SCAN: swd MATCH swclk=GP2 swdio=GP3", "SCAN:   dpidr=0x0bc11477"]
        if line.startswith("scan i2c"):
            return [
                "SCAN: i2c MATCH sda=GP0 scl=GP1 found=2",
                "SCAN:   addr=0x3C",
                "SCAN:   addr=0x50",
            ]
        if line.startswith("i2c probe"):
            return ["I2C: OK probe sda=GP0 scl=GP1", "I2C:   addr=0x3C"]
        if line.startswith("reset"):
            parts = line.split()
            gp = parts[1] if len(parts) > 1 else "?"
            return [f"RESET: OK pulsed GP{gp} low 10ms"]
        if line.startswith("uart enter"):
            self._uart_on = True
            return ["UART: OK enabled baud=115200 parity=n stop=1"]
        if line.startswith("uart exit"):
            self._uart_on = False
            return ["UART: OK disabled"]
        if line.startswith("uart status"):
            return [
                (
                    "UART: enabled baud=115200 parity=n stop=1"
                    if self._uart_on
                    else "UART: disabled"
                )
            ]
        if line.startswith("uart"):
            return ["UART: OK"]
        return []


class UartDataSim:
    """Raw data-CDC sim for the target UART. Emits a boot banner, and after
    ``reset_input_buffer()`` (which the attack->observe flow calls right
    before firing) queues a 'glitched' response line — so the demo shows a
    believable before/after."""

    def __init__(self) -> None:
        self._buf = bytearray(b"boot: secure check... OK\r\nlogin: ")
        self.baudrate = 115200
        self.timeout = 0.2

    def write(self, data: bytes) -> int:
        return len(data)

    def read(self, size: int = 1) -> bytes:
        chunk = bytes(self._buf[:size])
        del self._buf[:size]
        return chunk

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        idx = self._buf.find(expected)
        if idx < 0:
            out = bytes(self._buf)
            self._buf.clear()
            return out
        end = idx + len(expected)
        out = bytes(self._buf[:end])
        del self._buf[:end]
        return out

    @property
    def in_waiting(self) -> int:
        return len(self._buf)

    def reset_input_buffer(self) -> None:
        # Simulate a fault that skipped the secure check.
        self._buf = bytearray(b"*** GLITCH HIT: auth bypass, uid=0(root)\r\n")

    def close(self) -> None:
        self._buf.clear()


class FaultyCatSimulator:
    """Vends role-specific fake serials. Pass ``.factory`` as a
    ``serial_factory``; it dispatches on the ``sim://<role>`` port
    string the session hands each client."""

    _ROLES = {
        "emfi": EmfiSim,
        "crowbar": CrowbarSim,
        "campaign-emfi": CampaignSim,
        "campaign-crowbar": CampaignSim,
        "scanner": ScannerSim,
        "target": UartDataSim,
    }

    def factory(self, port: str, baud: int = 115200, timeout: float = 0.5):
        role = port.split("://", 1)[-1] if "://" in port else port
        cls = self._ROLES.get(role)
        if cls is None:
            raise ValueError(f"no simulator for role {role!r}")
        return cls()


def _synthetic_trace(offset: int, length: int) -> bytes:
    """A decaying oscillation with a glitch spike, as one byte per sample."""
    out = bytearray()
    for i in range(length):
        x = offset + i
        base = 128 + 90 * math.sin(x / 7.0) * math.exp(-x / 180.0)
        if 40 <= x <= 46:  # the injected glitch
            base = 255 if x % 2 else 5
        out.append(max(0, min(255, int(base))))
    return bytes(out)
