"""3-axis CNC / XYZ stage control over G-code serial.

A thin controller for a G-code CNC stage (Marlin/GRBL/RepRap) used to position
the EMFI probe over the target (ChipShover-style), plus a ready-made ipywidgets
jog panel for notebooks::

    import faultycat as fc

    stage = fc.CncStage("/dev/ttyUSB0", 115200)
    fc.cnc_panel(stage)          # crosshair + step/speed/origin buttons
    ...
    stage.close()

``pyserial`` is a base dependency, so :class:`CncStage` works headless. The
:func:`cnc_panel` helper imports ``ipywidgets`` lazily — install the
``[notebook]`` extra to use it.

The stage boots assuming it is at ``0,0,0`` wherever the head happens to be (no
homing, no saved position), so :meth:`CncStage._open` disables Marlin's software
endstops (``M211 S0``) to allow negative coordinates. The intended workflow is:
jog the probe over the chip, call :meth:`CncStage.set_origin` (``G92``), then
move in +/- around that point.
"""

from __future__ import annotations

import re
import time

import serial

__all__ = [
    "CncStage",
    "cnc_panel",
    "cnc_volume_panel",
    "cnc_move_to",
    "cnc_frame",
    "cnc_diagnostics",
]

# Jog step sizes (mm) offered by the panel.
STEPS = [0.1, 1.0, 10.0, 50.0, 100.0]


class CncStage:
    """XYZ stage over G-code serial (Marlin/GRBL/RepRap).

    Wraps the serial port and the request/``ok`` G-code dialog. If the port
    drops into an unusable state it reopens once and retries, so a panel click
    is never left dead.
    """

    POS_RE = re.compile(r"X:(-?[\d.]+)\s+Y:(-?[\d.]+)\s+Z:(-?[\d.]+)")

    def __init__(self, port: str, baud: int = 115200, timeout: float = 30.0):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.ser: serial.Serial | None = None
        # Remember the adapter's VID:PID so we can re-find it if it re-enumerates
        # to a different /dev/ttyUSB* (EMFI/HV noise resets USB-serial adapters).
        self._vidpid = self._vidpid_of(port)
        self._open()

    # -- connection -------------------------------------------------------
    @staticmethod
    def _vidpid_of(device: str):
        from serial.tools import list_ports  # noqa: PLC0415

        for p in list_ports.comports():
            if p.device == device and p.vid is not None:
                return (p.vid, p.pid)
        return None

    def _resolve_port(self) -> str:
        """Return a usable device node: the known one if it still exists,
        otherwise a port matching the original VID:PID (the adapter may have
        re-enumerated to a new node after an EMFI-induced USB reset)."""
        import os  # noqa: PLC0415

        if os.path.exists(self.port):
            return self.port
        if self._vidpid:
            from serial.tools import list_ports  # noqa: PLC0415

            for p in list_ports.comports():
                if p.vid is not None and (p.vid, p.pid) == self._vidpid:
                    return p.device
        return self.port

    def _open(self) -> None:
        self.port = self._resolve_port()
        self.ser = serial.Serial(self.port, self.baud, timeout=0.1)
        # Many boards reset when the port opens: wait and flush the banner.
        time.sleep(2.0)
        self._drain()
        self._send_once("M110 N0", timeout=10)  # reset line number
        self._send_once("G90", timeout=10)  # absolute positioning
        # Positioning stage: disable software endstops so we can reach negative
        # coordinates. With M211 S1 (default) Marlin clamps any axis below
        # MIN_POS=0. Toggle it back on with soft_endstops(True).
        self._send_once("M211 S0", timeout=10)

    def reconnect(self, log=None, attempts: int = 8, wait: float = 1.0) -> None:
        """Close (if possible) and reopen the port, re-discovering its device
        node and retrying — an EMFI discharge can reset the USB-serial adapter,
        drop the port mid-command and bring it back as a different node."""
        if log:
            log("... reconnecting to port")
        try:
            if self.ser is not None:
                self.ser.close()
        except Exception:
            pass
        last = None
        for _ in range(attempts):
            try:
                self._open()
                if log:
                    log(f"... reconnected on {self.port}")
                return
            except (OSError, serial.SerialException) as e:
                last = e
                time.sleep(wait)
        raise last if last is not None else serial.SerialException("reconnect failed")

    def _alive(self) -> bool:
        # Usable = open and with pyserial's abort-pipe intact. If that pipe is
        # None (after a close/reopen), any read raises
        # "argument must be an int, or have a fileno() method".
        return bool(
            self.ser
            and self.ser.is_open
            and getattr(self.ser, "pipe_abort_read_r", None) is not None
        )

    # -- transport --------------------------------------------------------
    def _drain(self) -> list[str]:
        lines: list[str] = []
        try:
            while self.ser.in_waiting:
                line = self.ser.readline().decode(errors="replace").strip()
                if line:
                    lines.append(line)
        except Exception:
            pass
        return lines

    def send(self, cmd: str, wait_ok: bool = True, log=None, timeout=None) -> list[str]:
        """Send G-code and wait for ``ok``. If the port is unusable, reopen it
        once and retry so a click is never left dead."""
        try:
            if not self._alive():
                self.reconnect(log=log)
            return self._send_once(cmd, wait_ok=wait_ok, log=log, timeout=timeout)
        except (TypeError, OSError, serial.SerialException) as e:
            if log:
                log(f"! port down ({e}); reconnecting and retrying")
            self.reconnect(log=log)
            return self._send_once(cmd, wait_ok=wait_ok, log=log, timeout=timeout)

    def _send_once(self, cmd: str, wait_ok: bool = True, log=None, timeout=None) -> list[str]:
        cmd = cmd.strip()
        self._drain()
        if log:
            log(f"> {cmd}")
        self.ser.write((cmd + "\n").encode())
        self.ser.flush()
        if not wait_ok:
            return []
        to = self.timeout if timeout is None else timeout
        out: list[str] = []
        deadline = time.time() + to
        while time.time() < deadline:
            raw = self.ser.readline()
            if not raw:
                continue
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            out.append(line)
            if line.startswith("ok"):
                break
            if log:
                log(f"< {line}")
            if line.lower().startswith(("error", "!!")):
                break
            if line.startswith("echo:busy"):
                deadline = time.time() + to  # still working (e.g. G28)
        else:
            if log:
                log("! timeout waiting for 'ok'")
        return out

    # -- motion -----------------------------------------------------------
    def jog(self, axis: str, dist: float, feed: float, log=None) -> None:
        self.send("G91", log=log)  # relative
        self.send(f"G1 {axis}{dist:.3f} F{feed:.0f}", log=log)
        self.send("G90", log=log)  # back to absolute

    def home(self, axes: str = "", log=None) -> None:
        self.send(f"G28 {axes}".strip(), log=log)

    def position(self, log=None, timeout: float = 5.0) -> dict | None:
        """Read ``M114`` and return ``{"X":..,"Y":..,"Z":..}`` or ``None``."""
        for line in self.send("M114", log=log, timeout=timeout):
            m = self.POS_RE.search(line)
            if m:
                return {"X": float(m[1]), "Y": float(m[2]), "Z": float(m[3])}
        return None

    def motors_off(self, log=None) -> None:
        self.send("M84", log=log)

    def soft_endstops(self, enabled: bool, log=None) -> None:
        # M211 S1 = software limits ON (clamps at MIN/MAX_POS);
        # M211 S0 = OFF -> allows negative coordinates.
        self.send(f"M211 S{1 if enabled else 0}", log=log)

    def set_origin(self, log=None) -> None:
        # Set the CURRENT position as the (0,0,0) work origin. Use it after
        # placing the probe over the chip: the fault map centres on this point.
        self.send("G92 X0 Y0 Z0", log=log)

    def goto_origin(self, feed: float = 3000, log=None) -> None:
        self.send("G90", log=log)  # absolute
        self.send(f"G1 X0 Y0 Z0 F{feed:.0f}", log=log)  # back to the set origin

    def estop(self, log=None) -> None:
        self.send("M112", wait_ok=False, log=log)

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        state = "open" if self._alive() else "closed"
        return f"CncStage({self.port!r} @ {self.baud}, {state})"


def cnc_panel(stage: CncStage):
    """Build and display an ipywidgets jog panel for ``stage`` in a notebook.

    A crosshair for X/Y, a Z column, step-size and feedrate selectors, a work
    origin (``G92`` / go-to), software-endstop toggle, reconnect, emergency
    stop and a manual G-code entry. The board dialog is logged in a text box
    (newest line on top, since VS Code does not auto-scroll it).

    Displays the panel and returns ``None`` (so it renders exactly once).
    Requires the ``[notebook]`` extra (``ipywidgets``).
    """
    import ipywidgets as widgets  # noqa: PLC0415 — optional [notebook] dep
    from IPython.display import display  # noqa: PLC0415

    # Log in a Textarea (@jupyter-widgets/controls). We avoid widgets.Output:
    # its module @jupyter-widgets/output fails to register in VS Code.
    out = widgets.Textarea(
        value="", disabled=False, layout=widgets.Layout(width="100%", height="340px")
    )
    pos_label = widgets.HTML("<b>Position:</b> --")

    def log(msg):
        # Newest line on TOP: VS Code does not auto-scroll a Textarea, so the
        # latest line stays visible. Capped at 300 lines.
        lines = [str(msg)] + out.value.split("\n")
        out.value = "\n".join(lines[:300])

    def refresh_position():
        try:
            p = stage.position(log=log)
        except Exception as e:  # noqa: BLE001
            log(f"! error reading position: {e}")
            return
        if p:
            pos_label.value = (
                f"<b>Position:</b> X {p['X']:.2f} &nbsp; Y {p['Y']:.2f} &nbsp; Z {p['Z']:.2f} mm"
            )
        else:
            log("! M114 returned no position (board connected?)")

    # -- step + speed ----------------------------------------------------
    step_sel = widgets.ToggleButtons(
        options=[(f"{s:g}", s) for s in STEPS], value=1.0, description="Step mm:"
    )
    feed_xy = widgets.IntSlider(
        value=3000,
        min=100,
        max=6000,
        step=100,
        description="Speed XY",
        continuous_update=False,
        layout=widgets.Layout(width="320px"),
    )
    feed_z = widgets.IntSlider(
        value=600,
        min=100,
        max=3000,
        step=100,
        description="Speed Z",
        continuous_update=False,
        layout=widgets.Layout(width="320px"),
    )
    allow_neg = widgets.Checkbox(
        value=True,
        indent=False,
        description="Allow negatives (M211 S0 - disable software endstops)",
    )

    # -- button helpers --------------------------------------------------
    def _guard(fn):
        # Any exception in a handler goes to the log, never breaks the widget.
        def wrapped(_=None):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                log(f"! error: {e}")

        return wrapped

    def jog_button(text, axis, sign, use_z=False, style="", width="60px"):
        b = widgets.Button(
            description=text, button_style=style, layout=widgets.Layout(width=width, height="40px")
        )

        def do():
            feed = feed_z.value if use_z else feed_xy.value
            stage.jog(axis, sign * step_sel.value, feed, log=log)
            refresh_position()

        b.on_click(_guard(do))
        return b

    def action_button(text, fn, style="", width="90px"):
        b = widgets.Button(
            description=text, button_style=style, layout=widgets.Layout(width=width, height="40px")
        )
        b.on_click(_guard(fn))
        return b

    allow_neg.observe(
        _guard(lambda: stage.soft_endstops(not allow_neg.value, log=log)), names="value"
    )

    # -- XY crosshair ----------------------------------------------------
    blank = widgets.Label(layout=widgets.Layout(width="60px"))
    cross = widgets.VBox(
        [
            widgets.HBox([blank, jog_button("Y +", "Y", +1, style="primary"), blank]),
            widgets.HBox(
                [
                    jog_button("X -", "X", -1, style="primary"),
                    blank,  # empty centre of the crosshair (use "Go to origin" below)
                    jog_button("X +", "X", +1, style="primary"),
                ]
            ),
            widgets.HBox([blank, jog_button("Y -", "Y", -1, style="primary"), blank]),
        ]
    )

    z_col = widgets.VBox(
        [
            widgets.HTML("<div style='text-align:center'><b>Z</b></div>"),
            jog_button("Z +", "Z", +1, use_z=True, style="info"),
            jog_button("Z -", "Z", -1, use_z=True, style="info"),
        ]
    )

    actions = widgets.HBox(
        [
            action_button("Position", refresh_position),
            action_button("Motors off", lambda: stage.motors_off(log=log), width="110px"),
            action_button(
                "Reconnect",
                lambda: (stage.reconnect(log=log), refresh_position()),
                style="success",
                width="110px",
            ),
            action_button("STOP", lambda: stage.estop(log=log), style="danger", width="90px"),
        ]
    )

    origin_row = widgets.HBox(
        [
            action_button(
                "Set origin here (G92)",
                lambda: (stage.set_origin(log=log), refresh_position()),
                style="success",
                width="200px",
            ),
            action_button(
                "Go to origin",
                lambda: (stage.goto_origin(feed_xy.value, log=log), refresh_position()),
                width="140px",
            ),
        ]
    )

    gcode_in = widgets.Text(placeholder="manual G-code, e.g. G0 X10", description="G-code:")

    def send_gcode():
        if gcode_in.value.strip():
            stage.send(gcode_in.value, log=log)
            refresh_position()
            gcode_in.value = ""

    gcode_row = widgets.HBox([gcode_in, action_button("Send", send_gcode)])

    panel = widgets.VBox(
        [
            pos_label,
            widgets.HBox([cross, widgets.Label(layout=widgets.Layout(width="20px")), z_col]),
            step_sel,
            feed_xy,
            feed_z,
            allow_neg,
            actions,
            origin_row,
            gcode_row,
            widgets.HTML("<b>Board dialog:</b>"),
            out,
        ]
    )

    # Show the UI FIRST; any serial I/O runs afterwards and guarded, so the
    # board can never block the panel from rendering.
    display(panel)
    refresh_position()


def cnc_move_to(stage, params, *, feed=3000, keys=("x", "y", "z"), log=None):
    """Move the stage to the X/Y/Z found in a campaign point ``params``.

    Bridges a :class:`~faultycat.control.GlitchController` sweep to the stage:
    add ``x``/``y``/``z`` as axes of the controller and call this per point to
    add the CNC as an *attack axis* of the campaign::

        gc = fc.GlitchController(["x", "y", "z"])
        gc.set_range("x", range(-2, 3)).set_range("y", range(-2, 3)).set_range("z", [0])
        for p in gc.glitch_values():
            fc.cnc_move_to(stage, p)
            # (fault injection + measurement go here later)
            gc.add("visited")

    Coordinates are absolute in the work frame, so set the origin over the chip
    first (:meth:`CncStage.set_origin`). Axes missing from ``params`` are left
    unchanged. ``keys`` maps the X/Y/Z axes to the point's keys. Blocks until
    the move finishes (``M400``) so the probe is settled before the next step.
    """
    parts = [
        f"{axis}{float(params[key]):.3f}"
        for axis, key in zip("XYZ", keys, strict=False)
        if key in params
    ]
    if not parts:
        return
    stage.send("G90", log=log)  # absolute, in the work frame
    stage.send("G1 " + " ".join(parts) + f" F{feed:.0f}", log=log)
    stage.send("M400", log=log)  # wait until the move completes


def _span(v):
    """(lo, hi) from a scalar, a (lo, hi) pair, or any iterable (its min/max)."""
    if isinstance(v, (int, float)):
        return (float(v), float(v))
    vals = [float(x) for x in v]
    return (min(vals), max(vals))


def cnc_frame(
    stage, *, x, y, z=0.0, feed=1200, pause=0.5, cycles=1, return_to_origin=True, log=None
):
    """Trace the corners of the work area/volume so you can confirm the travel
    is what you want *before* running a campaign.

    Moves corner to corner around the X/Y rectangle (and, if ``z`` spans a
    range, the upper plane too, outlining the work volume), pausing at each
    corner. ``x``/``y``/``z`` each accept a scalar, a ``(lo, hi)`` pair, or any
    iterable — its min/max is used, so you can pass the very ranges you gave
    ``set_range`` to preview the sweep's real extent::

        fc.cnc_frame(stage, x=[0, 1, 2], y=[0, 5, 10])   # frame that area

    Absolute moves in the work frame, so set the origin first
    (:meth:`CncStage.set_origin`). By default it **returns to the origin**
    ``(0, 0, 0)`` at the end so the probe is back where it belongs.

    ``feed`` is kept moderate on purpose: too fast and a stepper can *lose
    steps* on the corner direction-changes (open-loop, no encoder), which is
    what makes it not come back to the same spot — lower it (or the machine's
    acceleration, ``M204``) if you see drift.
    """
    x0, x1 = _span(x)
    y0, y1 = _span(y)
    z0, z1 = _span(z)

    def rect(zz):
        return [(x0, y0, zz), (x1, y0, zz), (x1, y1, zz), (x0, y1, zz), (x0, y0, zz)]

    corners = rect(z0)
    if z1 != z0:
        corners += rect(z1)  # outline the upper plane too (volume check)

    if log:
        log(f"framing X[{x0:g}..{x1:g}] Y[{y0:g}..{y1:g}] Z[{z0:g}..{z1:g}]")
    stage.send("G90", log=log)  # absolute, in the work frame
    for _ in range(max(1, int(cycles))):
        for cx, cy, cz in corners:
            stage.send(f"G1 X{cx:.3f} Y{cy:.3f} Z{cz:.3f} F{feed:.0f}", log=log)
            stage.send("M400", log=log)  # wait for the move to finish
            if pause:
                time.sleep(pause)
    if return_to_origin:
        stage.send(f"G1 X0 Y0 Z0 F{feed:.0f}", log=log)  # back to where it belongs
        stage.send("M400", log=log)


def cnc_volume_panel(stage):
    """Standalone interface to define, frame and export the work **volume**.

    Separate from :func:`cnc_panel` (the jog interface): jog + set the origin
    over the chip there first, then here capture the corners (or type bounds)
    in X/Y/Z, hit **Frame volume** to trace the box — it returns to the origin
    so it ends where it belongs — and copy the shown `XS`/`YS`/`ZS` ranges into
    the campaign. Requires the ``[notebook]`` extra (``ipywidgets``).
    """
    import ipywidgets as widgets  # noqa: PLC0415 — optional [notebook] dep
    from IPython.display import display  # noqa: PLC0415

    out = widgets.Textarea(value="", layout=widgets.Layout(width="100%", height="120px"))

    def log(msg):
        lines = [str(msg)] + out.value.split("\n")
        out.value = "\n".join(lines[:200])

    _ft = dict(layout=widgets.Layout(width="150px"))
    xmin = widgets.FloatText(value=0.0, description="X min", **_ft)
    xmax = widgets.FloatText(value=10.0, description="X max", **_ft)
    ymin = widgets.FloatText(value=0.0, description="Y min", **_ft)
    ymax = widgets.FloatText(value=10.0, description="Y max", **_ft)
    zmin = widgets.FloatText(value=0.0, description="Z min", **_ft)
    zmax = widgets.FloatText(value=0.0, description="Z max", **_ft)
    area_step = widgets.FloatText(value=1.0, description="Step", **_ft)
    feed = widgets.IntSlider(
        value=1200,
        min=100,
        max=4000,
        step=100,
        description="Feed",
        continuous_update=False,
        layout=widgets.Layout(width="320px"),
    )
    range_out = widgets.Textarea(value="", layout=widgets.Layout(width="100%", height="80px"))

    def _range_line():
        s = area_step.value or 1.0

        def axis(name, lo, hi):
            n = max(1, int(round((hi - lo) / s)) + 1)
            return f"{name} = [round({lo} + i*{s}, 3) for i in range({n})]   # {lo}..{hi} step {s}"

        return "\n".join(
            [
                axis("XS", xmin.value, xmax.value),
                axis("YS", ymin.value, ymax.value),
                axis("ZS", zmin.value, zmax.value),
            ]
        )

    def _update(_=None):
        range_out.value = _range_line()

    for _w in (xmin, xmax, ymin, ymax, zmin, zmax, area_step):
        _w.observe(_update, names="value")
    _update()

    def _guard(fn):
        def wrapped(_=None):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                log(f"! error: {e}")

        return wrapped

    def button(text, fn, style="", width="150px"):
        b = widgets.Button(
            description=text,
            button_style=style,
            layout=widgets.Layout(width=width, height="40px"),
        )
        b.on_click(_guard(fn))
        return b

    def corner(which):
        p = stage.position(log=log)
        if not p:
            return
        if which == "min":
            xmin.value, ymin.value, zmin.value = p["X"], p["Y"], p["Z"]
        else:
            xmax.value, ymax.value, zmax.value = p["X"], p["Y"], p["Z"]
        _update()

    def frame():
        cnc_frame(
            stage,
            x=[xmin.value, xmax.value],
            y=[ymin.value, ymax.value],
            z=[zmin.value, zmax.value],
            feed=feed.value,
            pause=0.4,
            return_to_origin=True,
            log=log,
        )

    panel = widgets.VBox(
        [
            widgets.HTML(
                "<b>Work volume</b> — set the origin over the chip first (jog panel). "
                "Capture corners or type bounds (X/Y/Z), <b>Frame volume</b> to verify, "
                "then copy the ranges into the campaign. Leave Z min = Z max for a single "
                "plane."
            ),
            widgets.HBox([xmin, xmax, ymin, ymax]),
            widgets.HBox([zmin, zmax, area_step]),
            feed,
            widgets.HBox(
                [
                    button("Corner here → min", lambda: corner("min")),
                    button("Corner here → max", lambda: corner("max")),
                    button("Frame volume", frame, style="info", width="140px"),
                    button(
                        "Go to origin",
                        lambda: stage.goto_origin(feed.value, log=log),
                        width="130px",
                    ),
                ]
            ),
            widgets.HTML("<b>Copy into the campaign (XS / YS / ZS):</b>"),
            range_out,
            widgets.HTML("<b>Log:</b>"),
            out,
        ]
    )
    display(panel)


def cnc_diagnostics(port: str, baud: int = 115200) -> None:
    """Open ``port`` raw and dump pyserial's state plus a full traceback on any
    read failure. Run this only when a panel button reports an error."""
    import platform  # noqa: PLC0415
    import traceback  # noqa: PLC0415

    print("Python", platform.python_version(), "| pyserial", serial.__version__)
    print(f"--- opening {port} @ {baud} ---")
    raw = serial.Serial(port, baud, timeout=0.1)
    time.sleep(2.0)
    print(
        "fd:",
        getattr(raw, "fd", "?"),
        "| is_open:",
        raw.is_open,
        "| pipe_read:",
        getattr(raw, "pipe_abort_read_r", "?"),
        "| pipe_write:",
        getattr(raw, "pipe_abort_write_r", "?"),
    )
    try:
        print("in_waiting:", raw.in_waiting)
        raw.write(b"M114\n")
        raw.flush()
        print("write/flush OK")
        t = time.time()
        got = False
        while time.time() - t < 3.0:
            ln = raw.readline()
            if ln:
                got = True
                print("<", ln)
                if ln.startswith(b"ok"):
                    break
        if not got:
            print("(no reply in 3 s -- board powered / baud correct?)")
        print(">>> RAW READ OK: the port itself is fine")
    except Exception:
        print(">>> FAILURE -- FULL TRACEBACK:")
        traceback.print_exc()
        print(
            "state at failure -> fd:",
            getattr(raw, "fd", "?"),
            "| pipe_read:",
            getattr(raw, "pipe_abort_read_r", "?"),
            "| pipe_write:",
            getattr(raw, "pipe_abort_write_r", "?"),
        )
    finally:
        try:
            raw.close()
        except Exception as e:
            print("final close:", e)
