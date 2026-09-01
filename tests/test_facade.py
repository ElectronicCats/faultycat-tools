"""Unit tests for the faultycat notebook facade.

These test *our* ergonomics layer without touching real serial ports:
the engine wrappers are driven with a recording stub, and the
hardware-discovery path is exercised via connect(require=False), which
must degrade to an empty session instead of raising when no board is
present.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import faultycat as fc
from faultycat._compat import EmfiState, EmfiTrigger, coerce_enum
from faultycat.engines import CrowbarEngine, EmfiEngine


class StubClient:
    """Records calls so we can assert the facade wires them correctly."""

    def __init__(self):
        self.calls = []
        # glitch() polls this via wait_for_charged() before firing; default
        # to already-charged so the stub behaves like the simulator (ARM ->
        # CHARGED synchronously) rather than real hardware's async charge.
        self.status_state = EmfiState.CHARGED

    def configure(self, *args):
        self.calls.append(("configure", args))

    def arm(self):
        self.calls.append(("arm", ()))

    def fire(self, timeout=60000):
        self.calls.append(("fire", (timeout,)))

    def disarm(self):
        self.calls.append(("disarm", ()))

    def status(self):
        self.calls.append(("status", ()))
        return SimpleNamespace(state=self.status_state)

    def capture(self, offset=0, length=512):
        self.calls.append(("capture", (offset, length)))
        return b"\x01\x02\x03"


# -- coerce_enum ----------------------------------------------------------

def test_coerce_enum_accepts_string_int_and_member():
    assert coerce_enum("ext_rising", EmfiTrigger, field="t") == int(EmfiTrigger.EXT_RISING)
    assert coerce_enum("EXT-RISING", EmfiTrigger, field="t") == int(EmfiTrigger.EXT_RISING)
    assert coerce_enum(EmfiTrigger.IMMEDIATE, EmfiTrigger, field="t") == 0
    assert coerce_enum(3, EmfiTrigger, field="t") == 3


def test_coerce_enum_rejects_bad_name_and_bool():
    with pytest.raises(ValueError, match="unknown t"):
        coerce_enum("nope", EmfiTrigger, field="t")
    with pytest.raises(ValueError, match="cannot be a bool"):
        coerce_enum(True, EmfiTrigger, field="t")


# -- EmfiEngine wiring ----------------------------------------------------

def test_emfi_glitch_sequence_and_coercion():
    stub = StubClient()
    emfi = EmfiEngine(stub)
    emfi.trigger = "ext_falling"
    emfi.delay_us = 120
    emfi.width_us = 8
    emfi.glitch()

    kinds = [c[0] for c in stub.calls]
    # wait_for_charged() polls status once (already CHARGED) before firing.
    assert kinds == ["configure", "arm", "status", "fire", "status"]
    # configure got the coerced trigger int + our delay/width.
    _, cfg_args = stub.calls[0]
    assert cfg_args == (int(EmfiTrigger.EXT_FALLING), 120, 8, 0)


def test_crowbar_apply_coerces_output():
    stub = StubClient()
    cb = CrowbarEngine(stub)
    cb.trigger = "immediate"
    cb.output = "hp"
    cb.delay_us = 5
    cb.width_ns = 250
    cb.apply()
    _, args = stub.calls[0]
    assert args == (0, 2, 5, 250)  # IMMEDIATE, HP, delay, width


def test_emfi_repr_html_renders():
    html = EmfiEngine(StubClient())._repr_html_()
    assert "emfi" in html and "<table" in html


# -- discovery degrades gracefully ---------------------------------------

def test_connect_without_hardware_returns_empty_session():
    # Force the discovery-failure path with bogus ports so the test is
    # deterministic whether or not a real board is plugged into the CI/dev
    # machine. require=False must degrade to an empty session, not raise.
    bogus = "/dev/faultycat-does-not-exist"
    cat = fc.connect(
        require=False,
        emfi_port=bogus,
        crowbar_port=bogus,
        scanner_port=bogus,
    )
    assert cat.emfi is None and cat.crowbar is None
    assert cat.scanner is None and cat.uart is None
    assert "none" in repr(cat).lower() or "absent" in cat._repr_html_()
    cat.close()


# -- plotting data path (needs the [notebook] extra) ---------------------

# -- ScannerEngine --------------------------------------------------------

class StubScanner:
    """A scanner client exposing only SWD (like older faultycmd)."""

    def __init__(self, swd_lines):
        self._swd_lines = swd_lines

    def scan_swd(self, targetsel_hex=None, timeout_s=30.0, on_progress=None):
        return self._swd_lines


def test_scanner_swd_parses_match():
    from faultycat.scanner import ScannerEngine

    lines = ["SCAN: swd MATCH swclk=GP2 swdio=GP3", "SCAN:   dpidr=0x0bc12477"]
    res = ScannerEngine(StubScanner(lines)).swd()
    assert res.matched and res.swclk_gp == 2 and res.swdio_gp == 3


def test_scanner_swd_no_match():
    from faultycat.scanner import ScannerEngine

    res = ScannerEngine(StubScanner(["SCAN: swd NO_MATCH"])).swd()
    assert res.matched is False and res.swclk_gp is None


def test_scanner_i2c_feature_gated():
    from faultycat.scanner import ScannerEngine

    with pytest.raises(NotImplementedError, match="I2C scanning"):
        ScannerEngine(StubScanner([])).i2c()


# -- UartTarget -----------------------------------------------------------

class StubControl:
    def __init__(self, has_uart=True):
        self._has_uart = has_uart
        if has_uart:
            self.uart_enter = lambda **kw: "UART: OK enabled"
            self.uart_exit = lambda: "UART: OK disabled"
            self.uart_status = lambda: "UART: disabled"


def test_uart_write_before_open_raises():
    from faultycat.uart import UartTarget

    u = UartTarget(StubControl())
    with pytest.raises(RuntimeError, match="not open"):
        u.write(b"hi")


def test_uart_feature_gated_when_control_lacks_bridge():
    from faultycat.uart import UartTarget

    u = UartTarget(StubControl(has_uart=False))
    with pytest.raises(NotImplementedError, match="target UART"):
        u.open()


# -- logic analyzer -------------------------------------------------------

def test_logic_channels_unpacks_bits():
    pytest.importorskip("numpy")
    from faultycat import logic_channels

    bits = logic_channels(bytes([0b00000101, 0b00000010]))  # 2 samples
    assert bits.shape == (8, 2)
    assert list(bits[0]) == [1, 0]  # GP0
    assert list(bits[1]) == [0, 1]  # GP1
    assert list(bits[2]) == [1, 0]  # GP2


# -- simulator (no hardware, full round-trip) -----------------------------

def test_simulator_emfi_and_campaign():
    cat = fc.connect(simulator=True)
    try:
        assert cat.emfi is not None and cat.crowbar is not None
        cat.emfi.trigger = "ext_rising"
        cat.emfi.delay_us = 100
        cat.emfi.width_us = 10
        cat.emfi.glitch()
        assert cat.emfi.status.state.name == "FIRED"

        trace = cat.emfi.capture(length=64)
        assert len(trace) == 64

        pytest.importorskip("pandas")
        df = cat.campaign("emfi").configure(delay=(0, 180, 20), width=(1, 25, 3)).run(progress=False)
        assert len(df) == 90
        ddf = fc.results_to_dataframe(df)
        # success is user-defined; the sim plants verify_status in a middle
        # band of each swept axis. Build the mask ourselves (no auto column).
        hits = ddf[ddf["verify_status"] != 0]
        assert 0 < len(hits) < len(ddf)
        assert hits["delay"].min() > ddf["delay"].min()
        assert hits["delay"].max() < ddf["delay"].max()
    finally:
        cat.close()


def test_uart_set_baud_never_touches_cdc_line_coding():
    # Regression: set_baud must NOT set the CDC3 line-coding — 1200 baud
    # line-coding is the RP2040 BOOTSEL magic touch and dropped the board.
    cat = fc.connect(simulator=True)
    try:
        if cat.uart is None:
            pytest.skip("uart not available")
        cat.uart.open(baud=9600)
        before = cat.uart._ser.baudrate
        cat.uart.set_baud(1200)                 # wire baud only
        assert cat.uart._ser.baudrate == before  # CDC line-coding unchanged
        assert cat.uart._ser.baudrate != 1200
    finally:
        cat.close()


def test_simulator_target_reset():
    cat = fc.connect(simulator=True)
    try:
        if not hasattr(cat.scanner.client, "send_line"):
            pytest.skip("scanner client lacks send_line")
        line = cat.target_reset(5, ms=20)
        assert "RESET: OK" in line and "GP5" in line
    finally:
        cat.close()


def test_simulator_scanner_swd():
    cat = fc.connect(simulator=True)
    try:
        if not hasattr(cat.scanner.client, "scan_swd"):
            pytest.skip("installed faultycmd ScannerClient lacks scan_swd")
        res = cat.scanner.swd()
        assert res.matched and res.swclk_gp == 2 and res.swdio_gp == 3
    finally:
        cat.close()


def test_glitch_controller():
    pytest.importorskip("pandas")
    gc = fc.GlitchController(["delay", "width"])
    gc.set_range("delay", range(0, 30, 10)).set_range("width", [1, 2])
    for p in gc.glitch_values():
        gc.add("success" if p["delay"] == 10 else "normal")
    assert len(gc) == 3 * 2                       # cartesian product
    assert gc.counts()["success"] == 2           # delay==10, both widths
    df = gc.results_df()
    assert set(["delay", "width", "group"]).issubset(df.columns)
    with pytest.raises(KeyError):
        gc.set_range("nope", [1])


def test_glitch_map_groups_and_bool():
    pd = pytest.importorskip("pandas")
    pytest.importorskip("matplotlib")
    import matplotlib
    matplotlib.use("Agg")

    df = pd.DataFrame(
        {"delay": [0, 1, 2, 3], "width": [0, 1, 2, 3],
         "group": ["normal", "reset", "success", "success"]}
    )
    # categorical: one legend entry per distinct group
    ax = fc.glitch_map(df, "group")
    assert {t.get_text() for t in ax.get_legend().get_texts()} == {"normal", "reset", "success"}
    # boolean mask: success vs no effect
    ax2 = fc.glitch_map(df, df.delay > 1)
    assert {t.get_text() for t in ax2.get_legend().get_texts()} == {"success", "no effect"}


def test_results_to_dataframe():
    pd = pytest.importorskip("pandas")

    class R:
        def __init__(self, d, w, v):
            self.step_n = 0
            self.delay = d
            self.width = w
            self.power = 0
            self.fire_status = 0
            self.verify_status = v
            self.target_state = 0
            self.ts_us = 0

    df = fc.results_to_dataframe([R(10, 5, 0), R(20, 6, 1)])
    # raw fields only — no invented 'success' column
    assert "success" not in df.columns
    assert set(["delay", "width", "verify_status"]).issubset(df.columns)
    assert list(df["verify_status"]) == [0, 1]
    assert isinstance(df, pd.DataFrame)
