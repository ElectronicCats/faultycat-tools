"""Unit tests for faultycmd.protocols.scanner — text-shell wrapper."""

from __future__ import annotations

import pytest

from faultycmd.protocols import ScannerClient, ScannerError
from faultycmd.protocols.scanner import (
    parse_i2c_probe_ok,
    parse_scan_i2c_match,
    parse_scan_swd_match,
)


class FakeShellSerial:
    """Line-emitting serial stand-in. Calls back into a script once
    per `read()`; the script returns CDC2-style mixed lines (some
    diag noise, some matching prefixes)."""

    def __init__(self) -> None:
        self.written = bytearray()
        self.replies: list[bytes] = []
        self.closed = False

    def queue_lines(self, *lines: str) -> None:
        """Queue full lines (\\n appended automatically) to be returned."""
        text = "\n".join(lines) + "\n"
        self.replies.append(text.encode())

    def write(self, data: bytes) -> int:
        self.written.extend(data)
        return len(data)

    def read(self, size: int = 1) -> bytes:
        if not self.replies:
            return b""
        chunk = self.replies.pop(0)
        if len(chunk) > size:
            self.replies.insert(0, chunk[size:])
            return bytes(chunk[:size])
        return bytes(chunk)

    def reset_input_buffer(self) -> None:
        # Leave queued replies alone (same rationale as FakeSerial).
        pass

    def close(self) -> None:
        self.closed = True


def _client(fake: FakeShellSerial) -> ScannerClient:
    factory = lambda *_a, **_kw: fake  # noqa: E731
    return ScannerClient(
        "/dev/null", serial_factory=factory, check_firmware_version=False
    )


# -- low-level send_line ------------------------------------------


def test_send_line_filters_noise():
    fake = FakeShellSerial()
    fake.queue_lines(
        "ADC= 750 SCAN=11111111 TRIG=0 GATE=NONE HV[---] EMFI=IDLE CROW=IDLE",
        "SWD: OK init swclk=GP0 swdio=GP1 nrst=2",
        "ADC= 753 SCAN=11111111",
    )
    with _client(fake) as cli:
        line = cli.send_line("swd init 0 1 2", accept_prefixes=("SWD:",))
    assert line == "SWD: OK init swclk=GP0 swdio=GP1 nrst=2"
    assert b"swd init 0 1 2\r\n" in bytes(fake.written)


def test_send_line_timeout():
    fake = FakeShellSerial()
    fake.queue_lines("noise without prefix")
    with _client(fake) as cli, pytest.raises(TimeoutError):
        cli.send_line("nonsense", accept_prefixes=("ZZ:",), timeout=0.1)


# -- SWD ----------------------------------------------------------


def test_swd_init_ok():
    fake = FakeShellSerial()
    fake.queue_lines("SWD: OK init swclk=GP0 swdio=GP1 nrst=2")
    with _client(fake) as cli:
        line = cli._swd_init(0, 1, 2)
    assert "OK init" in line


def test_swd_connect_parses_dpidr():
    fake = FakeShellSerial()
    fake.queue_lines("SWD: OK connect dpidr=0x2BA01477")
    with _client(fake) as cli:
        line, dpidr = cli._swd_connect()
    assert dpidr == 0x2BA01477
    assert b"swd connect\r\n" in bytes(fake.written)


def test_swd_idcode_parses_dpidr_and_uses_new_command():
    fake = FakeShellSerial()
    fake.queue_lines(
        "SWD: OK bus-detect dpidr=0x0BC12477"
    )  # RP2040 is just one example.
    with _client(fake) as cli:
        line, dpidr = cli._swd_idcode()
    assert "OK bus-detect" in line
    assert dpidr == 0x0BC12477
    assert b"swd idcode\r\n" in bytes(fake.written)


def test_swd_read32_parses_value():
    fake = FakeShellSerial()
    fake.queue_lines("SWD: OK read32 [0x20000000]=0xDEADBEEF")
    with _client(fake) as cli:
        line, value = cli._swd_read32(0x20000000)
    assert value == 0xDEADBEEF


def test_swd_err_raises():
    fake = FakeShellSerial()
    fake.queue_lines("SWD: ERR phy_init_failed")
    with _client(fake) as cli, pytest.raises(ScannerError) as ei:
        cli._swd_init()
    assert "phy_init_failed" in ei.value.line


# -- JTAG ---------------------------------------------------------


def test_jtag_init_with_trst():
    fake = FakeShellSerial()
    fake.queue_lines("JTAG: OK init tdi=GP0 tdo=GP1 tms=GP2 tck=GP3 trst=4")
    with _client(fake) as cli:
        cli._jtag_init(0, 1, 2, 3, 4)
    assert b"jtag init 0 1 2 3 4\r\n" in bytes(fake.written)


def test_jtag_chain_parses_count():
    fake = FakeShellSerial()
    fake.queue_lines("JTAG: OK chain devices=2")
    with _client(fake) as cli:
        line, n = cli._jtag_chain()
    assert n == 2


def test_jtag_idcode_collects_multi_line():
    fake = FakeShellSerial()
    fake.queue_lines(
        "JTAG: OK idcodes count=2",
        "JTAG:   [0] 0x1BA01477 VALID mfg_bank=0x4 mfg_id=0x3B part=0xBA01 ver=0x1",
        "JTAG:   [1] 0x4BA00477 VALID mfg_bank=0x4 mfg_id=0x3B part=0xBA00 ver=0x4",
    )
    with _client(fake) as cli:
        lines = cli._jtag_idcode()
    assert len(lines) == 3
    assert "count=2" in lines[0]
    assert "0x1BA01477" in lines[1]


# -- SCAN ---------------------------------------------------------


def test_scan_jtag_streams_progress_until_terminal():
    fake = FakeShellSerial()
    fake.queue_lines(
        "SCAN: starting JTAG pinout scan over 8 channels (P(8,4)=1680)",
        "SCAN: progress 0/1680",
        "SCAN: progress 100/1680",
        "ADC= 750 SCAN=11111111",  # diag noise interleaved
        "SCAN: jtag NO_MATCH (no valid IDCODE found)",
    )
    seen: list[str] = []
    with _client(fake) as cli:
        lines = cli._scan_jtag(timeout_s=2.0, on_progress=seen.append)
    assert any("NO_MATCH" in line for line in lines)
    assert any("starting" in line for line in seen)
    assert any("NO_MATCH" in line for line in seen)


def test_scan_swd_with_targetsel():
    fake = FakeShellSerial()
    fake.queue_lines(
        "SCAN: starting SWD pinout scan over 8 channels (P(8,2)=56) targetsel_compat=0x01002927",
        "SCAN: swd NO_MATCH (no OK DPIDR found)",
    )
    with _client(fake) as cli:
        cli.scan_swd(targetsel_hex="01002927", timeout_s=2.0)
    assert b"scan swd 01002927\r\n" in bytes(fake.written)


def test_scan_i2c_collects_addr_lines_after_match():
    fake = FakeShellSerial()
    fake.queue_lines(
        "SCAN: starting I2C pinout scan over 8 channels (P(8,2)=56)",
        "SCAN: progress 0/56",
        "ADC= 750 SCAN=11111111",  # diag noise interleaved
        "SCAN: i2c MATCH sda=GP0 scl=GP1 found=2",
        "SCAN:   addr=0x3C",
        "SCAN:   addr=0x50",
    )
    with _client(fake) as cli:
        lines = cli.scan_i2c(timeout_s=2.0)
    assert any("MATCH" in line for line in lines)
    assert sum("addr=" in line for line in lines) == 2


def test_scan_i2c_no_match():
    fake = FakeShellSerial()
    fake.queue_lines(
        "SCAN: starting I2C pinout scan over 8 channels (P(8,2)=56)",
        "SCAN: i2c NO_MATCH (no ACKed address found)",
    )
    with _client(fake) as cli:
        lines = cli.scan_i2c(timeout_s=2.0)
    assert any("NO_MATCH" in line for line in lines)


def test_i2c_probe_collects_addr_lines():
    fake = FakeShellSerial()
    fake.queue_lines(
        "I2C: OK probe sda=GP0 scl=GP1 found=1",
        "I2C:   addr=0x3C",
    )
    with _client(fake) as cli:
        lines = cli.i2c_probe(0, 1, timeout_s=2.0)
    assert b"i2c probe 0 1\r\n" in bytes(fake.written)
    assert any("OK probe" in line for line in lines)
    assert any("addr=0x3C" in line for line in lines)


def test_i2c_probe_no_match():
    fake = FakeShellSerial()
    fake.queue_lines("I2C: NO_MATCH sda=GP0 scl=GP1 (no ACKed address)")
    with _client(fake) as cli:
        lines = cli.i2c_probe(0, 1, timeout_s=2.0)
    assert any("NO_MATCH" in line for line in lines)


def test_la_parses_summary_and_hexdump():
    fake = FakeShellSerial()
    # 4 samples -> 8 hex chars, wrapped across lines without LA: prefix.
    fake.queue_lines(
        "LA: OK capture ch=GP0..GP7 stream n=4 interval_us=2",
        "01 02",
        "03 00",
    )
    with _client(fake) as cli:
        cap = cli.la(2, 4, timeout_s=2.0)
    assert b"la 2 4\r\n" in bytes(fake.written)
    assert cap.interval_us == 2
    assert cap.samples == bytes.fromhex("01020300")


def test_la_passes_max_samples_unclamped():
    fake = FakeShellSerial()
    fake.queue_lines(
        "LA: OK capture ch=GP0..GP7 stream n=0 interval_us=2",
        "",
    )
    with _client(fake) as cli:
        cli.la(2, 999999, timeout_s=2.0)
    # Value must pass through unchanged — the firmware caps it, not the host.
    assert b"la 2 999999\r\n" in bytes(fake.written)


def test_la_binary_mode_sends_bin_and_reads_raw_bytes():
    fake = FakeShellSerial()
    fake.queue_lines("LA: OK capture ch=GP0..GP7 stream n=4 interval_us=2")
    fake.replies.append(b"\x01\x02\x03\x04")
    with _client(fake) as cli:
        cap = cli.la(2, 4, timeout_s=2.0, binary=True)
    assert b"la 2 4 bin\r\n" in bytes(fake.written)
    assert cap.samples == b"\x01\x02\x03\x04"


def test_la_err_raises():
    fake = FakeShellSerial()
    fake.queue_lines("LA: ERR start_failed")
    with _client(fake) as cli, pytest.raises(ScannerError) as ei:
        cli.la(2, 4, timeout_s=2.0)
    assert "start_failed" in ei.value.line


def test_la_timeout_waiting_for_hex():
    fake = FakeShellSerial()
    fake.queue_lines(
        "LA: OK capture ch=GP0..GP7 stream n=4 interval_us=2",
        "01",  # only 2 of the 8 expected hex chars arrive
    )
    with _client(fake) as cli, pytest.raises(TimeoutError):
        cli.la(2, 4, timeout_s=0.2)


def test_la_trigger_ch_appends_trig_arg():
    fake = FakeShellSerial()
    fake.queue_lines(
        "LA: OK capture ch=GP0..GP7 stream n=4 interval_us=2",
        "01 02",
        "03 00",
    )
    with _client(fake) as cli:
        cli.la(2, 4, timeout_s=2.0, trigger_ch=3)
    assert b"la 2 4 trig=3\r\n" in bytes(fake.written)


def test_la_trigger_ch_and_timeout_appends_trig_arg_with_ms():
    fake = FakeShellSerial()
    fake.queue_lines(
        "LA: OK capture ch=GP0..GP7 stream n=4 interval_us=2",
        "01 02",
        "03 00",
    )
    with _client(fake) as cli:
        cli.la(2, 4, timeout_s=2.0, trigger_ch=3, trigger_timeout_ms=1500)
    assert b"la 2 4 trig=3:1500\r\n" in bytes(fake.written)


def test_la_trigger_timeout_reply_raises_scanner_error():
    # Firmware's `LA: ERR trigger_timeout` (trig= wait exceeded) must
    # surface the same way any other `LA: ERR ...` reply does — no
    # special-casing needed in _capture_la_stream's generic " ERR " check.
    fake = FakeShellSerial()
    fake.queue_lines("LA: ERR trigger_timeout")
    with _client(fake) as cli, pytest.raises(ScannerError) as ei:
        cli.la(2, 4, timeout_s=2.0, trigger_ch=0, trigger_timeout_ms=100)
    assert "trigger_timeout" in ei.value.line


def test_la_sump_arm_sends_enter_and_returns_ok():
    fake = FakeShellSerial()
    fake.queue_lines("LA: OK entering SUMP mode ch=GP0..GP7")
    with _client(fake) as cli:
        reply = cli.la_sump_arm()
    assert b"la sump enter\r\n" in bytes(fake.written)
    assert "OK entering SUMP mode" in reply


def test_la_sump_arm_err_raises():
    fake = FakeShellSerial()
    fake.queue_lines("LA: ERR bus_busy (held by another service)")
    with _client(fake) as cli, pytest.raises(ScannerError):
        cli.la_sump_arm()


def test_force_exit_sump_writes_escape_byte_and_waits_for_ok():
    fake = FakeShellSerial()
    fake.queue_lines("SUMP: OK exited (back to text shell)")
    with _client(fake) as cli:
        cli.force_exit_sump()
    assert bytes(fake.written) == bytes([ScannerClient.SUMP_FORCE_EXIT_BYTE])


def test_force_exit_sump_times_out_without_confirmation():
    fake = FakeShellSerial()
    with _client(fake) as cli, pytest.raises(TimeoutError):
        cli.force_exit_sump(timeout_s=0.2)


# -- mode switches ------------------------------------------------


def test_buspirate_enter_returns_confirmation():
    fake = FakeShellSerial()
    fake.queue_lines(
        "BPIRATE: OK entering BBIO mode tdi=GP0 tdo=GP1 tms=GP2 tck=GP3",
    )
    with _client(fake) as cli:
        line = cli.buspirate_enter(0, 1, 2, 3)
    assert "OK entering BBIO" in line


def test_serprog_enter_returns_confirmation():
    fake = FakeShellSerial()
    fake.queue_lines(
        "SERPROG: OK entering serprog mode cs=GP0 mosi=GP1 miso=GP2 sck=GP3",
    )
    with _client(fake) as cli:
        line = cli.serprog_enter()
    assert "OK entering serprog" in line


# -- lifecycle ----------------------------------------------------


def test_must_open_first():
    fake = FakeShellSerial()
    cli = _client(fake)
    with pytest.raises(RuntimeError):
        cli.send_line("?")


def test_close_runs_via_context():
    fake = FakeShellSerial()
    fake.queue_lines("SWD: OK init swclk=GP0 swdio=GP1 nrst=2")
    with _client(fake) as cli:
        cli._swd_init()
    assert fake.closed


# -- parse_scan_swd_match -----------------------------------------


def test_parse_scan_swd_match_picks_swclk_swdio():
    lines = [
        "SCAN: starting SWD pinout scan over 8 channels (P(8,2)=56)",
        "SCAN: progress 12/56",
        "SCAN: swd MATCH swclk=GP3 swdio=GP5",
        "SCAN:   dpidr=0x0BC12477 targetsel_compat=0x01002927",
    ]
    assert parse_scan_swd_match(lines) == (3, 5)


def test_parse_scan_swd_match_returns_none_on_no_match():
    lines = [
        "SCAN: starting SWD pinout scan over 8 channels (P(8,2)=56)",
        "SCAN: swd NO_MATCH (no OK DPIDR found)",
    ]
    assert parse_scan_swd_match(lines) is None


def test_parse_scan_swd_match_accepts_split_text_block():
    # Same scenario but the caller hands us a single text blob
    # (e.g. the `_run_scanner_task` `_render` output) and split()s
    # it externally. parse_scan_swd_match takes any iterable of
    # strings, so a list of split lines must still match.
    blob = (
        "SCAN: swd MATCH swclk=GP0 swdio=GP1\n"
        "SCAN:   dpidr=0x0BC12477 targetsel_compat=0x01002927"
    )
    assert parse_scan_swd_match(blob.split("\n")) == (0, 1)


# -- parse_scan_i2c_match / parse_i2c_probe_ok ---------------------


def test_parse_scan_i2c_match_picks_pins_and_addrs():
    lines = [
        "SCAN: starting I2C pinout scan over 8 channels (P(8,2)=56)",
        "SCAN: progress 12/56",
        "SCAN: i2c MATCH sda=GP0 scl=GP1 found=2",
        "SCAN:   addr=0x3C",
        "SCAN:   addr=0x50",
    ]
    assert parse_scan_i2c_match(lines) == (0, 1, [0x3C, 0x50])


def test_parse_scan_i2c_match_returns_none_on_no_match():
    lines = [
        "SCAN: starting I2C pinout scan over 8 channels (P(8,2)=56)",
        "SCAN: i2c NO_MATCH (no ACKed address found)",
    ]
    assert parse_scan_i2c_match(lines) is None


def test_parse_i2c_probe_ok_returns_addrs():
    lines = [
        "I2C: OK probe sda=GP0 scl=GP1 found=2",
        "I2C:   addr=0x3C",
        "I2C:   addr=0x50",
    ]
    assert parse_i2c_probe_ok(lines) == [0x3C, 0x50]


def test_parse_i2c_probe_ok_returns_none_on_no_match():
    lines = ["I2C: NO_MATCH sda=GP0 scl=GP1 (no ACKed address)"]
    assert parse_i2c_probe_ok(lines) is None
