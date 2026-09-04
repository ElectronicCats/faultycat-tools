"""Notebook-facing facades over the faultycmd protocol clients.

Design mirrors ChipWhisperer's ``scope``: settings are plain attributes
you assign, and the object renders itself as a readable table in
Jupyter. The wire round-trip is delegated to the underlying faultycmd
client — this layer only adds ergonomics (attribute config, validation,
``_repr_html_``, numpy/pandas conversion, progress bars).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from ._compat import (
    CampaignClient,
    CampaignResult,
    CrowbarClient,
    CrowbarOutput,
    CrowbarStatus,
    CrowbarTrigger,
    EmfiClient,
    EmfiState,
    EmfiStatus,
    EmfiTrigger,
    coerce_enum,
)
from ._html import rows_to_html, status_html

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd


class EmfiEngine:
    """EMFI (electromagnetic) engine — ``cat.emfi``.

    Configure via attributes, then arm/fire::

        cat.emfi.trigger = "ext_rising"
        cat.emfi.delay_us = 100
        cat.emfi.width_us = 10
        cat.emfi.arm()
        cat.emfi.fire()
        trace = cat.emfi.capture()
    """

    def __init__(self, client: EmfiClient) -> None:
        self._c = client
        self.trigger: EmfiTrigger | int | str = EmfiTrigger.EXT_RISING
        self.delay_us: int = 0
        self.width_us: int = 5
        self.charge_timeout_ms: int = 0
        # Pulses per trigger (ChipWhisperer's glitch.repeat). >1 fires a burst
        # of `repeat` pulses, each preceded by `delay_us` (trigger -> delay ->
        # pulse -> delay -> pulse ...). Needs multipulse firmware; repeat=1
        # works on any firmware.
        self.repeat: int = 1

    def apply(self) -> None:
        """Push the current attribute settings to the firmware."""
        trig = coerce_enum(self.trigger, EmfiTrigger, field="trigger")
        if int(self.repeat) <= 1:
            self._c.configure(
                trig,
                int(self.delay_us),
                int(self.width_us),
                int(self.charge_timeout_ms),
            )
            return
        # Extended CONFIGURE carrying repeat (5th u32). Firmware without
        # multipulse support ignores it / rejects the longer frame.
        import struct  # noqa: PLC0415

        from faultycmd.protocols.emfi import CMD_CONFIGURE  # noqa: PLC0415

        payload = bytes([trig]) + struct.pack(
            "<IIII",
            int(self.delay_us),
            int(self.width_us),
            int(self.charge_timeout_ms),
            int(self.repeat),
        )
        self._c._raise_on_err(self._c._send(CMD_CONFIGURE, payload))

    def arm(self, *, apply: bool = True) -> None:
        """Apply settings (unless ``apply=False``) then arm (HV charges)."""
        if apply:
            self.apply()
        self._c.arm()

    def fire(self, trigger_timeout_ms: int = 60000) -> None:
        self._c.fire(trigger_timeout_ms)

    def disarm(self) -> None:
        self._c.disarm()

    def glitch(
        self, trigger_timeout_ms: int = 60000, charge_timeout_s: float = 5.0
    ) -> EmfiStatus:
        """Convenience one-shot: apply -> arm -> wait for HV charge -> fire -> status.

        ``arm()`` only kicks off HV charging — the firmware reaches CHARGED
        asynchronously a moment later. Firing before that returns a
        misleading ``INTERNAL`` error (really "HV not charged yet"), so this
        waits for CHARGED first, same as the ``emfi arm`` CLI command does.
        """
        self.arm()
        if not self.wait_for_charged(charge_timeout_s):
            raise TimeoutError(
                f"HV cap did not reach CHARGED within {charge_timeout_s:.1f}s "
                "(check cat.emfi.status, or raise charge_timeout_s)"
            )
        self.fire(trigger_timeout_ms)
        return self.status

    @property
    def status(self) -> EmfiStatus:
        return self._c.status()

    @property
    def charged(self) -> bool:
        return self.status.state == EmfiState.CHARGED

    def wait_for_charged(self, timeout_s: float = 5.0) -> bool:
        """Block until the HV cap reports CHARGED (or timeout). Poll it
        after ``arm()`` and before ``fire()`` — the status round-trip
        paces the loop. Returns False on timeout."""
        import time  # noqa: PLC0415

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.charged:
                return True
        return False

    def capture(self, offset: int = 0, length: int = 512):
        """Read the ADC capture buffer. Returns a numpy array if numpy is
        installed (the ``[notebook]`` extra), else raw ``bytes``."""
        raw = self._c.capture(offset, length)
        try:
            import numpy as np  # noqa: PLC0415 — optional [notebook] dep
        except ImportError:
            return raw
        return np.frombuffer(raw, dtype=np.uint8)

    def _settings_rows(self) -> list[tuple[str, str]]:
        return [
            ("trigger", str(self.trigger)),
            ("delay_us", str(self.delay_us)),
            ("width_us", str(self.width_us)),
            ("charge_timeout_ms", str(self.charge_timeout_ms)),
            ("repeat", str(self.repeat)),
        ]

    def __repr__(self) -> str:
        s = self._settings_rows()
        return "EmfiEngine(" + ", ".join(f"{k}={v}" for k, v in s) + ")"

    def _repr_html_(self) -> str:
        html = rows_to_html("emfi · settings", self._settings_rows())
        try:
            html += status_html("emfi · status", self.status)
        except Exception:  # noqa: BLE001 — status is best-effort in repr
            pass
        return html


class CrowbarEngine:
    """Crowbar (voltage-glitch) engine — ``cat.crowbar``."""

    def __init__(self, client: CrowbarClient) -> None:
        self._c = client
        self.trigger: CrowbarTrigger | int | str = CrowbarTrigger.EXT_RISING
        self.output: CrowbarOutput | int | str = CrowbarOutput.LP
        self.delay_us: int = 0
        self.width_ns: int = 100
        self.repeat: int = 1  # pulses per trigger (needs multipulse firmware if >1)

    def apply(self) -> None:
        trig = coerce_enum(self.trigger, CrowbarTrigger, field="trigger")
        out = coerce_enum(self.output, CrowbarOutput, field="output")
        if int(self.repeat) <= 1:
            self._c.configure(trig, out, int(self.delay_us), int(self.width_ns))
            return
        import struct  # noqa: PLC0415

        from faultycmd.protocols.crowbar import CMD_CONFIGURE  # noqa: PLC0415

        payload = bytes([trig, out]) + struct.pack(
            "<III", int(self.delay_us), int(self.width_ns), int(self.repeat)
        )
        self._c._raise_on_err(self._c._send(CMD_CONFIGURE, payload))

    def arm(self, *, apply: bool = True) -> None:
        if apply:
            self.apply()
        self._c.arm()

    def fire(self, trigger_timeout_ms: int = 60000) -> None:
        self._c.fire(trigger_timeout_ms)

    def disarm(self) -> None:
        self._c.disarm()

    def glitch(self, trigger_timeout_ms: int = 60000) -> CrowbarStatus:
        self.arm()
        self.fire(trigger_timeout_ms)
        return self.status

    @property
    def status(self) -> CrowbarStatus:
        return self._c.status()

    def _settings_rows(self) -> list[tuple[str, str]]:
        return [
            ("trigger", str(self.trigger)),
            ("output", str(self.output)),
            ("delay_us", str(self.delay_us)),
            ("width_ns", str(self.width_ns)),
            ("repeat", str(self.repeat)),
        ]

    def __repr__(self) -> str:
        s = self._settings_rows()
        return "CrowbarEngine(" + ", ".join(f"{k}={v}" for k, v in s) + ")"

    def _repr_html_(self) -> str:
        html = rows_to_html("crowbar · settings", self._settings_rows())
        try:
            html += status_html("crowbar · status", self.status)
        except Exception:  # noqa: BLE001
            pass
        return html


Axis = tuple[int, int, int]  # (start, end, step); step==0 -> single value


class CampaignRunner:
    """Parameter-sweep runner — ``cat.campaign("emfi")``.

    A campaign sweeps ``delay`` / ``width`` / ``power`` axes on-device
    and streams back one :class:`CampaignResult` per step. This is the
    workflow a notebook does best: run the sweep, collect a DataFrame,
    plot the glitch map.
    """

    def __init__(self, client: CampaignClient) -> None:
        self._c = client
        self.engine: str = getattr(client, "engine", "emfi")
        self.delay: Axis = (0, 0, 0)
        self.width: Axis = (0, 0, 0)
        self.power: Axis = (0, 0, 0)
        self.settle_ms: int = 0
        self._results: list[CampaignResult] = []

    def configure(
        self,
        *,
        delay: Axis | None = None,
        width: Axis | None = None,
        power: Axis | None = None,
        settle_ms: int | None = None,
    ) -> CampaignRunner:
        """Set sweep axes. Each axis is ``(start, end, step)``; a
        ``step`` of 0 collapses that axis to its start value."""
        if delay is not None:
            self.delay = delay
        if width is not None:
            self.width = width
        if power is not None:
            self.power = power
        if settle_ms is not None:
            self.settle_ms = settle_ms
        self._c.configure(self.delay, self.width, self.power, self.settle_ms)
        return self

    def run(
        self,
        *,
        progress: bool = True,
        every_ms: int = 200,
        on_result: Callable[[CampaignResult], None] | None = None,
    ) -> list[CampaignResult]:
        """Start the sweep and block until it finishes, collecting all
        results. Shows a tqdm progress bar in notebooks when available.

        Assumes :meth:`configure` has been called. Returns the full list
        of results (also stored on ``self.results``).
        """
        self._results = []
        bar = self._make_bar(progress)
        try:
            self._c.start()
            for status, batch in self._c.watch(every_ms=every_ms):
                for r in batch:
                    self._results.append(r)
                    if on_result is not None:
                        on_result(r)
                if bar is not None:
                    bar.total = getattr(status, "total_steps", None) or bar.total
                    bar.n = getattr(status, "step_n", bar.n)
                    bar.refresh()
        finally:
            if bar is not None:
                bar.close()
        return self._results

    def stop(self) -> None:
        self._c.stop()

    @property
    def results(self) -> list[CampaignResult]:
        return self._results

    def results_df(self) -> pd.DataFrame:
        """Return the collected results as a pandas DataFrame."""
        from .plotting import results_to_dataframe  # noqa: PLC0415 — optional dep

        return results_to_dataframe(self._results)

    @staticmethod
    def _make_bar(progress: bool):
        if not progress:
            return None
        try:
            from tqdm.auto import tqdm  # noqa: PLC0415 — optional [notebook] dep
        except ImportError:
            return None
        return tqdm(total=0, unit="step", desc="campaign")

    def __repr__(self) -> str:
        return (
            f"CampaignRunner(engine={self.engine!r}, delay={self.delay}, "
            f"width={self.width}, power={self.power}, results={len(self._results)})"
        )

    def _repr_html_(self) -> str:
        rows = [
            ("engine", self.engine),
            ("delay (s,e,st)", str(self.delay)),
            ("width (s,e,st)", str(self.width)),
            ("power (s,e,st)", str(self.power)),
            ("settle_ms", str(self.settle_ms)),
            ("results collected", str(len(self._results))),
        ]
        return rows_to_html("campaign", rows)


def iter_campaign(
    client: CampaignClient, every_ms: int = 200
) -> Iterator[CampaignResult]:
    """Low-level generator: start a configured campaign and yield each
    result as it arrives. For callers who want to stream rather than
    collect."""
    client.start()
    for _status, batch in client.watch(every_ms=every_ms):
        yield from batch
