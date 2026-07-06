"""Firmware ↔ host version parity check.

The firmware embeds its 4-segment version (`A.MAJOR.MINOR.PATCH`,
where `A` is the compatible board id) in two places the host already
talks to:

* PING reply on CDC0/CDC1 (emfi/crowbar proto): bytes `'F', family,
  A, MAJOR, MINOR, PATCH` (6 B payload).
* `version` text command on CDC2 (scanner shell): single line
  `SHELL: VERSION A.MAJOR.MINOR.PATCH`.

The host only reports `MAJOR.MINOR.PATCH` (`faultycmd.__version__`,
no board segment) since the host itself isn't tied to a board, and
the host and firmware version numbers are released and incremented
independently — there is no expectation that they line up. The only
thing that has to match is the hardware: on connect, each client
validates that the firmware's board id `A` equals
:data:`EXPECTED_BOARD`.

A board mismatch aborts the connection with a clear "wrong board"
message. Operators can override the check globally with the CLI flag
``--ignore-version-mismatch`` — that flips :func:`set_allow_mismatch`,
which the per-client probes consult.

The version files in the repo (``/VERSION``, ``__init__.py``,
``pyproject.toml``, CMake ``project(VERSION ...)``) are kept in sync
by the tag-driven GitHub release workflow.
"""

from __future__ import annotations

import re

from .. import __version__

# Host version: (MAJOR, MINOR, PATCH) — no board segment.
VersionTuple = tuple[int, int, int]
# Firmware version: (board, MAJOR, MINOR, PATCH).
FirmwareVersionTuple = tuple[int, int, int, int]

# The single FaultyCat board id every supported firmware must report.
EXPECTED_BOARD = 2

_HOST_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_SHELL_VERSION_RE = re.compile(r"^SHELL:\s+VERSION\s+(\d+)\.(\d+)\.(\d+)\.(\d+)\s*$")


# Module-level flag toggled by `faultycmd --ignore-version-mismatch`.
# Off by default so production CLI/TUI fail-closed on mismatch.
_allow_mismatch = False


def set_allow_mismatch(allow: bool) -> None:
    """Toggle the global override that bypasses the version check.

    Wired to the CLI's top-level ``--ignore-version-mismatch`` flag.
    Leaving this False (the default) is the safe production behaviour.
    """
    global _allow_mismatch
    _allow_mismatch = allow


def allow_mismatch() -> bool:
    return _allow_mismatch


class VersionMismatchError(RuntimeError):
    """Raised when the firmware version/board does not match the host's.

    Attributes:
        firmware: the 4-segment `(board, MAJOR, MINOR, PATCH)` the
            firmware reported, or ``None`` if the firmware predates
            the version embed (legacy 4-byte PING reply, shell with
            no `version` verb).
        host: the host's `faultycmd.__version__` at check time.
    """

    def __init__(
        self,
        firmware: FirmwareVersionTuple | None,
        host: str,
        *,
        hint: str = "",
    ) -> None:
        self.firmware = firmware
        self.host = host
        fw_str = ".".join(str(v) for v in firmware) if firmware else "<pre-versioning>"
        msg = (
            f"firmware/board mismatch: firmware={fw_str} (host v{host}). "
            "Re-flash the firmware built for this board, or pass "
            "--ignore-version-mismatch to bypass (unsafe — wire protocol "
            "may have shifted)."
        )
        if firmware is None:
            msg += (
                " This firmware build predates version embedding — likely a "
                "beta/dev build whose CI release-versioning step hasn't run "
                "yet. Functionality is not guaranteed against this host "
                "version; proceed with --ignore-version-mismatch only if you "
                "know what you're testing."
            )
        if hint:
            msg += f" [{hint}]"
        super().__init__(msg)


def host_version_tuple() -> VersionTuple:
    """Parse ``faultycmd.__version__`` into a ``(MAJOR, MINOR, PATCH)`` tuple.

    The host has no board segment — it's compared against the
    firmware's `MAJOR.MINOR.PATCH` portion only, separately from the
    board-id check (see :data:`EXPECTED_BOARD`).
    """
    m = _HOST_VERSION_RE.match(__version__)
    if not m:
        raise RuntimeError(
            f"faultycmd.__version__ is malformed: {__version__!r} "
            "(expected `MAJOR.MINOR.PATCH`)"
        )
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def parse_ping_version(payload: bytes) -> FirmwareVersionTuple:
    """Parse the PING reply payload into a ``(board, MAJOR, MINOR, PATCH)`` tuple.

    Layout (F11+ firmware): ``'F', family, A, MAJ, MIN, PATCH``.
    The legacy 4-byte reply (`'F', family, 0, 0`) trips the length
    check and surfaces as VersionMismatchError(firmware=None) so the
    host shows a meaningful "firmware too old" message instead of a
    cryptic 0.0.0.0 mismatch.
    """
    if len(payload) < 2 or payload[0:1] != b"F":
        raise VersionMismatchError(
            None, __version__, hint=f"unexpected ping reply: {payload!r}"
        )
    if len(payload) < 6:
        raise VersionMismatchError(
            None, __version__, hint="firmware predates the version-in-PING extension"
        )
    return payload[2], payload[3], payload[4], payload[5]


def parse_shell_version(line: str) -> FirmwareVersionTuple:
    """Parse the CDC2 `version` reply (``SHELL: VERSION A.X.Y.Z``)."""
    m = _SHELL_VERSION_RE.match(line.strip())
    if not m:
        raise VersionMismatchError(
            None, __version__, hint=f"bad shell version reply: {line!r}"
        )
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))


def assert_version_match(firmware: FirmwareVersionTuple) -> None:
    """Verify the firmware targets this host's expected board.

    Host and firmware version numbers (`MAJOR.MINOR.PATCH`) are
    released independently — only the board id `A` is required to
    match :data:`EXPECTED_BOARD`. No-op if the global override is on
    (see :func:`set_allow_mismatch`).
    """
    if _allow_mismatch:
        return
    board = firmware[0]
    if board != EXPECTED_BOARD:
        raise VersionMismatchError(
            firmware,
            __version__,
            hint=(
                f"firmware targets board {board}, this host expects board "
                f"{EXPECTED_BOARD}"
            ),
        )
