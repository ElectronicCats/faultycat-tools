#!/bin/bash
set -e

# faultycmd is pure Python (pyserial/click/rich/textual) — no native
# libraries are required before running this script, unlike catnip
# which needs libusb/libmagic from Homebrew.

echo "[*] Installing Python dependencies..."
pip install -e .

echo "[*] Installing PyInstaller..."
pip install pyinstaller

echo "[*] Building faultycmd..."
pyinstaller faultycmd.spec

echo "[+] Verifying binary..."
test -f dist/faultycmd/faultycmd || { echo "[!] ERROR: dist/faultycmd/faultycmd not found"; exit 1; }
ls -lh dist/faultycmd/faultycmd

echo "[*] Creating macOS Package (.pkg)..."
PKG_ROOT="pkg_root"
INSTALL_LOCATION="/usr/local/opt/faultycmd"
BIN_DIR="/usr/local/bin"

mkdir -p "${PKG_ROOT}${INSTALL_LOCATION}"
mkdir -p "${PKG_ROOT}${BIN_DIR}"

cp -R dist/faultycmd "${PKG_ROOT}${INSTALL_LOCATION}/"
ln -sf "${INSTALL_LOCATION}/faultycmd/faultycmd" "${PKG_ROOT}${BIN_DIR}/faultycmd"

VERSION=$(cat VERSION | tr -d '[:space:]')
if [ -z "$VERSION" ]; then
  VERSION="1.0.1"
fi

IDENTIFIER="com.electroniccats.faultycat"

pkgbuild --root "${PKG_ROOT}" \
         --identifier "${IDENTIFIER}" \
         --version "${VERSION}" \
         --install-location "/" \
         "faultycmd-${VERSION}.pkg"

echo "[+] Build successful. Installer created: faultycmd-${VERSION}.pkg"
