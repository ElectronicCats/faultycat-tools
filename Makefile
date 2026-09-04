# FaultyCat Makefile
# Simplified installation and management

PYTHON=python3
PIP=pip

.PHONY: install compile-install uninstall clean help

help:
	@echo "faultycat-tools Makefile"
	@echo "Usage:"
	@echo "  make install         Install faultycmd as a python package (pip install -e .)"
	@echo "  make compile-install Compile with PyInstaller and install the binary globally"
	@echo "  make uninstall       Remove faultycmd from the system"
	@echo "  make clean           Remove build artifacts"

install:
	@echo "[*] Installing faultycmd package..."
	$(PIP) install -e .

compile-install:
	@echo "[*] Compiling faultycmd with PyInstaller..."
	$(PIP) install pyinstaller
	pyinstaller faultycmd.spec
	@echo "[*] Installing compiled binary to /usr/local/bin (requires sudo)..."
	sudo cp dist/faultycmd/faultycmd /usr/local/bin/faultycmd
	@echo "[+] Compiled binary installed to /usr/local/bin/faultycmd"

uninstall:
	@echo "[*] Uninstalling faultycmd package..."
	$(PIP) uninstall -y faultycmd
	sudo rm -f /usr/local/bin/faultycmd

clean:
	rm -rf build/ dist/ *.egg-info/ pkg_root/
	find . -type d -name "__pycache__" -exec rm -rf {} +
