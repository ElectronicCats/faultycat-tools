"""RP2040 firmware update via GitHub Releases.

faultycmd pins an *exact* version match between host and firmware
(see :mod:`..utils.version_check`) — both are cut together and
attached to the same tag on the ``ElectronicCats/faultycat`` GitHub
repo (e.g. tag ``v3.0.0.0`` carries ``faultycat_v3.0.0.0.uf2``
alongside the ``faultycmd`` wheel/exe/tarball). So ``faultycmd
update`` doesn't chase "latest" — it fetches the UF2 for the tag
matching *this host's* ``__version__`` and flashes it, keeping the
pair in lockstep. If that tag has no published release yet (a dev
build ahead of the last cut release), there's nothing to flash and
the command says so.

Flashing rides the RP2040's UF2 mass-storage bootloader: once the
board is in boot mode it enumerates as a ``RPI-RP2`` USB drive, and
copying the ``.uf2`` file there triggers the update. There is no
shell verb to trigger that reboot remotely (unlike designs where an
RP2040 bridges to a separate target MCU), so entering boot mode is
always a manual, physical step here.
"""

from __future__ import annotations

import glob
import os
import platform
import shutil
import string
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from .. import __version__
from ..utils.output import (
    print_dim,
    print_error,
    print_info,
    print_success,
    print_warning,
)
from ..utils.version_check import host_version_tuple
from .usb import discover

GITHUB_RELEASES_API = (
    "https://api.github.com/repos/ElectronicCats/faultycat-firmware/releases"
)
_REQUEST_TIMEOUT_S = 10
_DOWNLOAD_TIMEOUT_S = 60


class FirmwareUpdateError(RuntimeError):
    """Unrecoverable update failure: network, missing asset, no boot device, ..."""


@dataclass(frozen=True)
class ReleaseAsset:
    tag: str
    uf2_name: str
    uf2_url: str


def _host_version_str() -> str:
    return ".".join(str(v) for v in host_version_tuple())


def get_latest_release_tag() -> str | None:
    """Best-effort lookup of the most recently published release tag.

    Informational only (lets the operator know if their host build is
    ahead of the last cut release) — None on any network failure.
    """
    try:
        resp = requests.get(f"{GITHUB_RELEASES_API}/latest", timeout=_REQUEST_TIMEOUT_S)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json().get("tag_name")
    except requests.exceptions.RequestException:
        return None


def get_release_for_tag(tag: str) -> ReleaseAsset | None:
    """Look up the .uf2 asset attached to a specific release tag.

    Returns None if that tag has no published release yet, or the
    release has no .uf2 asset attached.
    """
    try:
        resp = requests.get(
            f"{GITHUB_RELEASES_API}/tags/{tag}", timeout=_REQUEST_TIMEOUT_S
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise FirmwareUpdateError(f"could not reach GitHub: {e}") from e

    for asset in resp.json().get("assets", []):
        name = asset.get("name", "")
        if name.lower().endswith(".uf2"):
            return ReleaseAsset(
                tag=tag, uf2_name=name, uf2_url=asset["browser_download_url"]
            )
    return None


def cache_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    path = base / "faultycmd" / "firmware"
    path.mkdir(parents=True, exist_ok=True)
    return path


def download_uf2(asset: ReleaseAsset) -> Path:
    """Download (or reuse a cached copy of) the UF2 for ``asset``."""
    dest = cache_dir() / asset.uf2_name
    if dest.is_file():
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        resp = requests.get(asset.uf2_url, timeout=_DOWNLOAD_TIMEOUT_S, stream=True)
        resp.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
        tmp.replace(dest)
    except requests.exceptions.RequestException as e:
        tmp.unlink(missing_ok=True)
        raise FirmwareUpdateError(f"download failed: {e}") from e
    return dest


def find_rp2040_mount_point() -> str | None:
    """Find the RP2040 mass-storage mount point when in UF2 boot mode."""
    import platform

    system = platform.system()

    if system == "Linux":
        # Check common mount points
        search_paths = [
            "/media/*/RPI-RP2",
            "/run/media/*/RPI-RP2",
            "/mnt/RPI-RP2",
        ]
        for pattern in search_paths:
            matches = glob.glob(pattern)
            if matches:
                return matches[0]

    elif system == "Darwin":  # macOS
        mount_path = "/Volumes/RPI-RP2"
        if os.path.exists(mount_path):
            return mount_path

    elif system == "Windows":
        # Check all drive letters for RPI-RP2 volume
        import string

        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            try:
                if os.path.exists(drive):
                    # Check volume label
                    label_path = os.path.join(drive, "INFO_UF2.TXT")
                    if os.path.exists(label_path):
                        return drive
            except Exception:
                continue

    return None


def wait_for_boot_mode(
    timeout_s: float = 30.0, on_progress: Callable[[str], None] | None = None
) -> str:
    """Poll for the RPI-RP2 mount point, raising on timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        mount_point = find_rp2040_mount_point()
        if mount_point:
            return mount_point
        if on_progress:
            on_progress(
                f"waiting for boot mode... ({int(deadline - time.monotonic())}s left)"
            )
        time.sleep(1)
    raise FirmwareUpdateError("timed out waiting for the RPI-RP2 boot device to appear")


def flash_uf2(uf2_path: Path, mount_point: str) -> None:
    """Copy ``uf2_path`` onto the detected RPI-RP2 mass-storage device."""
    try:
        shutil.copy2(uf2_path, os.path.join(mount_point, uf2_path.name))
    except OSError as e:
        raise FirmwareUpdateError(f"could not copy UF2 to {mount_point}: {e}") from e


def get_connected_firmware_version() -> tuple[int, int, int, int] | None:
    """Best-effort probe of the connected board's firmware version.

    Tries the EMFI CDC's PING first (cheapest single round-trip),
    falls back to the scanner shell's ``version`` verb. Returns None
    if no FaultyCat CDC is detected, or neither probe succeeds.
    """
    ports = discover()
    if not ports:
        return None

    from ..protocols import EmfiClient, ScannerClient
    from ..utils.version_check import parse_ping_version, parse_shell_version

    emfi_port = next((p.device for p in ports if p.interface == 0x00), None)
    if emfi_port is not None:
        try:
            with EmfiClient(emfi_port, check_firmware_version=False) as cli:
                payload = cli.ping()
            return parse_ping_version(payload)
        except Exception:
            pass

    scanner_port = next((p.device for p in ports if p.interface == 0x04), None)
    if scanner_port is not None:
        try:
            with ScannerClient(scanner_port, check_firmware_version=False) as cli:
                line = cli.send_line("version", accept_prefixes=("SHELL:",))
            return parse_shell_version(line)
        except Exception:
            pass

    return None


def _print_boot_mode_instructions() -> None:
    print_warning("Put the FaultyCat into UF2 boot mode now.")
    print_dim("Consult your board's hardware docs for the exact button combo —")
    print_dim("once in boot mode it enumerates as a 'RPI-RP2' USB drive.")


def check_and_update_firmware(force: bool = False) -> bool:
    """Orchestrate the update: probe → resolve release → flash.

    Returns True if the firmware is already up-to-date or was
    successfully flashed, False otherwise.
    """
    host_str = _host_version_str()
    tag = f"v{host_str}"
    print_info(f"Host version: {host_str} (target tag: {tag})")

    latest_tag = get_latest_release_tag()
    if latest_tag and latest_tag != tag:
        print_dim(f"Latest published release: {latest_tag}")

    device_ver = get_connected_firmware_version()
    if device_ver is not None:
        device_str = ".".join(str(v) for v in device_ver)
        print_info(f"Connected firmware version: {device_str}")
        if device_ver == host_version_tuple():
            if not force:
                print_success("Firmware already matches this host version.")
                return True
            print_warning("Force re-flash requested — firmware already matches.")
        else:
            print_warning(f"Firmware mismatch: device={device_str} ≠ host={host_str}")
    else:
        print_warning("No FaultyCat device detected on its CDC interfaces.")

    try:
        asset = get_release_for_tag(tag)
    except FirmwareUpdateError as e:
        print_error(str(e))
        return False

    if asset is None:
        print_error(f"No UF2 published for tag {tag} yet.")
        print_dim(
            "This host build may be ahead of the last cut release "
            "(a dev/test build) — wait for the matching GitHub Release, "
            "or build/flash the firmware manually."
        )
        return False

    print_info(f"Resolved firmware asset: {asset.uf2_name}")
    try:
        uf2_path = download_uf2(asset)
    except FirmwareUpdateError as e:
        print_error(str(e))
        return False
    print_success(f"UF2 ready: {uf2_path}")

    mount_point = find_rp2040_mount_point()
    if not mount_point:
        _print_boot_mode_instructions()
        try:
            mount_point = wait_for_boot_mode(timeout_s=30.0, on_progress=print_dim)
        except FirmwareUpdateError as e:
            print_error(str(e))
            return False

    print_success(f"RP2040 boot device detected at: {mount_point}")
    try:
        flash_uf2(uf2_path, mount_point)
    except FirmwareUpdateError as e:
        print_error(str(e))
        return False

    print_success("Firmware flashed successfully!")
    print_dim(
        "The device will reboot automatically. Wait a few seconds before reconnecting."
    )
    return True
